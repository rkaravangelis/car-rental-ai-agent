"""Guardrail tests. Run with `pytest`: no LLM, no database, no Ollama needed."""

import pytest
from guardrails import AgentLLMOutput, GuardrailViolation, check_business_rules, requested_amount
from pydantic import ValidationError


def output(**changes) -> AgentLLMOutput:
    # A valid policy answer. Each test changes one field to break exactly one rule.
    valid = {"rationale": "Section 11.", "answer": "Up to $5,000.", "cited_pages": [11], "action": "answer"}
    return AgentLLMOutput(**{**valid, **changes})


def test_valid_answer_passes():
    check_business_rules(output(), requested=None, retrieved_pages={11})  # no exception = pass


def test_refund_over_100_is_rejected():
    with pytest.raises(ValidationError):  # layer 1: the schema itself refuses it
        output(action="refund", refund_amount=150)


def test_refund_above_requested_amount_is_blocked():
    with pytest.raises(GuardrailViolation):  # layer 2: never pay more than the customer asked
        check_business_rules(output(action="refund", refund_amount=60), requested=40, retrieved_pages={11})


def test_invented_page_is_blocked():
    with pytest.raises(GuardrailViolation):  # grounding: page 99 was never retrieved
        check_business_rules(output(cited_pages=[99]), requested=None, retrieved_pages={11})


def test_empty_answer_is_rejected():
    with pytest.raises(ValidationError):
        output(answer="")


@pytest.mark.parametrize(
    ("text", "amount"),
    [("$60", 60), ("60 €", 60), ("$1,000", 1000), ("my 2-month rental", None)],
)
def test_requested_amount_is_read_from_the_question(text, amount):
    assert requested_amount(text) == amount
