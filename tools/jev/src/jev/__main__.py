"""CLI: python -m jev <request.json | ->

Request JSON:
  {"template": "finding_triage",
   "item": {...template-specific fields...},
   "prior": {"answer": "<option>", "reason": "<one line>"},
   "facts": ["<fact> (<file>:<line>)", ...]}      # optional

The prior is logged, never sent to Jev or Codex. Prints the result as
JSON and appends one line to the decision log.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from typesafe_sdk import Choice, Noul, TypeSafeClient

from .flow import Answer, run
from .templates import TEMPLATES, Template, context_question

JEV_MODEL = "jev-1.13.0"
LOG_PATH = Path(os.environ.get(
    "MKB_DECISION_LOG",
    Path.home() / ".local/state/mini-kanban-board/decisions.jsonl",
))

CODEX_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {"type": "string"}},
        "template_defect": {"type": ["string", "null"]},
    },
    "required": ["facts", "template_defect"],
    "additionalProperties": False,
}


def repo_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def make_ask(client: TypeSafeClient):
    def ask(state: dict, instructions: str, criteria: dict) -> Answer:
        resp = client.system_one(
            state=state,
            questions={
                "decision": Choice(instructions=instructions, criteria=criteria),
                "context_sufficient": Noul(instructions=context_question(instructions, criteria)),
            },
        )
        d, c = resp.answers["decision"], resp.answers["context_sufficient"]
        return Answer(d.choice, d.confidence, dict(d.probabilities), c.noul)
    return ask


def codex_check(repo: Path):
    """Ask Codex for facts missing from the state. Does not see the prior."""
    def check(template: Template, state: dict) -> list[str]:
        prompt = (
            "A classifier must answer the question below from the given state, "
            "and was not confident. Look in this repository for concrete facts that "
            "are missing from the state and bear on the question. Do not recommend "
            "an option. Return each fact as one sentence ending with its source as "
            "(path:line). Return at most 5 facts; return none if nothing relevant is "
            "missing. If the question or options themselves are ambiguous or favor "
            "an option, describe that in template_defect.\n\n"
            + json.dumps({"question": template.instructions, "options": template.criteria,
                          "state": state}, indent=2)
        )
        with tempfile.TemporaryDirectory() as tmp:
            schema, out = Path(tmp, "schema.json"), Path(tmp, "out.json")
            schema.write_text(json.dumps(CODEX_SCHEMA))
            try:
                proc = subprocess.run(
                    ["codex", "exec", "--sandbox", "read-only", "--ephemeral", "-C", str(repo),
                     "--output-schema", str(schema), "-o", str(out), "-"],
                    input=prompt, capture_output=True, text=True, timeout=600,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"jev: codex check failed: {exc!r}", file=sys.stderr)
                return []
            if proc.returncode != 0 or not out.exists():
                print(f"jev: codex check failed: {proc.stderr[-500:]}", file=sys.stderr)
                return []
            data = json.loads(out.read_text())
        if data.get("template_defect"):
            print(f"jev: codex reports template defect: {data['template_defect']}", file=sys.stderr)
        return [str(f) for f in data.get("facts", [])][:5]
    return check


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    raw = sys.stdin.read() if sys.argv[1] == "-" else Path(sys.argv[1]).read_text()
    req = json.loads(raw)
    template = TEMPLATES[req["template"]]
    item, prior = req["item"], req.get("prior") or {}
    repo = repo_root()

    state = template.build_state(item, repo)
    if req.get("facts"):
        state["additional_facts"] = list(req["facts"])

    started = time.perf_counter()
    with TypeSafeClient(model=JEV_MODEL) as client:
        result = run(template, item, state, make_ask(client), codex_check(repo),
                     prior_answer=prior.get("answer"))
    elapsed = round(time.perf_counter() - started, 3)

    out = asdict(result)
    out["prior_agrees"] = (result.answer == prior.get("answer")) if result.answer else None
    print(json.dumps(out, indent=2))

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "template": template.name, "template_version": template.version,
            "jev_model": JEV_MODEL, "item": item, "prior": prior,
            "facts_from_claude": req.get("facts", []), "seconds": elapsed, **out,
        }) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
