import random

from jev.flow import MAX_JEV_REQUESTS, Answer, run
from jev.templates import FINDING_TRIAGE, NONE_FIT

LOW_ITEM = {"severity": "low"}
HIGH_ITEM = {"severity": "high"}


def answer(choice="fix_now", confidence=0.95, ctx=0.9, none_fit=0.0):
    rest = 1.0 - none_fit
    return Answer(choice, confidence, {choice: rest, NONE_FIT: none_fit}, ctx)


def scripted(*answers):
    calls = []

    def ask(state, instructions, criteria):
        calls.append({"state": state, "criteria": criteria})
        a = answers[len(calls) - 1]
        if isinstance(a, Exception):
            raise a
        return a
    return ask, calls


def test_accepts_confident_answer():
    ask, calls = scripted(answer())
    r = run(FINDING_TRIAGE, LOW_ITEM, {}, ask, check=None)
    assert (r.outcome, r.answer, r.tier) == ("accept", "fix_now", "A")
    assert len(calls) == 1


def test_tier_b_needs_higher_confidence():
    ask, _ = scripted(answer(confidence=0.7))
    r = run(FINDING_TRIAGE, HIGH_ITEM, {}, ask, check=None, prior_answer="defer")
    assert (r.outcome, r.reason, r.decider) == ("fallback", "low_confidence", "human")


def test_tier_b_fallback_stays_with_claude_when_prior_agrees():
    ask, _ = scripted(answer(choice="fix_now", confidence=0.7))
    r = run(FINDING_TRIAGE, HIGH_ITEM, {}, ask, check=None, prior_answer="fix_now")
    assert (r.outcome, r.decider) == ("fallback", "claude")


def test_tier_b_errors_fall_back_to_claude():
    ask, _ = scripted(RuntimeError("boom"), RuntimeError("boom"))
    r = run(FINDING_TRIAGE, HIGH_ITEM, {}, ask, check=None, prior_answer="fix_now")
    assert (r.reason, r.decider) == ("error", "claude")


def test_context_gate_runs_before_confidence():
    ask, _ = scripted(answer(confidence=0.99, ctx=0.2))
    r = run(FINDING_TRIAGE, LOW_ITEM, {}, ask, check=None)
    assert (r.outcome, r.reason, r.decider) == ("fallback", "insufficient_context", "claude")


def test_none_fit_falls_back_without_codex():
    ask, _ = scripted(answer(choice="defer", confidence=0.99, none_fit=0.4))
    checked = []
    r = run(FINDING_TRIAGE, LOW_ITEM, {}, ask, check=lambda t, s: checked.append(1) or ["x"])
    assert (r.outcome, r.reason) == ("fallback", "none_fit")
    assert checked == []


def test_codex_facts_added_then_reask():
    ask, calls = scripted(answer(confidence=0.4), answer(confidence=0.9))
    r = run(FINDING_TRIAGE, LOW_ITEM, {"finding": {}}, ask, check=lambda t, s: ["fact (a.py:1)"])
    assert (r.outcome, r.answer) == ("accept", "fix_now")
    assert calls[1]["state"]["additional_facts"] == ["fact (a.py:1)"]


def test_codex_called_at_most_once_and_budget_holds():
    ask, calls = scripted(*[answer(confidence=0.4)] * 5)
    checks = []
    r = run(FINDING_TRIAGE, LOW_ITEM, {}, ask, check=lambda t, s: checks.append(1) or ["f"])
    assert r.outcome == "fallback"
    assert len(checks) == 1
    assert len(calls) <= MAX_JEV_REQUESTS


def test_no_codex_facts_falls_back():
    ask, calls = scripted(answer(confidence=0.4))
    r = run(FINDING_TRIAGE, LOW_ITEM, {}, ask, check=lambda t, s: [])
    assert (r.outcome, r.reason) == ("fallback", "low_confidence")
    assert len(calls) == 1


def test_one_error_retry_then_fallback():
    ask, calls = scripted(RuntimeError("boom"), RuntimeError("boom"))
    r = run(FINDING_TRIAGE, LOW_ITEM, {}, ask, check=None)
    assert (r.outcome, r.reason) == ("fallback", "error")
    assert len(calls) == 2


def test_error_retry_then_success():
    ask, _ = scripted(RuntimeError("boom"), answer())
    assert run(FINDING_TRIAGE, LOW_ITEM, {}, ask, check=None).outcome == "accept"


def test_option_order_is_shuffled():
    ask, calls = scripted(*[answer()] * 20)
    orders = set()
    for seed in range(20):
        calls.clear()
        run(FINDING_TRIAGE, LOW_ITEM, {}, ask, check=None, rng=random.Random(seed))
        orders.add(tuple(calls[0]["criteria"]))
    assert len(orders) > 1
