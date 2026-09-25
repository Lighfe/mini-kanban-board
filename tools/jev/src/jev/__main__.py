"""CLI: python -m jev <request.json | ->

Asks TypeSafe's Jev model one request and prints the response as JSON.

Request JSON:
  {"state": <string | object | array>,
   "questions": {
     "<id>": {"type": "choice", "instructions": "...", "criteria": {"<option>": "<description>", ...}},
     "<id>": {"type": "noul", "instructions": "..."},
     "<id>": {"type": "score", "instructions": "...", "criteria": ["<lowest level>", ..., "<highest level>"]}},
   "model": "jev-latest"}                       # optional

Questions in one request run in parallel and don't see each other's
answers. Question ids are not sent to the model.
"""

import json
import sys
from pathlib import Path

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

DEFAULT_MODEL = "jev-latest"
QUESTION_TYPES = {"choice": Choice, "noul": Noul, "score": Score}


def build_questions(spec: dict) -> dict:
    questions = {}
    for qid, q in spec.items():
        kind = q.get("type")
        if kind not in QUESTION_TYPES:
            raise ValueError(f"question {qid!r}: type must be one of {sorted(QUESTION_TYPES)}, got {kind!r}")
        kwargs = {"instructions": q["instructions"]}
        if "criteria" in q:
            kwargs["criteria"] = q["criteria"]
        questions[qid] = QUESTION_TYPES[kind](**kwargs)
    return questions


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    raw = sys.stdin.read() if sys.argv[1] == "-" else Path(sys.argv[1]).read_text()
    req = json.loads(raw)
    questions = build_questions(req["questions"])
    with TypeSafeClient(model=req.get("model", DEFAULT_MODEL)) as client:
        response = client.system_one(state=req["state"], questions=questions)
    print(json.dumps(response.model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
