# Decision delegation (Jev)

Status: design. Nothing below is built yet except the secrets setup.

Agents hand narrow decisions to Jev, TypeSafe's System One model, which
returns a typed answer with a probability distribution. Low-confidence
answers go through a framing check by Codex, then to Claude or the human.
References: [TypeSafe docs](https://docs.typesafe.ai/llms.txt),
[confidence](https://docs.typesafe.ai/confidence.md),
[confidence-gated routing](https://docs.typesafe.ai/patterns/confidence-routing.md).

## Goals

In priority order, all measured by the pilot:

1. Fewer agent tokens per decision.
2. Less agent wall-clock time per decision (excludes waiting on the human).
3. Equal or better decision quality.
4. Fewer decisions escalated to the human.

Jev costs $0.042 per million input tokens (output is free) and answers a
13-question batch in ~0.3 s ([models](https://docs.typesafe.ai/models.md),
[parallel questions](https://docs.typesafe.ai/cookbooks/parallel_questions.md)).
The main cost is on the agent side: each delegation is a tool call in
which Claude writes the question and options. A trivial Codex call
(`gpt-6-astra`, "Reply with exactly: ok") took 13 s and 3.2k tokens.

## When to delegate

A decision is delegated when it is **narrow** (one judgment over a fixed
set of options) and at least one of:

- it comes in bulk (many findings, files, or items with the same question);
- its state can be assembled by a script without Claude reading it;
- Claude would otherwise deliberate at length, call Codex, or ask the human.

Small decisions that meet none of these are made inline by Claude.
Complex decisions are split into narrow questions. Independent questions
go in one request; a second request is used only when an answer changes
the next question's state or options.

Never delegated: push, merge, deploy, visibility or permission changes,
and edits to `specs.md`. These follow the existing rules in AGENTS.md.

## Request shape

Each decision sends two questions in one request:

- `decision`: a Choice over the options plus `none_fit` ("none of these
  options applies").
- `context_sufficient`: a Noul, "Does the state contain enough
  information to choose between these options?"

State is JSON with named fields (for example `finding`, `diff`,
`spec_excerpt`). Every option has criteria with the same structure:
`what` and `not_for`.

## Neutral framing

- Claude records its expected answer and a one-line reason in a `prior`
  field. The script logs it and does not send it to Jev or Codex.
- The script shuffles option order before sending.
- The script rejects criteria containing preference words
  (`recommended`, `preferred`, `best`, `safer`, `should`).
- The Codex framing check does not see the prior. One of its checks is
  whether the wording favors an option.

## Flow

```
frame (state, options + none_fit, prior)
  -> Jev {decision, context_sufficient}
  -> confidence >= tier threshold            -> act, log
  -> context_sufficient < 0.5                -> Claude adds evidence, re-ask (max 1)
  -> decision == none_fit                    -> Claude revises options, re-ask (max 1)
  -> otherwise low confidence                -> Codex framing check, re-ask (max 1)
  -> still low: tier A                       -> Claude decides, log
                tier B                       -> human
```

If the top two options are both acceptable for the task, Claude takes
the top one and logs it as a preference split instead of escalating.

The Codex framing check may add evidence, split or merge overlapping
options, and fix criteria wording. It may not remove an option that had
probability >= 0.2 without stating why.

### Thresholds

Starting values, to be tuned from the log:

| Tier | Scope | Act at |
|---|---|---|
| A | Reversible, local to the working tree (for example "defer this finding") | confidence >= 0.6 |
| B | Changes committed code or docs (for example "fix now: change X") | confidence >= 0.85 |

Any answer with confidence < 0.6 is treated as low in both tiers.

### Disagreement with the prior

If Jev answers with confidence above the threshold and disagrees with
Claude's prior, Claude may override only by naming a concrete fact that
is missing from the state. The fact is added to the state and Jev is
asked again. Without such a fact, Jev's answer stands.

### Human escalation

The question lists the options without a "(Recommended)" label and shows
Jev's distribution and Claude's prior, each labeled by source.

## Components

- `tools/jev/`: a separate `uv` project using `typesafe-sdk`. Takes a
  request JSON (state, options, criteria, prior, tier), sends it to Jev
  with the model version pinned, applies the flow rules, and prints the
  result as JSON. Not a dependency of `backend/`.
- `.claude/skills/delegate-decision/`: when to delegate, how to frame,
  tier per decision type.
- Decision log: one JSONL line per decision, written by the script to
  `~/.local/state/mini-kanban-board/decisions.jsonl` (outside the repo).
  Fields: decision type, tier, options, probabilities, confidence,
  `context_sufficient`, prior, agreement with prior, path taken
  (direct / re-ask / Codex / Claude / human), time per step, Claude
  and Codex tokens where available.

## Models

- Jev: `jev-1.13.0` (pinned; `jev-latest` points to it as of 2026-09-24).
- Codex (framing check and stage-end reviews): `gpt-6-astra`, the
  Codex default. No models older than GPT-6. `gpt-6-sol` is rejected by
  Codex when signed in with a ChatGPT account; using it requires OpenAI
  API key auth.

## Secrets

Secrets live outside the repo in
`~/.config/mini-kanban-board/secrets.env` (dir 700, file 600), with
variables documented in [secrets.env.example](../secrets.env.example).
[scripts/with-secrets](../scripts/with-secrets) exports them into a
single command's environment, for any agent or a human:

```
scripts/with-secrets uv run --project tools/jev python -m jev ...
```

`.claude/settings.json` denies Claude Code reads of the secrets
directory. Codex and other agents with shell access can still read the file;
the key limits are scoped keys and spend limits set at the provider.
AWS access uses SSO profiles (`AWS_PROFILE`), not static keys.

## Pilot

Decision type: triage of Codex review findings into `fix_now`, `defer`,
`reject`. Replayed from past stage reviews in git history and
[archive/](archive/).

For each finding, compare Claude deciding inline against the Jev flow:

- agent tokens and wall-clock time per finding;
- agreement between the two, and with the outcome recorded in history;
- share of findings reaching each path (direct, re-ask, Codex, Claude,
  human).

The pilot continues into the regular workflow only if the Jev flow is
not worse on tokens and time, and not worse on agreement with the
recorded outcomes.

## Open questions

- Whether the Codex framing check pays for its time (13 s+ per call) at
  tier A, or only at tier B.
- Whether preference-word linting produces false rejections on real
  criteria.
- Which decision type to add after the pilot.
