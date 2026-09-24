"""Decision flow: ask Jev, apply gates in order, escalate within a budget.

See _docs/decision-delegation.md.
"""

import random
import time
from dataclasses import dataclass, field
from typing import Callable

from .templates import NONE_FIT, Template

THRESHOLDS = {"A": 0.6, "B": 0.85}
# Provisional: on five probe findings (2026-09-24) context_sufficient was
# 0.47-0.56, except 0.26 for the one with no code attached.
CONTEXT_THRESHOLD = 0.4
NONE_FIT_THRESHOLD = 0.3
MAX_JEV_REQUESTS = 3


@dataclass
class Answer:
    choice: str
    confidence: float
    probabilities: dict[str, float]
    context_sufficient: float


# ask(state, instructions, criteria) -> Answer; raises on API/validation errors.
AskFn = Callable[[dict, str, dict], Answer]
# check(template, state) -> list of fact strings (each with a source).
CheckFn = Callable[[Template, dict], list[str]]


@dataclass
class Result:
    outcome: str  # "accept" or "fallback"
    tier: str
    answer: str | None = None
    reason: str | None = None
    decider: str | None = None  # on fallback: "claude" or "human", see _fallback_decider
    attempts: list[dict] = field(default_factory=list)
    codex_facts: list[str] = field(default_factory=list)


def _gate(ans: Answer, tier: str) -> str | None:
    """Return the name of the first failing gate, or None to accept."""
    if ans.context_sufficient < CONTEXT_THRESHOLD:
        return "insufficient_context"
    if ans.probabilities.get(NONE_FIT, 0.0) >= NONE_FIT_THRESHOLD:
        return "none_fit"
    if ans.confidence < THRESHOLDS[tier]:
        return "low_confidence"
    return None


def _fallback_decider(tier: str, attempts: list[dict], prior_answer: str | None) -> str:
    """Tier A: Claude. Tier B: the human only if Jev's last top answer
    disagrees with Claude's prior; otherwise Claude."""
    if tier == "A":
        return "claude"
    choices = [a["choice"] for a in attempts if "choice" in a]
    if not choices or choices[-1] == prior_answer:
        return "claude"
    return "human"


def run(template: Template, item: dict, state: dict, ask: AskFn, check: CheckFn | None,
        prior_answer: str | None = None, rng: random.Random | None = None) -> Result:
    rng = rng or random.Random()
    tier = template.tier(item)
    result = Result(outcome="fallback", tier=tier)
    codex_used = False
    retried_error = False

    while len(result.attempts) < MAX_JEV_REQUESTS:
        options = list(template.criteria.items())
        rng.shuffle(options)
        started = time.perf_counter()
        try:
            ans = ask(state, template.instructions, dict(options))
        except Exception as exc:  # API or validation error
            result.attempts.append({"error": repr(exc), "seconds": round(time.perf_counter() - started, 3)})
            if retried_error:
                result.reason = "error"
                break
            retried_error = True
            continue

        failed = _gate(ans, tier)
        result.attempts.append({
            "choice": ans.choice,
            "confidence": ans.confidence,
            "probabilities": ans.probabilities,
            "context_sufficient": ans.context_sufficient,
            "gate": failed or "accept",
            "seconds": round(time.perf_counter() - started, 3),
        })
        if failed is None:
            result.outcome, result.answer = "accept", ans.choice
            return result

        result.reason = failed
        # none_fit means the template's options are wrong; more evidence won't fix that.
        if failed == "none_fit" or codex_used or check is None:
            break
        codex_used = True
        facts = check(template, state)
        result.codex_facts = facts
        if not facts:
            break
        state = {**state, "additional_facts": state.get("additional_facts", []) + facts}

    result.decider = _fallback_decider(tier, result.attempts, prior_answer)
    return result
