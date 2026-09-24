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


# Facts from README.md, AGENTS.md, _docs/deployment-plan.md (step 6) and
# _docs/process.md. Update when those change.
PROJECT_CONTEXT = (
    "Course project for the AI Dev Tools Zoomcamp: a multi-user kanban board "
    "(FastAPI backend, React frontend) built with AI coding agents. Users "
    "share boards with others as viewers or editors. All six implementation "
    "stages are done. The app is deployed to AWS over public HTTPS. The app "
    "stack (EC2 + RDS) is ephemeral: created and destroyed together, most "
    "sessions under 2 hours and never longer than 3 days, and its Postgres "
    "data is lost on each teardown. The bootstrap resources (GitHub OIDC "
    "provider, the scoped IAM deploy role CI uses, the ECR repository) and "
    "local Docker Compose data persist. The backend runs a single uvicorn "
    "worker that serializes requests."
)

SCOPE_DESCRIPTIONS = {
    "change": "Review of one change (see `current_change`).",
    "repository": "Review of the whole repository, not tied to one change.",
}


def _current_change(repo: Path, base: str = "main") -> dict:
    """The change under review: branch commits and files changed vs base,
    including uncommitted work."""
    return {
        "commits": _git(repo, "log", "--format=%s", f"{base}..HEAD").splitlines(),
        "files_changed": _git(repo, "diff", "--stat", base).splitlines(),
    }


def _finding_state(item: dict, repo: Path) -> dict:
    scope = item.get("review_scope", "change")
    if scope not in SCOPE_DESCRIPTIONS:
        raise ValueError(f"review_scope must be one of {sorted(SCOPE_DESCRIPTIONS)}, got {scope!r}")
    state = {
        "project_context": PROJECT_CONTEXT,
        "review_scope": SCOPE_DESCRIPTIONS[scope],
        "finding": {k: item.get(k) for k in ("title", "body", "severity", "file", "line")},
    }
    if scope == "change":
        state["current_change"] = _current_change(repo)
    excerpt = _excerpt(repo, item.get("file"), item.get("line"))
    if excerpt:
        state["code_excerpt"] = excerpt
    return state


FINDING_TRIAGE = Template(
    name="finding_triage",
    version=3,
    instructions=(
        "A code review (scope in `review_scope`) produced the finding in "
        "`finding`. `project_context` describes the project; `code_excerpt`, "
        "if present, shows the referenced code with line numbers. How should "
        "the finding be handled? Each option's `consequence` states what "
        "happens next."
    ),
    criteria={
        "fix_now": {
            "what": "The defect and its impact are real, and fixing it now is proportionate: the impact is likely or severe in the setup described in `project_context`, or the fix is small relative to the impact.",
            "not_for": "Unsupported claims, problems whose fix is large relative to a limited or unlikely impact, or problems not worth fixing in this project.",
            "consequence": "The problem is fixed before the review is closed.",
        },
        "defer": {
            "what": "The defect and its impact are real and worth fixing eventually, but the fix is large relative to an impact that is limited or unlikely in the setup described in `project_context`.",
            "not_for": "Unsupported claims, problems with a likely or severe impact or a small fix, or problems not worth fixing in this project.",
            "consequence": "A GitHub issue records the problem and the reason for deferring; no code changes now.",
        },
        "accept": {
            "what": "The defect and its impact are real, but given `project_context` they do not justify fixing.",
            "not_for": "Unsupported claims, or problems worth fixing now or later.",
            "consequence": "No code change and no issue; the reason is recorded in the decision log only.",
        },
        "reject": {
            "what": "The alleged defect or its impact is unsupported by the code, or already resolved.",
            "not_for": "Findings whose defect and impact are real, whatever their size.",
            "consequence": "The finding is dropped.",
        },
        NONE_FIT: {
            "what": "None of the other options applies to this finding.",
            "not_for": "Findings that one of the other options covers.",
            "consequence": "The decision goes back to the agent.",
        },
    },
    build_state=_finding_state,
    tier=lambda item: "B" if str(item.get("severity", "")).lower() in {"high", "critical"} else "A",
)

TEMPLATES = {t.name: t for t in (FINDING_TRIAGE,)}
