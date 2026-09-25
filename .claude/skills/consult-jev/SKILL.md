---
name: consult-jev
description: Ask TypeSafe's Jev model a typed judgment (choice, yes/no probability, or score, with probabilities) via tools/jev. For when the user asks to consult Jev, or a narrow judgment with a confidence value is useful.
---

# Consult Jev

Jev returns typed answers with probabilities, not text. For how to phrase
state, instructions and criteria, see the `typesafe:typesafe-ai` skill
and https://docs.typesafe.ai/llms.txt.

Run from the repo root:

```
scripts/with-secrets TYPESAFE_API_KEY -- \
  uv run -q --project tools/jev python -m jev request.json   # or - for stdin
```

```json
{"state": {"<field>": "..."},
 "questions": {
   "pick": {"type": "choice", "instructions": "...", "criteria": {"a": "...", "b": "..."}},
   "holds": {"type": "noul", "instructions": "..."},
   "level": {"type": "score", "instructions": "...", "criteria": ["low ...", "high ..."]}}}
```

The output has the answers by question id (`choice` + `probabilities` +
`confidence`, `noul` in 0-1, `score` + `probabilities`) and token usage.
Report Jev's answer with its probabilities; it is one input, not a
decision on its own.
