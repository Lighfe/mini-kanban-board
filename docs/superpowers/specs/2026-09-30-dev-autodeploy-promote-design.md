# Dev auto-deploy and promote to prod — design

Date: 2026-09-30. Builds on [deployment-plan.md](../../deployment-plan.md)
step 7 and the [dev/prod design](2026-09-26-dev-prod-environments-design.md).
Covers the rest of phases 1–2 of the course module
[DevOps and observability for an AI app](https://aishippingblog.com/p/devops-and-observability-for-an-ai):
every green commit on `main` goes to dev; prod gets an image dev already
ran, by hand, after an approval. Observability is out of scope.

## Goal

- A commit on `main` that passes CI is built once and deployed to dev.
  If dev is down, the deploy recreates it.
- A manual promote deploys an existing image tag to prod without
  rebuilding, after a required approval.

Success: a push to `main` brings dev to that commit and
`/api/health` returns 200; promoting that tag waits for approval, runs
no build, and prod's `app` container runs that tag.

## Decisions

- **Dev tracks `main` and is recreated when down.** Dev is treated as
  always on while development is active: if auto-destroy removed it, the
  next green push brings it back (~10+ min). Without pushes nothing
  recreates it, so auto-destroy still caps an idle dev at about 72–96
  hours. Redeploys don't reset the stack's `CreationTime`, so an active
  dev is also destroyed and recreated about every 3–4 days.
- **Deploy waits for CI** via `workflow_run`, not a parallel `push`
  trigger.
- **Promote takes an explicit `image_tag`.** No picker (`workflow_dispatch`
  choices are static) and no "whatever dev runs" lookup (the prod role
  can't read dev's stack, and dev is often down).
- **Approval via a separate gate Environment `prod-approval`**, not a
  required reviewer on `prod`: a reviewer on `prod` would also hold
  `auto-destroy.yml`'s prod job and every prod destroy. `prod-approval`
  has no secrets and holds no AWS access; it is a deliberate checkpoint,
  not protection against someone who can change workflows on `main`.
- **No AWS changes.** The prod role already has `ecr:DescribeImages` on
  the repo.

## Workflows

| File | Triggers | Does |
| --- | --- | --- |
| `deploy.yml` | `workflow_run` (CI), `workflow_dispatch` (no inputs) | build, then deploy dev |
| `promote.yml` (new) | `workflow_dispatch`, input `image_tag` | check tag, approve, deploy prod |
| `destroy.yml` (new) | `workflow_dispatch`, input `environment` | destroy one environment |
| `auto-destroy.yml` | unchanged | unchanged |

Prod deploys and all destroys move out of `deploy.yml`; it only
deploys dev.

### `deploy.yml` (dev)

- `workflow_run` on workflow `CI`, `types: [completed]`,
  `branches: [main]`. The job runs only if
  `conclusion == 'success'`, `event == 'push'` and
  `head_repository.full_name == github.repository` (a fork PR from a
  branch named `main` must not trigger a deploy with secrets).
- The commit is `github.event.workflow_run.head_sha` for `workflow_run`
  and `github.sha` for a manual dispatch. Checkout (with submodules) and
  the image tag both use it, not `GITHUB_SHA`, which is the tip of
  `main` when the run starts.
- `build` (environment `dev`): as today.
- `deploy` (environment `dev`): checkout, credentials,
  `./.github/actions/deploy-env`. Writes the tag and
  `gh workflow run promote.yml -f image_tag=<tag>` to the job summary.
- Concurrency group `deploy-kanban-app-dev` on the `deploy` job only
  (from the Codex review): at workflow level every CI completion,
  including failed or cancelled ones that `build` then skips, would
  enter the group and replace a pending deploy or destroy.
- CI's `cancel-in-progress` on `main` means that of several quick
  pushes, older CI runs are cancelled and only the newest deploys.

### `promote.yml` (prod)

`run-name: Promote <tag> to prod`. Three jobs:

1. `check` (environment `prod`): the tag matches
   `^[0-9]{8}-[0-9]{6}-[0-9a-f]{7}$`, and
   `aws ecr describe-images --image-ids imageTag=<tag>` finds it. Fails
   before anyone is asked to approve.
2. `approve` (environment `prod-approval`): no steps beyond an echo;
   waits for the reviewer.
3. `deploy` (environment `prod`): checkout, credentials,
   `deploy-env` with the input tag. Job-level concurrency group
   `deploy-kanban-app-prod`, so a pending approval doesn't hold the
   group and never blocks `auto-destroy.yml` or `destroy.yml`.

No build step anywhere in the workflow. The CloudFormation template
comes from `main` at promote time, not from the image's commit.

### `destroy.yml`

Input `environment` (`dev`/`prod`), environment of the same name,
concurrency group `deploy-kanban-app-<env>`; checkout, credentials,
`destroy-env`. The current `destroy` job of `deploy.yml`, moved.

### `.github/actions/deploy-env` (new composite action)

Today's deploy steps from `deploy.yml`, moved: resolve the ECR URI,
`cloudformation deploy`, DNS `UPSERT`, SSM compose push, health check.
Inputs: stack name, hostname, image tag, hosted zone ID, VPC ID, subnet
IDs, region, ECR repo name. Callers check out the repo and configure
credentials first, as with `destroy-env`. Step order stays the same
(DNS before the SSM push). `${{ }}` values used inside scripts are
passed through `env:`.

## GitHub settings (needs approval)

Create Environment `prod-approval`: deployment-branch rule `main`,
required reviewer `Lighfe`, `prevent_self_review: false` (solo repo).
`dev` and `prod` stay as they are.

## IAM

No template change. `deploy/verify-iam.sh` gets
`check allowed prod ecr:DescribeImages "$ECR"` (and the same for dev).

## Verification

Before merging:

- `actionlint` on the workflows (via its Docker image), `bash -n` on
  scripts, `deploy/verify-iam.sh` passes.
- After the move into `deploy-env`, diff the old `deploy` job against
  the action as a whole (step order, every `${{ }}` replaced), per the
  step-6 postmortem.
- Codex review, then triage.

Live, starting with both environments down (checked right before):

1. Merge the PR. CI passes on `main`, `deploy.yml` runs from
   `workflow_run`, creates dev from cold, and
   `https://dev.katban-…/api/health` returns 200. This is the
   "push to `main` deploys dev" check, from cold.
2. `promote.yml` with a tag that doesn't exist fails in `check`,
   without an approval request.
3. `promote.yml` with the tag from step 1: `check` passes, the run shows
   waiting for approval; after approving, prod comes up,
   `https://katban-…/api/health` returns 200, the run has no build
   step, and `docker ps` over SSM on the prod instance shows the
   `app` container on `…/kanban:<tag>`.
4. Merge the step-8 docs PR while dev is up: dev updates in place to
   the new commit (warm path), `docker ps` over SSM shows the new tag.
5. `destroy.yml` for dev and prod; the prod destroy runs without an
   approval request. `describe-stacks` shows neither app stack.

## Docs

- `deployment-plan.md`: step 8 (what was built and verified); step 6's
  "manual, not push-to-main" becomes history.
- `deploy/README.md`: one-time setup adds `prod-approval`; running
  section covers auto-deploy, promote, approval, `destroy.yml`.
- `README.md`: commands for promote and destroy.

## Open questions

- Docs-only pushes also deploy dev, and recreate it if down. No path
  filter now (`workflow_run` can't filter by path).
- GitHub keeps one pending run per concurrency group; a newer run
  replaces a pending one. With automatic deploys, a queued dev
  `destroy.yml` or auto-destroy job can be cancelled by a deploy queued
  behind it. The daily auto-destroy retries the next day; a cancelled
  manual destroy shows as cancelled.
- Promote doesn't check that a tag passed dev's health check; every
  tag in ECR came from a dev build, but a build whose deploy failed is
  still promotable.
- Template/image skew: promote uses `main`'s `stack.yaml` with an older
  image.
- ECR keeps the last 30 images. With a build on every green push, 30
  pushes while prod runs one tag would expire prod's image (a restart
  would then fail to pull).
