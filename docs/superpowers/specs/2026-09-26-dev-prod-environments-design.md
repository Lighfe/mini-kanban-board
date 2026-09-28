# Dev and prod environments — design

Date: 2026-09-26. Builds on [deployment-plan.md](../../../_docs/deployment-plan.md)
step 6 and follows the course module
[DevOps and observability for an AI app](https://aishippingblog.com/p/devops-and-observability-for-an-ai):
"Create a second, independent copy of our infrastructure. We will use the
copy as production, and the existing infrastructure as a dev environment."

## Goal

Two independent AWS environments, `dev` and `prod`, each deployed and
destroyed by hand through `deploy.yml`, neither able to affect the
other. Plus two spending safeguards: instance-size caps on the deploy
roles and a scheduled auto-destroy.

Success: both environments can be up at once on their own hostnames;
destroying one leaves the other serving; the dev deploy role is denied
on prod resources and vice versa.

## Decisions

- **Same AWS account, region (eu-central-1) and `lighfe.dev` hosted zone.**
- **Both environments stay ephemeral**, as today: RDS is deleted on
  destroy, no snapshot.
- **Hostnames:** prod keeps `katban-10x-cat-productivity.lighfe.dev`;
  dev is `dev.katban-10x-cat-productivity.lighfe.dev`.
- **One shared ECR repo, per-environment deploy roles and app stacks.**
  The course builds an image once and promotes the same image to prod;
  a shared repo supports that without copying images between repos.
- **Image tags** use the course's `YYYYMMDD-HHMMSS-shortsha` pattern
  (UTC), replacing the plain git SHA. The repo becomes tag-immutable, so
  a pushed tag can't be overwritten (e.g. by the dev role replacing an
  image prod runs).

## Out of scope (next task)

Dev auto-deploy on push and a manual promote-dev-to-prod workflow. This
design leaves the seams for them (shared repo, build job separate from
deploy job, tag format) but does not build them.

The build pipeline is a shared trust dependency: prod runs images the
dev role pushed. Immutable tags stop later tampering, not a bad build.
That is inherent to build-once-promote and accepted.

## AWS layout

| Stack | Template | Contents | Applied by |
| --- | --- | --- | --- |
| `kanban-bootstrap` (existing, trimmed) | `bootstrap.yaml` | GitHub OIDC provider, ECR repo `kanban` | admin, by hand |
| `kanban-deploy-dev`, `kanban-deploy-prod` | new `deploy-role.yaml`, parameter `Environment` | deploy role `kanban-deploy-<env>`, EC2 permissions boundary `kanban-app-<env>-ec2-boundary` | admin, by hand |
| `kanban-app-dev`, `kanban-app-prod` | `stack.yaml` | EC2 + Caddy, RDS, security groups, DB secret, EC2 instance role | CI |

- The deploy role and the EC2 permissions boundary move out of
  `bootstrap.yaml` so the ECR repo stays in place (no delete or resource
  import). The bootstrap update removes the old `kanban-app-deploy` role
  and `kanban-app-ec2-boundary` policy.
- ECR: `ImageTagMutability: IMMUTABLE`; lifecycle keeps the last 30
  images instead of 5. Auto-destroy caps an environment's life at about
  4 days, so 30 dev builds overtaking prod's image in that window is not
  a realistic case.
- `stack.yaml` resource names use the stack name as prefix:
  secret `kanban-app-<env>-db`, instance role `kanban-app-<env>-ec2`,
  EC2 `Name` tag `kanban-app-<env>`. CloudFormation-generated names
  (RDS instance, subnet group, instance profile) already start with the
  lowercased stack name. The instance role attaches its environment's
  boundary.
- Deploy roles are named `kanban-deploy-<env>`, not `kanban-app-*`, so
  no role-ARN pattern in either policy can match a deploy role itself
  (the self-escalation path fixed in step 6).

## EC2 permissions boundary

Per environment, same as today's boundary except the secret: ECR pull,
the SSM agent actions, and `secretsmanager:GetSecretValue` on
`secret:kanban-app-<env>-*` only. Today's boundary allows all
`kanban-app-*` secrets, which would let the dev role write its instance
role a policy for prod's DB secret and read it through its SSM shell.

## Deploy role permissions

Each role gets the same actions the current role has, scoped to its own
environment:

| Area | Scope |
| --- | --- |
| CloudFormation | `stack/kanban-app-<env>/*` |
| Secrets Manager | `secret:kanban-app-<env>-*` |
| IAM create/put/attach on the instance role | `role/kanban-app-<env>-ec2`, only with its own environment's boundary (condition `iam:PermissionsBoundary`) |
| IAM other role/profile actions, PassRole | `role/kanban-app-<env>-ec2`, `instance-profile/kanban-app-<env>-*` |
| RDS | separate statements: `CreateDBInstance` on `db:kanban-app-<env>-*` with the class cap; `CreateDBInstance` on the env's `subgrp:` ARN and the default `pg:default.postgres17` / `og:default:postgres-17` ARNs without it; subnet-group create/delete/tag on `subgrp:kanban-app-<env>-*` |
| EC2 mutating actions: Terminate/Stop/Start/ModifyInstanceAttribute, DeleteSecurityGroup, Authorize/Revoke ingress and egress | condition `aws:ResourceTag/aws:cloudformation:stack-name = kanban-app-<env>` |
| SSM `SendCommand` | two statements: the AWS-owned `AWS-RunShellScript` document unconditionally; instances with `ssm:resourceTag/aws:cloudformation:stack-name = kanban-app-<env>` |
| Route 53 `ChangeResourceRecordSets` | `ForAllValues:StringEquals` on normalized record names (the env's hostname only), record types (`A`) and actions (`UPSERT`, `DELETE`). List/get on the hosted zone in a separate read statement |
| ECR | dev: push + pull + describe; prod: describe only (prod never builds; instances pull with their own role) |
| Create/describe actions without resource-level support | `*`, as today |

`aws:`-prefixed tags cannot be set by any principal, so the stack-name
tag cannot be forged to reach the other environment's resources.
`CreateTags`/`DeleteTags` stay unscoped: user tags play no part in any
authorization decision here.

The Route 53 record-type and action limits matter because prod's
hostname is the parent of dev's: a CAA record at the prod name could
block certificate issuance for dev.

Trust: each role trusts GitHub OIDC tokens with
`sub = repo:Lighfe@*/mini-kanban-board@*:environment:<env>`. Jobs that
declare `environment:` get this `sub` instead of the branch ref, so the
current `ref:refs/heads/main` condition would stop matching. The
main-only guarantee moves to the GitHub Environments' deployment-branch
rule.

### Spending cap: instance sizes

- `ec2:RunInstances` split by resource type:
  - `instance/*` only with `ec2:InstanceType = t3.micro`;
  - `security-group/*` only with the environment's stack-name tag, so
    dev can't launch an instance into prod's security group (which
    prod's RDS trusts);
  - subnet, network interface, volume, image without conditions.
- Deny `ec2:ModifyInstanceAttribute` when `ec2:Attribute/InstanceType`
  is present and not `t3.micro` (a resize of the stack's own instance).
- `rds:CreateDBInstance` on the `db:` resource only with
  `rds:DatabaseClass = db.t4g.micro`. The role has no
  `rds:ModifyDBInstance`, so the class can't be changed later.

## GitHub

- GitHub Environments `dev` and `prod`, each with a deployment-branch
  rule of `main` only, no required reviewers (solo repo).
- Per environment: secret `AWS_DEPLOY_ROLE_ARN`, variable `SUBDOMAIN`.
- Repo-level variables `AWS_HOSTED_ZONE_ID`, `AWS_VPC_ID`,
  `AWS_SUBNET_IDS` stay shared. The repo-level `AWS_DEPLOY_ROLE_ARN`
  secret is deleted after cutover.

## Workflows

### `deploy.yml`

Inputs: `action` (`deploy`/`destroy`), `environment` (`dev`/`prod`).
Concurrency group `deploy-kanban-app-<env>`, `cancel-in-progress: false`,
so dev and prod runs don't block each other.

- `build` (deploy only): `environment: dev` (only the dev role can
  push). Computes the tag, builds, pushes, outputs `image_tag`. A prod
  deploy therefore builds with dev's credentials; promotion replaces
  this later.
- `deploy`: `needs: build`, `environment: ${{ inputs.environment }}`,
  its own credentials and its own ECR URI lookup; stack
  `kanban-app-<env>`, hostname from `vars.SUBDOMAIN`. Every place that
  selects an image (CloudFormation parameter, SSM compose file) uses
  `needs.build.outputs.image_tag`. Steps otherwise as today:
  CloudFormation, DNS, SSM push, health check.
- `destroy`: independent of `build`; `environment: ${{ inputs.environment }}`,
  checkout, credentials, then the shared destroy action.

### Shared destroy action

`.github/actions/destroy-env`, used by `deploy.yml` and
`auto-destroy.yml`. Inputs: stack name, hostname, hosted zone ID,
region; callers check out the repo and configure credentials; run steps
use `shell: bash`.

- DNS: read the actual `A` record for the hostname from Route 53 and
  delete that (today it's rebuilt from the stack's IP output, which
  misses drifted records and is gone once the stack is). No record: no-op.
- Stack: delete and wait. No stack: no-op.
- A DNS failure doesn't stop the stack deletion but fails the step at
  the end. Only a confirmed missing record/stack counts as success;
  no blanket `|| true`.

### `auto-destroy.yml`

- Triggers: daily cron (`max_age_hours` = 72), plus `workflow_dispatch`
  with a `max_age_hours` input for testing. `0` must work (no
  `inputs.x || 72` fallback); non-numeric input fails.
- Matrix over `dev` and `prod`, `fail-fast: false`. Each job runs in
  that GitHub Environment with that environment's role and holds the
  same concurrency group as `deploy.yml` from before the age check until
  the deletion finishes.
- Reads the stack's `CreationTime`; if `kanban-app-<env>` exists and is
  older than the threshold, runs the destroy action. No stack: no-op.
- Creation time counts from the first deploy; redeploys don't reset it.

Limits, documented rather than engineered around:

- Best effort: with a daily run, an environment is destroyed on the
  first run after 72 hours, i.e. after roughly 72–96 hours. GitHub can
  delay or skip scheduled runs. deployment-plan.md's "never longer than
  3 days" is reworded to match.
- It only reaches `kanban-app-dev` and `kanban-app-prod`, not anything
  else in the account.
- GitHub disables scheduled workflows in public repos after 60 days
  without repo activity; the AWS budget alert covers that case.
- The 72-hour threshold and daily schedule are open questions to
  revisit, not fixed rules.

## Not ephemeral

The shared layer is not covered by auto-destroy: `kanban-bootstrap`,
both deploy-role stacks, the ECR images (cents per month), and the
Route 53 hosted zone (~$0.50/month). deploy/README.md gets a short
cleanup section for removing them by hand.

## Cutover

Nothing is running (only `kanban-bootstrap` exists, checked 2026-09-26);
recheck right before starting. Every AWS command that creates, changes
or deletes resources is shown to the user and approved before it runs.
New pieces are added before old ones are removed.

1. Branch: templates, workflows, composite action, docs. Codex review.
2. (AWS) Create `kanban-deploy-dev` and `kanban-deploy-prod` (roles and
   boundaries).
3. (AWS) Update `kanban-bootstrap` for tag immutability and lifecycle
   30 only; the old role and boundary stay for now.
4. `gh`: create both GitHub Environments with branch rules, secrets and
   variables.
5. Merge once CI is green. The Environments' main-only rule is what
   restricts deploys to `main`; scheduled runs use the default branch
   (`main`).
6. Verification (below).
7. (AWS) Update `kanban-bootstrap` again to remove the old role and
   boundary; `gh`: delete the repo-level `AWS_DEPLOY_ROLE_ARN`.

## Verification

- `cfn-lint` on all templates.
- IAM without deploying: `aws iam simulate-principal-policy` for each
  role, with context keys for the tag and Route 53 conditions. Own stack,
  secret, DB, instance, DNS name: allowed. The other environment's:
  denied, including a mixed dev+prod Route 53 change batch and
  `RunInstances` with the other environment's security group.
  `RunInstances` with `t3.large` and `CreateDBInstance` with
  `db.m5.large`: denied. The simulator can't prove the real request
  context; the live run does.
- Live, from cold, dev first (it doubles as the rehearsal for the tag
  conditions):
  1. Deploy dev; `https://dev.katban-…/api/health` returns 200.
  2. Redeploy dev with a new image (warm instance: the Stop/Modify/Start
     and SSM path from step 6); confirm via SSM that the running
     container uses the new tag.
  3. Deploy prod; `https://katban-…/api/health` returns 200, both up.
  4. Destroy dev; prod still returns 200.
  5. Run `auto-destroy.yml` by hand with `max_age_hours: 0`; prod is
     destroyed. A second run with no stacks is a no-op.
  6. `describe-stacks` shows only the shared stacks; neither DNS record
     remains.
- Known risk: the tag conditions depend on CloudFormation tagging a
  security group or instance before modifying it. An `AccessDenied`
  gets diagnosed from stack events, not fixed by widening permissions
  blindly.

## Docs

- `_docs/deployment-plan.md`: new step 7 (dev and prod environments);
  reword "never longer than 3 days".
- `deploy/README.md`: per-environment setup, running, auto-destroy,
  cleanup of the shared layer.
- `README.md`: the two commands take `-f environment=dev|prod`.
