# Deploy stage (step 6) postmortem

Written after the fact, once step 6 of [deployment-plan.md](deployment-plan.md)
was done and verified. The design in that file didn't change; getting a real
deploy to pass took 11 rounds of live failure → diagnose → fix, two Codex
reviews, and about two hours longer than it should have. This is what went
wrong and what to do differently next time this shape of work comes up
(anything: IaC + CI/CD + a service with many interacting AWS resources).

## What happened, in one line

The design was sound, but each fix was shipped as soon as it looked
plausible instead of being proven end-to-end first. Two of those "fixes"
looked like they worked and didn't — one was masked by a leftover duplicate
step, the other fixed a symptom without fixing the underlying race.

## Root causes

### 1. Green CI was treated as "the fix works"

`cfn-lint` and YAML parsing catch syntax errors, not runtime behavior.
Nearly every real bug in this stage — IAM permission gaps, the CPU
architecture mismatch, the password/URL bug, the DNS-ordering race, the
`UserData` race — was a *runtime* AWS behavior no static check could catch.
The actual test loop was "trigger `deploy`, see if it goes green," which is
correct in principle. The mistake was declaring victory on the first green
run instead of asking *what would make this look green while still being
broken?*

That question would have caught the duplicate-`SSM`-step bug immediately —
two identical steps appearing in a job's step list is visibly odd. It was
noticed and dismissed as a "display quirk" instead of investigated.

### 2. IAM was scoped from a *read* of the requirements, not a dry run

The deploy role's policy was written by reasoning about what
`cloudformation deploy` *should* need, then the real requirements surfaced
only when each code path executed for the first time: dynamic-reference
resolution, a secret update, an EC2 stop/start cycle, an SSM command. Four
separate `AccessDenied` errors, two of which left the stack in
`UPDATE_ROLLBACK_FAILED`, needing a manual `continue-update-rollback` to
recover.

This is a known sharp edge of scoped IAM + CloudFormation. The fix isn't
"grant more permissions upfront" — it's **prove the deploy works against a
real (temporarily more permissive) role first, then narrow it**, instead of
narrowing first and discovering the gaps against a stack that's mid-deploy
in production.

### 3. Ground-truth tooling (container logs) was added too late

`docker logs` / SSM Session Manager access wasn't added to the EC2 instance
role until the *second* distinct failure (the architecture mismatch).
Before that, diagnosis relied on CloudFormation stack events and HTTP
status codes alone — neither shows what's happening inside the container.
Once SSM access existed, every subsequent bug took one command to
identify. That access should be part of the first version of any
EC2-in-the-loop deploy design, not added reactively.

### 4. An edit introduced a bug that wasn't re-verified against intent

The DNS-ordering fix was a botched `Edit`: a new, correctly-ordered step was
inserted, but the old, wrongly-ordered one was never removed — the workflow
ended up with two copies of the same step. The live test that "confirmed"
the fix was actually exercising the accidental workaround (the second,
redundant copy happened to run after DNS was already correct), not the
intended fix. This was only caught because a second Codex review re-read
the whole file fresh; the earlier "I tested it live, it works" claim was
wrong because the *wrong thing* was being tested.

**Lesson:** after any edit to something order-sensitive (a CI workflow, a
state machine, anything where sequence matters), diff the *whole* file
against intent afterward — not just the hunk that was touched — before
calling it done.

### 5. Outside review was the highest-value single thing done, and it only happened twice

Every bug that survived a round of live self-testing was caught by a
review reading the code fresh (Codex), not by re-testing. Live testing is
necessary but not sufficient for this class of work: it confirms a given
run passed, not that the reasoning behind the fix was correct. A fresh
read of the code, unburdened by "I already decided this works," is a
different and complementary check.

## What to do differently next time

1. **Get a working end-to-end deploy first with a broad(er) role, then
   narrow the IAM policy as a separate, mechanical pass** (e.g. by reading
   back which API calls the role actually made from CloudTrail). Discovering
   permission gaps live means eating a full resource-provisioning cycle
   (minutes) per missing permission; narrowing a working setup is cheap and
   low-risk by comparison.
2. **Add shell/log access to any EC2-in-the-loop resource from the start**
   (SSM Session Manager, or equivalent) — don't wait for the second opaque
   failure to add it.
3. **After every fix, re-read the full changed file and ask "does this file
   now say what I intended?"** before re-running the pipeline. A clean exit
   code is not the same question as "did the edit do what I meant."
4. **Be suspicious of a "looks fixed" pass if the causal mechanism can't be
   stated.** If it's not possible to explain, before re-running, exactly why
   the previous failure mode is now impossible, that's a sign the fix has
   only been observed to correlate with success, not verified to cause it.
5. **Get a review after each risky change, not only at the end**, for
   infra work with many interacting failure modes. Batching all review to
   the end means many rounds of self-testing happen first, each one a
   chance to ship a masked or partial fix that only a fresh read catches.
6. **Budget for this class of task realistically up front.** A from-scratch
   cloud deploy with scoped IAM, TLS automation, and a database is
   inherently a multi-hour iteration loop the first time, even with a
   correct design — RDS provisioning alone is 5+ minutes per attempt. That
   cost isn't avoidable by being more careful mid-flight; it's reducible by
   front-loading more of the guesswork (permissions, architecture, script
   correctness) into cheap, non-live checks before the first real deploy
   attempt, and by treating the first deploy attempt as a rehearsal, not
   the real thing.

## Concrete list of what broke, for reference

In the order hit, each requiring a live `deploy` run to surface:

1. OIDC trust policy `sub` claim didn't account for GitHub's numeric
   org/repo IDs.
2. Deploy role missing `ssm:GetParameters` / `secretsmanager:GetSecretValue`
   for CloudFormation's own dynamic-reference resolution.
3. Missing one-time `AWSServiceRoleForRDS` service-linked role (first RDS
   use in the account).
4. Invalid character (apostrophe) in a security group rule description.
5. CPU architecture mismatch: image built on amd64 CI runners, deployed to
   an arm64 (`t4g.micro`) instance.
6. Generated RDS password could contain URL-reserved punctuation, breaking
   its own connection string.
7. Deploy role missing `secretsmanager:UpdateSecret` (also broke automatic
   rollback, needing manual recovery).
8. Deploy role missing `ec2:StopInstances`/`StartInstances` for in-place
   `UserData` updates (also broke automatic rollback again).
9. Critical: deploy role's IAM resource pattern also matched its own role
   name, a self-escalation path (found by review, not live testing).
10. `EC2` `UserData` only runs once at first boot — redeploys against an
    existing instance never applied the new image (found by review).
11. DNS updated after containers started, not before — Caddy's ACME
    challenge hit `NXDOMAIN`.
12. The DNS-ordering fix left a duplicate, wrongly-ordered step in place
    (found by a second review, after a live test wrongly looked clean).
13. `SSM` agent came online before `UserData`'s `docker` install finished.
14. Root cause of #13: `UserData`'s own closing `docker-compose up -d` was
    still in flight, racing the workflow's own — fixed with
    `cloud-init status --wait`, not a docker-readiness poll.
