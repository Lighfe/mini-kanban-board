---
name: delegate-decision
description: Use when triaging code review findings (fix now / defer / reject), e.g. after a Codex review at the end of a stage. Delegates the decision to Jev via tools/jev and applies the result.
---

# Delegate a decision to Jev

Design: [_docs/decision-delegation.md](../../../_docs/decision-delegation.md).

Templates available: `finding_triage` (one review finding → `fix_now`,
`defer`, `reject`).

## Steps

1. For each finding, before running the tool, write your own expected
   answer and a one-line reason as `prior`. Don't put it anywhere else.
2. Write the request (one per finding) and run from the repo root:

   ```
   scripts/with-secrets TYPESAFE_API_KEY -- \
     uv run -q --project tools/jev python -m jev request.json
   ```

   ```json
   {"template": "finding_triage",
    "item": {"title": "...", "body": "<finding text as the reviewer wrote it>",
             "severity": "low|medium|high|critical", "file": "path", "line": 42},
    "prior": {"answer": "defer", "reason": "..."}}
   ```

   Copy the finding text as written; don't summarize or add your view.
3. Act on the output:
   - `outcome: accept`: apply `answer`.
   - `outcome: fallback`, `decider: claude`: decide yourself; the
     output's attempts show Jev's distribution.
   - `outcome: fallback`, `decider: human` (high-severity finding where
     Jev's top answer disagrees with your prior): ask the user. List the
     options without a recommended label; show Jev's last distribution
     and your prior, each labeled by source.
4. If an accepted answer disagrees with your prior, you may re-run once
   with `"facts": ["<fact> (<file>:<line>)"]` naming a concrete fact the
   state lacks. Without such a fact, Jev's answer stands.

The tool logs each decision to
`~/.local/state/mini-kanban-board/decisions.jsonl`. Once 10 or more are
logged, if more than 1 in 10 went to the human, stop using the tool and
tell the user.
