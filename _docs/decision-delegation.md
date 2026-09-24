# Decision delegation (Jev)

Status: `finding_triage` built in [tools/jev/](../tools/jev/); pilot not run.

Agents hand narrow decisions to Jev, TypeSafe's System One model, which
returns a typed answer with a probability distribution. Answers that
fail the gates below go through a Codex framing check, then to Claude or
the human. References: [TypeSafe docs](https://docs.typesafe.ai/llms.txt),
[confidence](https://docs.typesafe.ai/confidence.md),
[confidence-gated routing](https://docs.typesafe.ai/patterns/confidence-routing.md).

## Goals

In priority order, all measured by the pilot:

1. Fewer agent tokens per decision.
2. Less agent wall-clock time per decision (excludes waiting on the human).
3. Equal or better decision quality.
4. Fewer decisions escalated to the human.

Observations so far, not yet measured for this workflow:

- Jev pricing: $0.042 per million input tokens, output free
  ([models](https://docs.typesafe.ai/models.md)).
- TypeSafe's parallel-questions cookbook reports 0.27 s for one request
  with 13 questions over a ~54k-character document.
- `finding_triage` requests (2026-09-24): 0.7-1.5 s, ~360 input tokens
  without code excerpt. A run including the Codex check took 28-33 s.
- A trivial Codex call ("Reply with exactly: ok", `gpt-6-astra`,
  `codex exec`, 2026-09-24) took 13 s wall clock and reported 3,203
  tokens used.

## When to delegate

A decision type is delegated when it has a reviewed question template
(below), is **narrow** (one judgment over a fixed set of options), and
at least one of:

- it comes in bulk (many findings, files, or items with the same question);
- its state can be assembled by a script without Claude reading it;
- Claude would otherwise deliberate at length, call Codex, or ask the human.

Other decisions are made inline by Claude. Complex decisions are split
into narrow questions. Independent questions go in one request; a second
request is used only when an answer changes the next question's state or
options.

Never delegated: push, merge, deploy, visibility or permission changes,
and edits to `specs.md`. These follow the existing rules in AGENTS.md.

## Question templates

Each decision type has a versioned template in `tools/jev/templates/`:

- instructions and options, each with `what` and `not_for` criteria,
  plus `none_fit` ("none of these options applies");
- the state fields and the script that extracts them from the repo
  (for example finding text, the referenced diff hunk, the matching
  `specs.md` section);
- the risk rule that sets the tier (see Thresholds).

At most 5 templates. Each is reviewed by Codex and the human before
use. A wording change is a new template version. Per decision, Claude supplies only the
item reference and its prior; it does not write criteria.

## Request shape

Each decision sends two questions in one request:

- `decision`: a Choice over the template's options plus `none_fit`.
- `context_sufficient`: a Noul, "Does the state contain enough
  information to choose between these options?"

## Neutral framing

- Criteria come from reviewed templates, not per-decision wording.
- State is extracted by the template's script, not selected by Claude.
- Claude records its expected answer and a one-line reason in a `prior`
  field. The script logs it and does not send it to Jev or Codex.
- The script shuffles option order before sending.
- The Codex framing check does not see the prior. One of its checks is
  whether the wording favors an option.
- The pilot compares Jev's answers with Claude's priors and the human's
  labels.

## Flow

The script runs this as a state machine with a budget of 3 Jev requests
and 1 Codex call per decision. Every retry, including the disagreement
retry, counts against the budget. When the budget is spent, the decision
goes to the fallback for its tier.

Gates, checked in order on each Jev answer:

1. Invalid response or API error: retry once (counts against budget).
2. `context_sufficient` < 0.4: Codex check, re-ask.
3. `none_fit` probability >= 0.3: fallback. Options are fixed per
   template version; a recurring `none_fit` is fixed by a new template
   version.
4. Confidence below the tier threshold: Codex check, re-ask.
5. Otherwise: accept.

The Codex check gets the question, options and state (not the prior),
and returns up to 5 facts from the repo with sources, which are added
to the state. It may also report a template defect (logged); it does
not change options. If it returns no facts: fallback.

Questions in one request don't see each other, so the context question
restates the decision question and options. The 0.4 threshold is
provisional: on five probe findings, `context_sufficient` was 0.47-0.56,
except 0.26 for the one with no code attached. Decision confidence on
the same probes was 0.13-0.84; only one would pass tier A.

Fallback: tier A, Claude decides. Tier B, the human decides only if
Jev's last top answer disagrees with Claude's prior; otherwise, and on
API errors, Claude decides. Each fallback is logged with the gate that
caused it.

### Thresholds

The tier is set before inference by the template's risk rule, using the
item's own attributes (for example finding severity). It covers the
risk of every option, including inaction (`defer`, `reject`).

| Tier | Example | Accept at |
|---|---|---|
| A | Low-severity finding: wrong choice costs a small follow-up edit | confidence >= 0.6 |
| B | High-severity finding: deferring or rejecting could ship a bug | confidence >= 0.85 |

Starting values, tuned from the pilot. Confidence describes the shape of the
distribution, not accuracy; the pilot checks it against observed errors.

### Disagreement with the prior

If an accepted answer disagrees with Claude's prior, Claude may add one
concrete fact that the extracted state lacks, with its source (file and
line). The fact is appended to the state and Jev is asked again, within
the budget. Without such a fact, Jev's answer stands.

### Human escalation

The question lists the options without a "(Recommended)" label and shows
Jev's distribution and Claude's prior, each labeled by source.

## Components

- `tools/jev/`: a separate `uv` project using `typesafe-sdk`. Runs the
  state machine and prints the result as JSON. Pins the Jev model
  version. Not a dependency of `backend/`.
- `tools/jev/templates/`: question templates and their extraction
  scripts.
- `.claude/skills/delegate-decision/`: which decision types have
  templates and how to invoke the tool.
- Decision log: one JSONL line per decision, written by the script to
  `~/.local/state/mini-kanban-board/decisions.jsonl` (outside the repo).
  Fields: template and version, model versions, tier, every attempt
  (probabilities, confidence, `context_sufficient`, gate result,
  duration), prior, agreement with prior, final path and decider.

## Models

- Jev: `jev-1.13.0` (pinned; `jev-latest` points to it as of 2026-09-24).
- Codex (framing check and stage-end reviews): `gpt-6-astra`, the Codex
  default. No models older than GPT-6. `gpt-6-sol` is rejected by Codex
  when signed in with a ChatGPT account; using it requires OpenAI API key
  auth.

## Secrets

Secrets live outside the repo in
`~/.config/mini-kanban-board/secrets.env` (dir 700, file 600, literal
`KEY=value` lines), with variables documented in
[secrets.env.example](../secrets.env.example).
[scripts/with-secrets](../scripts/with-secrets) parses the file without
evaluating it, checks owner, type and mode, and exports only the named
variables into one command:

```
scripts/with-secrets TYPESAFE_API_KEY -- uv run --project tools/jev python -m jev ...
```

The `.claude/settings.json` deny rules and the AGENTS.md instructions
guard against accidental reads. They are not a security boundary: any
agent with shell access can run the wrapper with a command that prints
its environment. Exposure is limited with per-provider keys, provider
spend limits, and short-lived credentials. AWS uses SSO profiles
(`AWS_PROFILE`), not static keys.

## Pilot

Decision type: triage of Codex review findings into `fix_now`, `defer`,
`reject`.

Cases: findings from the next Codex reviews, as they happen. Past
findings exist only as commit messages (about six) and are not used.

Per finding, both arms run on the same state: Claude's prior is the
inline answer; the tool's result is the delegated answer. The human
labels only findings where the two disagree or the tool falls back to
the human.

Measured per finding, from the decision log: Jev and Codex time, Jev
input tokens, path taken, agreement with the prior, and the human label
where given. Claude's per-delegation overhead (tokens and time for the
tool call) is measured once on a few findings with `claude -p
--output-format json`, with and without the tool.

Questions to answer after about 30 findings:

- Does the flow change agent tokens or time per finding, and by how
  much?
- On disagreements, does the human side with Jev or with the prior?
- How many findings reach the human, compared with Claude deciding
  alone?
- Where does the flow help, and where is it overhead?

### Results so far

2026-09-24, 14 findings from three Codex reviews (8 whole-repo, 3 on
the resulting fix branch, 3 spec-conformance):

- Template v1 accepted 0/8: without a review scope, `fix_now` vs
  `defer` had nothing to judge against (Codex reported this as a
  template defect on every finding). `context_sufficient` stayed at
  0.38-0.66.
- v2 (scope, project context, `accept` split from `reject`, stated
  consequences) accepted 4/8, but Jev's top answer was `fix_now` on
  all 8: `defer` required "no effect on correct or safe use", which
  review findings rarely meet. v3 weighs impact against fix size;
  `defer` rose to 0.19 on one contested finding, `fix_now` still led.
- Fix-branch review on v3: 1/3 accepted, 2 fell back to Claude.
- Spec-conformance review on v3 (3 findings, all tier A): 2/3 accepted,
  both agreeing with the prior; 1 fell back to Claude. No human
  escalation.
- Human labels on disagreements: 2 of 3 sided with Jev.
- Jev answers in 1-2 s; the Codex check takes 20-45 s. One check hung
  for ~90 min (child processes kept the pipes open; fixed), and Codex
  hit its usage limit during the run; the flow fell back as designed.

## Open questions

- Whether the Codex check is worth its time (~30 s per call)
  at tier A, or only at tier B.
- Claude writes a prior for every finding, so the tool adds Claude
  tokens unless it replaces longer deliberation. The overhead
  measurement decides whether priors stay mandatory after the pilot.
- Which decision type to add after the pilot.
- How much human feedback the flow adds compared with Claude deciding
  alone. Revisit the tier B fallback rule if the log shows a noticeable
  share of findings reaching the human.
