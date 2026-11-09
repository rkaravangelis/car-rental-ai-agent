"""Guardrails: two layers that validate every LLM answer before it reaches the customer.

Layer 1 - Schema (Pydantic): correct fields, allowed actions, refund limit. No context needed.
Layer 2 - Business rules: checks that need the conversation (requested amount, retrieved pages).
A failure in either layer triggers one retry in the agent, then a safe fallback.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MAX_REFUND = 100  # hard limit per refund, in the currency the customer uses


# --- Layer 1: Schema ----------------------------------------------------------------------
class AgentLLMOutput(BaseModel):

    # 1. Reasoning: grounded in the retrieved policy passages
    rationale: str = Field(..., max_length=800, description="Reasoning based only on the retrieved policy passages.")

    # 2. Conclusion: answer, sources and action, derived from the rationale above
    answer: str = Field(..., min_length=1, max_length=400, description="Reply to the customer, max 2 sentences.")
    cited_pages: list[int] = Field(default_factory=list, description="Page numbers of the passages the answer is based on.")
    action: Literal["answer", "refund", "account", "cannot_help"] = Field(..., description="answer = inform only, refund = pay money back, account = show the customer's balance, cannot_help = no basis.")
    refund_amount: float | None = Field(None, gt=0, le=MAX_REFUND, description="Only for action 'refund': the amount to pay back.")

    @model_validator(mode="after")
    def refund_needs_amount(self):
        # A refund without an amount, or an amount without a refund, is always a mistake
        if self.action == "refund" and self.refund_amount is None:
            raise ValueError("action 'refund' requires a refund_amount")
        if self.action != "refund" and self.refund_amount is not None:
            raise ValueError("refund_amount is only allowed with action 'refund'")
        return self


class AgentOutput(AgentLLMOutput):
    region: str
    customer: str
    input_tokens: int
    output_tokens: int
    shadow_cost_usd: float
    refund_result: str | None = None
    token_breakdown: dict[str, int] = Field(default_factory=dict)  # reasoning / retrieval / answer


# --- Layer 2: Business rules --------------------------------------------------------------
class GuardrailViolation(Exception):
    pass


def requested_amount(question: str) -> float | None:
    # Amount the customer asked for, e.g. "$60", "60 €", "60 dollars". None if no amount.
    match = re.search(
        r"[$€]\s?(\d[\d,]*(?:\.\d{1,2})?)"
        r"|(\d[\d,]*(?:\.\d{1,2})?)\s?(?:€|\$|(?:eur|euros?|usd|dollars?)\b)",
        question,
        re.IGNORECASE,
    )
    if not match:
        return None
    return float((match.group(1) or match.group(2)).replace(",", ""))  # "1,000" -> 1000


def check_business_rules(
    result: AgentLLMOutput, requested: float | None, retrieved_pages: set[int]) -> None:
    if result.action == "refund":
        if requested is None:
            raise GuardrailViolation("Refund proposed, but the customer did not state an amount.")
        if requested > MAX_REFUND:
            raise GuardrailViolation(f"Requested {requested} exceeds the limit of {MAX_REFUND}.")
        if result.refund_amount > requested:
            raise GuardrailViolation(
                f"Refund {result.refund_amount} is more than the requested {requested}."
            )

    # Grounding: policy answers must cite pages, and only pages that were actually retrieved.
    # "account" comes from the ledger, not the policy, so it needs no citation.
    if result.action in ("answer", "refund"):
        if not result.cited_pages:
            raise GuardrailViolation("The answer does not cite any policy page.")
        unknown = set(result.cited_pages) - retrieved_pages
        if unknown:
            raise GuardrailViolation(f"Cited pages {sorted(unknown)} were not in the context.")
