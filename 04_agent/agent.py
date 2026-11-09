"""Customer service agent: one LangGraph flow from question to validated answer.

    START -> route_region -> retrieve -> answer -> (refund) -> END

The LLM only proposes an action. Guardrails validate it, and the graph, not the LLM,
executes the refund via MCP. The LLM never touches money directly.
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import TypedDict

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph

ROOT = Path(__file__).parent.parent
sys.path.append(str(ROOT / "03_rag"))  # numbered folders are not packages, so add RAG to the path

from guardrails import (
    AgentLLMOutput,
    AgentOutput,
    GuardrailViolation,
    check_business_rules,
    requested_amount,
)
from prompts import SYSTEM_PROMPT, build_user_prompt, retry_prompt
from retrieve import format_context, list_regions, retrieve

# --- Variables ----------------------------------------------------------------------------
MODEL = "gpt-oss:120b-cloud"
CUSTOMER = "Alex Miller"  # the customer in this MVP is always Alex
DEFAULT_REGION = "General"  # used when the question names no known region
MAX_RETRIES = 1  # one correction attempt after a guardrail failure
PRICE_PER_1M = {"input": 0.037, "output": 0.17}  # USD, market price gpt-oss-120b (shadow cost)
MCP_SERVER = ROOT / "02_mcp_server" / "mcp_server.py"

FALLBACK = AgentLLMOutput(
    rationale="",
    answer="I'm sorry, I can't help with this request. Please contact our customer support.",
    action="cannot_help",
)


def split_output(output_tokens: int, visible_text: str) -> tuple[int, int]:
    # Ollama reports only the total output tokens. The visible text is estimated with
    # ~4 characters per token; the rest is the model's hidden reasoning. -> (visible, reasoning)
    visible = min(len(visible_text) // 4, output_tokens)
    return visible, output_tokens - visible


# --- State: what flows from node to node --------------------------------------------------
class AgentState(TypedDict, total=False):
    question: str
    region: str
    chunks: list[dict]
    result: AgentLLMOutput
    tokens: dict
    usage: dict  # where the tokens go: reasoning / retrieval / answer
    refund_result: str | None


# --- Agent --------------------------------------------------------------------------------
class CustomerServiceAgent:
    def __init__(self, llm=None):
        self.llm = llm or ChatOllama(
            model=MODEL,
            temperature=0.0,
            reasoning=False,
            num_ctx=20000,  # context window
            num_predict=1500,  # output cap
            keep_alive=-1,
            repeat_penalty=1.05,
        )
        # include_raw=True keeps the raw message, which carries the token usage
        self.structured_llm = self.llm.with_structured_output(AgentLLMOutput, include_raw=True)
        self.regions = list_regions()
        # MCP passes only a few safe variables to the server: hand over just the database credentials
        db_env = {key: value for key, value in os.environ.items() if key.startswith("POSTGRES_")}
        self.mcp = MultiServerMCPClient({"ledger": {"command": sys.executable, "args": [str(MCP_SERVER)], "transport": "stdio", "env": db_env}})
        self.graph = self._build_graph()

# --- Workflow --------------------------------------------------------------------------------
    # 1. Region: scan the question for a known region, otherwise General
    def route_region(self, state: AgentState) -> AgentState:
        question = state["question"]
        found = [r for r in self.regions if re.search(rf"\b{re.escape(r)}\b", question, re.IGNORECASE)]
        return {"region": found[0] if found else DEFAULT_REGION}

    # 2. Retrieval: HyDE search in the region's policy
    def retrieve(self, state: AgentState) -> AgentState:
        hits = retrieve(state["question"], state["region"], self.llm)
        visible, reasoning = split_output(hits["tokens"]["output"], hits["hyde"])
        usage = {"reasoning": reasoning, "retrieval": hits["tokens"]["input"] + visible, "answer": 0}
        return {"chunks": hits["chunks"], "tokens": hits["tokens"], "usage": usage}

    # 3. Answer: LLM proposes, guardrails validate, one retry, then safe fallback
    def answer(self, state: AgentState) -> AgentState:
        tokens = dict(state["tokens"])
        usage_split = dict(state["usage"])
        pages = {c["page"] for c in state["chunks"]}
        user_prompt = build_user_prompt(
            state["question"], state["region"], format_context(state["chunks"])
        )
        error = None

        for _ in range(MAX_RETRIES + 1):  # first try + retries
            prompt = user_prompt if error is None else retry_prompt(user_prompt, error)
            out = self.structured_llm.invoke([("system", SYSTEM_PROMPT), ("user", prompt)])

            usage = out["raw"].usage_metadata or {}  # every attempt counts towards the cost
            tokens["input"] += usage.get("input_tokens", 0)
            tokens["output"] += usage.get("output_tokens", 0)
            visible, reasoning = split_output(usage.get("output_tokens", 0), out["raw"].content or "")
            usage_split["answer"] += usage.get("input_tokens", 0) + visible
            usage_split["reasoning"] += reasoning

            try:
                if out["parsing_error"]:  # layer 1 failed: invalid fields or refund > limit
                    raise GuardrailViolation(out["parsing_error"])
                check_business_rules(out["parsed"], requested_amount(state["question"]), pages)
                return {"result": out["parsed"], "tokens": tokens, "usage": usage_split}  # both layers passed
            except GuardrailViolation as e:
                error = e

        fallback = FALLBACK.model_copy(update={"rationale": f"Guardrail: {error}"[:800]})
        return {"result": fallback, "tokens": tokens, "usage": usage_split}

    # 4. Refund: executed by the graph via MCP, only after the guardrails passed
    def refund(self, state: AgentState) -> AgentState:
        result = state["result"]
        args = {"amount": result.refund_amount, "reason": state["question"][:200]}
        try:
            outcome = json.loads(asyncio.run(self._call_mcp("create_refund", args)))
        except Exception as error:  # noqa: BLE001  any MCP failure -> nothing was booked
            outcome = {"booked": False, "message": f"Refund not booked: {type(error).__name__}"}

        # The answer is written here, not by the LLM: the balance comes from the ledger
        if outcome["booked"]:
            answer = f"I have executed the transaction for you. Your new balance is: €{outcome['new_balance']:,.2f}"
        else:
            answer = "I couldn't execute the refund right now. Please contact our customer support."
        return {"result": result.model_copy(update={"answer": answer}), "refund_result": outcome["message"]}

    # 5. Account: read the customer's balance from the ledger via MCP
    def account(self, state: AgentState) -> AgentState:
        try:
            info = json.loads(asyncio.run(self._call_mcp("get_account", {})))
            answer = f"Your current balance is €{info['balance']:,.2f}. Refunded so far: €{info['refunded']:,.2f}."
        except Exception:  # noqa: BLE001  any MCP failure -> say so instead of guessing
            answer = "I can't look up your account right now. Please contact our customer support."
        # Like the refund: the numbers come from the ledger, never from the LLM
        return {"result": state["result"].model_copy(update={"answer": answer})}

    async def _call_mcp(self, tool_name: str, args: dict) -> str:
        tools = {tool.name: tool for tool in await self.mcp.get_tools()}
        result = await tools[tool_name].ainvoke(args)
        # MCP returns content blocks, e.g. [{"type": "text", "text": "..."}] -> plain text
        if isinstance(result, list):
            return " ".join(block.get("text", "") for block in result if isinstance(block, dict))
        return str(result)

    # --- Graph ----------------------------------------------------------------------------
    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("route_region", self.route_region)
        graph.add_node("retrieve", self.retrieve)
        graph.add_node("answer", self.answer)
        graph.add_node("refund", self.refund)
        graph.add_node("account", self.account)

        graph.add_edge(START, "route_region")
        graph.add_edge("route_region", "retrieve")
        graph.add_edge("retrieve", "answer")
        graph.add_conditional_edges(  # refund and account need a tool; everything else ends here
            "answer", lambda s: s["result"].action if s["result"].action in ("refund", "account") else END
        )
        graph.add_edge("refund", END)
        graph.add_edge("account", END)
        return graph.compile()

    # --- Entry point for API, notebook and tests ------------------------------------------
    def run(self, question: str) -> AgentOutput:
        state = self.graph.invoke({"question": question})
        tokens = state["tokens"]
        cost = (tokens["input"] * PRICE_PER_1M["input"] + tokens["output"] * PRICE_PER_1M["output"]) / 1_000_000
        return AgentOutput(
            **state["result"].model_dump(),
            region=state["region"],
            customer=CUSTOMER,
            input_tokens=tokens["input"],
            output_tokens=tokens["output"],
            shadow_cost_usd=round(cost, 6),
            refund_result=state.get("refund_result"),
            token_breakdown=state["usage"],
        )


# --- Agent executor: python 04_agent/agent.py ---------------------------------------------
if __name__ == "__main__":
    agent = CustomerServiceAgent()
    # Ask any question: python 04_agent/agent.py "Arizona: How much deposit is held on my card?"
    question = " ".join(sys.argv[1:]) or "Arizona: How much deposit is held on my card?"
    result = agent.run(question)
    print(f"""
Customer Service Report
=======================
Region:     {result.region}
Action:     {result.action}  (refund: {result.refund_amount})
Answer:     {result.answer}
Pages:      {result.cited_pages}
Rationale:  {result.rationale}
Refund:     {result.refund_result}
Tokens:     {result.input_tokens} in / {result.output_tokens} out
Cost:       ${result.shadow_cost_usd}
""")
