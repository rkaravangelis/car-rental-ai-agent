"""Frontend: chat with the agent, compare with the golden answers, see what it did and what it cost.

This file is only the user interface. Talking to the agent's API happens in api_client.py,
the look and feel lives in style.css.
Run: streamlit run frontend.py   ->   http://localhost:8501
"""

import csv
from pathlib import Path

import streamlit as st
from api_client import AgentUnavailable, ask  # our own file: the connection to the agent's API

HERE = Path(__file__).parent
GOLDEN_CSV = HERE.parent / "00_assets" / "evaluation" / "golden_answers.csv"

st.set_page_config(page_title="AI Customer Service Agent", page_icon="🚗", layout="wide")
st.markdown(f"<style>{(HERE / 'style.css').read_text()}</style>", unsafe_allow_html=True)


# --- Data -----------------------------------------------------------------------------------
@st.cache_data
def load_golden() -> dict[str, str]:
    # Golden answers: question -> expected answer
    with open(GOLDEN_CSV, encoding="utf-8") as f:
        return {row["question"]: row["answer"] for row in csv.DictReader(f)}


# --- Building blocks ------------------------------------------------------------------------
def stop(i: int, label: str, value: str, state: str = "") -> str:
    # One stop on the route; --i sets its place in the drawing animation
    return f'<div class="stop {state}" style="--i:{i}"><div class="k">{label}</div><div class="v">{value}</div></div>'


def show_route(result: dict, fresh: bool) -> None:
    # The agent's path: Region -> Policy -> Checks -> Outcome
    pages = result["cited_pages"]
    policy = f"p. {', '.join(map(str, pages))}" if pages else "Searched"  # retrieval always runs
    blocked = result["action"] == "cannot_help" and result["rationale"].startswith("Guardrail")
    if result["action"] == "refund":
        booked = (result["refund_result"] or "").startswith("Refund of")  # set by the MCP server
        outcome = (f"Refund {result['refund_amount']:g}", "") if booked else ("Not booked", "block")
    elif result["action"] == "answer":
        outcome = ("Answered", "")
    elif result["action"] == "account":
        outcome = ("Account", "")
    else:
        outcome = ("Not covered", "idle")

    stops = [
        stop(0, "Region", result["region"]),
        stop(1, "Policy", policy, "" if pages else "idle"),
        stop(2, "Checks", "Blocked" if blocked else "Passed", "block" if blocked else "pass"),
        stop(3, "Outcome", *outcome),
    ]
    st.markdown(
        f'<div class="route {"fresh" if fresh else ""}">{"".join(stops)}</div>'
        f'<div class="readout"><span><b>{result["input_tokens"]:,}</b> tokens in</span>'
        f"<span><b>{result['output_tokens']:,}</b> tokens out</span>"
        f"<span><b>${result['shadow_cost_usd']:.6f}</b> at market price</span></div>"
        f"{token_panel(result.get('token_breakdown') or {})}",
        unsafe_allow_html=True,
    )


def token_panel(parts: dict) -> str:
    # Collapsible "where the tokens go": one stacked bar + legend
    total = sum(parts.values())
    if not total:
        return ""
    rows = [
        ("reasoning", "Reasoning", "≈", "hidden thinking of the model (estimated)"),
        ("retrieval", "Retrieval", "", "HyDE passage + policy search"),
        ("answer", "Answer", "", "prompt, policy chunks and reply"),
    ]
    bar = "".join(
        f'<span class="seg {key}" style="width:{parts.get(key, 0) / total * 100:.1f}%"></span>'
        for key, *_ in rows
    )
    legend = "".join(
        f'<li><i class="dot {key}"></i>{label}<b>{approx}{parts.get(key, 0):,}</b>{note}</li>'
        for key, label, approx, note in rows
    )
    return (
        f'<details class="usage"><summary>Where the tokens go</summary>'
        f'<div class="bar">{bar}</div><ul>{legend}</ul></details>'
    )


AVATARS = {"user": "👤", "assistant": "🚗"}


def show_message(message: dict, fresh: bool = False) -> None:
    with st.chat_message(message["role"], avatar=AVATARS[message["role"]]):
        st.write(message["content"])
        if "result" in message:
            show_route(message["result"], fresh)
        if message.get("expected"):
            st.markdown(
                f'<div class="expected"><b>Expected answer</b><br>{message["expected"]}</div>',
                unsafe_allow_html=True,
            )


# --- Sidebar: golden questions, always visible ----------------------------------------------
golden = load_golden()

with st.sidebar:
    st.subheader("Golden questions")
    st.caption("Questions with a known answer from the policy. Click one to ask the agent.")
    for question, expected in golden.items():
        if st.button(question, use_container_width=True):
            st.session_state.pending = question
        with st.expander("Expected answer"):
            st.write(expected)


# --- Main: header + chat --------------------------------------------------------------------
st.markdown(
    '<div class="title">AI Customer Service Agent</div>'
    '<div class="subtitle">Answers come only from the rental policies for Arizona and Germany, '
    "and every answer is checked before you see it.</div>",
    unsafe_allow_html=True,
)

if "messages" not in st.session_state:
    st.session_state.messages = []

# A question comes either from the chat input or from a clicked golden question
question = st.chat_input("Describe your issue") or st.session_state.pop("pending", None)

if not st.session_state.messages and not question:  # hint only on an empty chat
    st.markdown(
        '<div class="empty">Ask about deposits, fuel, early returns or refunds, '
        "or pick a golden question on the left.</div>",
        unsafe_allow_html=True,
    )

for message in st.session_state.messages:
    show_message(message)

if question:
    user_message = {"role": "user", "content": question}
    show_message(user_message)

    with st.spinner("Checking the policy..."):
        try:
            result = ask(question)
        except AgentUnavailable as error:  # API not reachable, timeout or server error
            st.error(f"The agent could not answer: {error}")
            st.stop()

    agent_message = {
        "role": "assistant",
        "content": result["answer"],
        "result": result,
        "expected": golden.get(question),  # shown only for golden questions
    }
    st.session_state.messages += [user_message, agent_message]
    show_message(agent_message, fresh=True)  # only the newest route draws itself
