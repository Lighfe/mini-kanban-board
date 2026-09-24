"""Question templates: one per decision type, versioned.

A template fixes the question wording, the options and their criteria,
how state is built from the item, and how the tier is set. Claude only
supplies the item and its prior.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

NONE_FIT = "none_fit"

def context_question(instructions: str, criteria: dict) -> str:
    """Questions in one request don't see each other, so this one restates
    the decision question and its options."""
    options = "; ".join(f"{k}: {v['what']}" for k, v in criteria.items() if k != NONE_FIT)
    return (
        f"Decision question: {instructions} Options: {options}. "
        "Does the state contain enough information to choose between these options?"
    )


@dataclass(frozen=True)
class Template:
    name: str
    version: int
    instructions: str
    criteria: dict[str, dict[str, str]]
    build_state: Callable[[dict, Path], dict]
    tier: Callable[[dict], str]


def _excerpt(repo: Path, file: str | None, line: int | None, radius: int = 15) -> str | None:
    if not file:
        return None
    path = (repo / file).resolve()
    if not path.is_relative_to(repo.resolve()) or not path.is_file():
        return None
    lines = path.read_text(errors="replace").splitlines()
    center = (line or 1) - 1
    start, end = max(0, center - radius), min(len(lines), center + radius + 1)
    return "\n".join(f"{i + 1}: {lines[i]}" for i in range(start, end))


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def _current_change(repo: Path, base: str = "main") -> dict:
    """The change under review: branch commits and files changed vs base,
    including uncommitted work."""
    return {
        "commits": _git(repo, "log", "--format=%s", f"{base}..HEAD").splitlines(),
        "files_changed": _git(repo, "diff", "--stat", base).splitlines(),
    }


def _finding_state(item: dict, repo: Path) -> dict:
    state = {
        "current_change": _current_change(repo),
        "finding": {k: item.get(k) for k in ("title", "body", "severity", "file", "line")},
    }
    excerpt = _excerpt(repo, item.get("file"), item.get("line"))
    if excerpt:
        state["code_excerpt"] = excerpt
    return state


FINDING_TRIAGE = Template(
    name="finding_triage",
    version=1,
    instructions=(
        "A code review of the change in `current_change` produced the finding "
        "in `finding`. `code_excerpt`, if present, shows the referenced code "
        "with line numbers. How should the finding be handled?"
    ),
    criteria={
        "fix_now": {
            "what": "The finding describes a real problem in the current change, and fixing it belongs in this change.",
            "not_for": "Problems outside the scope of the current change, or findings that are not real problems.",
        },
        "defer": {
            "what": "The finding describes a real problem, and fixing it belongs in a separate later change.",
            "not_for": "Findings that are not real problems, or problems whose fix belongs in the current change.",
        },
        "reject": {
            "what": "The finding does not describe a real problem: it is incorrect, already handled, or contradicts the documented design.",
            "not_for": "Findings that describe a real problem, whatever its size.",
        },
        NONE_FIT: {
            "what": "None of the other options applies to this finding.",
            "not_for": "Findings that one of the other options covers.",
        },
    },
    build_state=_finding_state,
    tier=lambda item: "B" if str(item.get("severity", "")).lower() in {"high", "critical"} else "A",
)

TEMPLATES = {t.name: t for t in (FINDING_TRIAGE,)}
