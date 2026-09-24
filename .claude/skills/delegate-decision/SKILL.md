---
name: delegate-decision
description: Use when triaging code review findings (fix now / defer / reject), e.g. after a Codex review at the end of a stage. Delegates the decision to Jev via tools/jev and applies the result.
---

# Delegate a decision to Jev

Design: [_docs/decision-delegation.md](../../../_docs/decision-delegation.md).

Templates available: `finding_triage` v2 (one review finding →
`fix_now`, `defer`, `accept`, `reject`).

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
    "item": {"review_scope": "change|repository",
             "title": "...", "body": "<finding text as the reviewer wrote it>",
             "severity": "low|medium|high|critical", "file": "path", "line": 42},
    "prior": {"answer": "defer", "reason": "..."}}
   ```

   `review_scope` is `change` when the review covered the current
   branch, `repository` when it covered the whole repo. Copy the finding
   text as written; don't summarize or add your view.
3. Act on the output:
   - `outcome: accept`: apply `answer`:
     - `fix_now`: fix it before the review is closed.
     - `defer`: `gh issue create` with the finding and the reason.
     - `accept`, `reject`: no action (the log has the decision).
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
`~/.local/state/mini-kanban-board/decisions.jsonl`.
