# Dev Auto-Deploy and Promote to Prod Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> (recommended) or superpowers:subagent-driven-development to implement this
> plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every commit on `main` that passes CI is built once and
deployed to dev (recreating dev if down); a manual, approval-gated
`promote.yml` deploys an existing image tag to prod without rebuilding.

**Architecture:** The deploy steps of today's `deploy.yml` move into a
composite action `.github/actions/deploy-env` (same pattern as
`destroy-env`). `deploy.yml` becomes dev-only, triggered by
`workflow_run` on CI and by manual dispatch. New `promote.yml`
(check tag → approval gate in Environment `prod-approval` → deploy) and
`destroy.yml` (destroy moved out of `deploy.yml`). No AWS resource
changes.

**Tech Stack:** GitHub Actions (`workflow_run`, Environments with
required reviewers, composite actions, concurrency), AWS CLI (ECR,
CloudFormation, Route 53, SSM), bash.

**Spec:** [docs/superpowers/specs/2026-09-30-dev-autodeploy-promote-design.md](../specs/2026-09-30-dev-autodeploy-promote-design.md).
Also read [docs/archive/deploy-postmortem.md](../../archive/deploy-postmortem.md)
before Task 1 (moving order-sensitive steps) and Task 6 (live runs).

## Global Constraints

- Region `eu-central-1`, ECR repo `kanban`, stacks `kanban-app-dev` / `kanban-app-prod`.
- Image tag format `YYYYMMDD-HHMMSS-shortsha` (UTC), regex `^[0-9]{8}-[0-9]{6}-[0-9a-f]{7}$`.
- Concurrency groups `deploy-kanban-app-dev` / `deploy-kanban-app-prod`, `cancel-in-progress: false`, shared with `auto-destroy.yml`.
- Dev deploys build from the CI run's `head_sha`, never `GITHUB_SHA`, on `workflow_run`.
- `workflow_run` deploys only if `conclusion == 'success'`, `event == 'push'`, `head_repository.full_name == github.repository`.
- Prod is never built for; `promote.yml` has no build step.
- DNS `UPSERT` runs before the SSM compose push (step-6 postmortem).
- AWS: read-only commands freely; every create/change/delete needs the user's approval for that exact command. GitHub repo/Environment settings changes need approval too. AWS via `scripts/with-secrets AWS_PROFILE -- aws ...`; never read the secrets file.
- The user approves prod promotions themselves in the GitHub UI; the agent never approves a pending deployment.
- Docs terse and factual.

## Review Focus

1. A CI run on a PR (including a fork PR from a branch named `main`) completes → no dev deploy (guard in `deploy.yml`'s `if`; Task 2 static check).
2. CI fails or is cancelled on `main` → no dev deploy (same guard; Task 2 static check).
3. `image_tag` with shell metacharacters or a wrong format → `check` fails before any AWS call or approval; the value only reaches shells through `env:` (Task 3 local regex test).
4. `image_tag` that is well-formed but not in ECR → `check` fails before the approval request (Task 6 live step 2).
5. A promote waiting for approval → doesn't hold `deploy-kanban-app-prod`, so auto-destroy/destroy of prod still run (job-level concurrency on `deploy` only; Task 3 static check).

---

## File map

| File | Change |
| --- | --- |
| `.github/actions/deploy-env/action.yml` | new: deploy steps moved from `deploy.yml` |
| `.github/workflows/deploy.yml` | rewrite: dev only, `workflow_run` + dispatch, uses `deploy-env` |
| `.github/workflows/destroy.yml` | new: destroy job moved from `deploy.yml` |
| `.github/workflows/promote.yml` | new: check → approve → deploy prod |
| `.github/workflows/auto-destroy.yml` | comment only (which workflows share the groups) |
| `deploy/verify-iam.sh` | two `ecr:DescribeImages` checks |
| `deploy/README.md`, `README.md` | how to run; `prod-approval` setup |
| `docs/deployment-plan.md` | step 8 (Task 6, after verification) |

Linting (no local installs): `actionlint` through Docker, which also
runs shellcheck on `run:` blocks:

```bash
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest -color
```

---

### Task 1: Move the deploy steps into `deploy-env` (pure refactor)

`deploy.yml` keeps its current triggers and inputs in this task; only
its `deploy` job's AWS steps move into the action. Behavior must not
change.

**Files:**
- Create: `.github/actions/deploy-env/action.yml`
- Modify: `.github/workflows/deploy.yml:95-269` (the `deploy` job's steps after `Check environment config`)

**Interfaces:**
- Produces: composite action `./.github/actions/deploy-env` with inputs
  `stack-name`, `hostname`, `image-tag`, `hosted-zone-id`, `vpc-id`,
  `subnet-ids`, `region`, `ecr-repository` (all required strings).
  Callers run `actions/checkout@v4` and
  `aws-actions/configure-aws-credentials@v4` first.

- [ ] **Step 1: Render today's SSM script as the reference**

The SSM script is where escaping is easiest to break (runner-side `$x`
vs instance-side `\$x`). Render the current version with fixed fake
values, before any edit:

```bash
S=/tmp/claude-1000/-home-julian-tuberlin-projects-ai-dev-tools-zoomcamp-mini-kanban-board/271a6a41-5ba6-44c5-b66c-78d47d2dd192/scratchpad
git show main:.github/workflows/deploy.yml \
  | awk '/cat > \/tmp\/update-app.sh <<SCRIPT/{f=1} f{print} f&&/^ *SCRIPT$/{f=0}' \
  | sed -E 's/^ {10}//' \
  | sed -e 's#/tmp/update-app.sh#/dev/stdout#' \
        -e 's#\${{ env.AWS_REGION }}#eu-central-1#g' \
        -e 's#\${{ steps.ecr.outputs.uri }}#123.dkr.ecr/kanban#g' \
        -e 's#\${{ env.IMAGE_TAG }}#20260930-120000-abcdef0#g' \
        -e 's#\${{ env.SUBDOMAIN }}#dev.example.test#g' > "$S/old-gen.sh"
db_secret_arn=arn:secret db_endpoint=db.host bash "$S/old-gen.sh" > "$S/old-rendered.sh"
cat "$S/old-rendered.sh"
```

Expected: a script starting `#!/bin/bash`, with `\$(aws secretsmanager`
rendered as `$(aws secretsmanager`, `--secret-id arn:secret`,
`image: 123.dkr.ecr/kanban:20260930-120000-abcdef0`, and a Caddyfile
block for `dev.example.test`.

- [ ] **Step 2: Create the action**

`.github/actions/deploy-env/action.yml`:

```yaml
name: Deploy environment
description: >
  Deploys an existing image tag to an environment: the app
  CloudFormation stack, the DNS A record, the compose config pushed
  over SSM, then waits for /api/health. Callers check out the repo and
  configure AWS credentials first. Used by deploy.yml (dev) and
  promote.yml (prod).
inputs:
  stack-name:
    description: App stack name, e.g. kanban-app-dev.
    required: true
  hostname:
    description: The environment's hostname (no trailing dot).
    required: true
  image-tag:
    description: Tag of an image already in the ECR repo.
    required: true
  hosted-zone-id:
    description: Route 53 hosted zone ID.
    required: true
  vpc-id:
    description: VPC for the stack.
    required: true
  subnet-ids:
    description: Two comma-separated subnets in different AZs.
    required: true
  region:
    description: AWS region.
    required: true
  ecr-repository:
    description: ECR repository name.
    required: true
runs:
  using: composite
  steps:
    - name: Check inputs
      shell: bash
      env:
        APP_HOSTNAME: ${{ inputs.hostname }}
        IMAGE_TAG: ${{ inputs.image-tag }}
      run: |
        if [ -z "$APP_HOSTNAME" ] || [ -z "$IMAGE_TAG" ]; then
          echo "::error::hostname (SUBDOMAIN on the GitHub Environment) or image tag is empty"
          exit 1
        fi

    - name: Resolve ECR repository URI
      id: ecr
      shell: bash
      env:
        ECR_REPOSITORY: ${{ inputs.ecr-repository }}
      run: |
        uri=$(aws ecr describe-repositories --repository-names "$ECR_REPOSITORY" \
          --query "repositories[0].repositoryUri" --output text)
        echo "uri=$uri" >> "$GITHUB_OUTPUT"

    - name: Deploy CloudFormation stack
      shell: bash
      env:
        STACK_NAME: ${{ inputs.stack-name }}
        VPC_ID: ${{ inputs.vpc-id }}
        SUBNET_IDS: ${{ inputs.subnet-ids }}
        ECR_URI: ${{ steps.ecr.outputs.uri }}
        IMAGE_TAG: ${{ inputs.image-tag }}
        APP_HOSTNAME: ${{ inputs.hostname }}
      run: |
        aws cloudformation deploy \
          --template-file deploy/cloudformation/stack.yaml \
          --stack-name "$STACK_NAME" \
          --capabilities CAPABILITY_NAMED_IAM \
          --parameter-overrides \
            VpcId="$VPC_ID" \
            SubnetIds="$SUBNET_IDS" \
            EcrRepositoryUri="$ECR_URI" \
            ImageTag="$IMAGE_TAG" \
            SubdomainName="$APP_HOSTNAME"

    - name: Update DNS A record
      # Before starting/restarting the containers: Caddy requests its
      # TLS cert as soon as it starts, and Let's Encrypt/ZeroSSL both
      # do a live DNS lookup as part of that - if the A record isn't
      # in place yet, the lookup gives NXDOMAIN, the attempt fails,
      # and Caddy doesn't retry again for a while (a live deploy
      # served TLS "internal error" for several minutes because of
      # this ordering).
      shell: bash
      env:
        STACK_NAME: ${{ inputs.stack-name }}
        APP_HOSTNAME: ${{ inputs.hostname }}
        ZONE_ID: ${{ inputs.hosted-zone-id }}
      run: |
        ip=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
          --query "Stacks[0].Outputs[?OutputKey=='InstancePublicIp'].OutputValue" \
          --output text)
        cat > /tmp/change-batch.json <<JSON
        {
          "Changes": [{
            "Action": "UPSERT",
            "ResourceRecordSet": {
              "Name": "$APP_HOSTNAME",
              "Type": "A",
              "TTL": 60,
              "ResourceRecords": [{"Value": "$ip"}]
            }
          }]
        }
        JSON
        aws route53 change-resource-record-sets \
          --hosted-zone-id "$ZONE_ID" \
          --change-batch file:///tmp/change-batch.json

    - name: Push app update via SSM
      # EC2 UserData only runs once at first boot - it does not
      # re-run on the stop/modify/start cycle CloudFormation uses for
      # a UserData-only update (e.g. just a new ImageTag on an
      # existing instance), so every deploy re-applies the compose
      # config here instead of relying on UserData for anything past
      # first-boot bring-up.
      shell: bash
      env:
        STACK_NAME: ${{ inputs.stack-name }}
        APP_HOSTNAME: ${{ inputs.hostname }}
        REGION: ${{ inputs.region }}
        ECR_URI: ${{ steps.ecr.outputs.uri }}
        IMAGE_TAG: ${{ inputs.image-tag }}
      run: |
        instance_id=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
          --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)
        db_secret_arn=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
          --query "Stacks[0].Outputs[?OutputKey=='DBSecretArn'].OutputValue" --output text)
        db_endpoint=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
          --query "Stacks[0].Outputs[?OutputKey=='DBEndpointAddress'].OutputValue" --output text)

        for i in $(seq 1 24); do
          status=$(aws ssm describe-instance-information \
            --filters "Key=InstanceIds,Values=$instance_id" \
            --query "InstanceInformationList[0].PingStatus" --output text 2>/dev/null || true)
          if [ "$status" = "Online" ]; then
            echo "SSM agent online"
            break
          fi
          echo "SSM agent not online yet (got: $status), retrying in 5s"
          sleep 5
        done

        cat > /tmp/update-app.sh <<SCRIPT
        #!/bin/bash
        set -euo pipefail
        # On a brand new instance the SSM agent comes online, and
        # this command starts running, well before UserData finishes
        # - including UserData's own "docker-compose up -d" at the
        # end. Racing that with this script's own "docker-compose up
        # -d" hits "container name already in use" (both create the
        # same containers). cloud-init status --wait blocks until
        # every UserData stage is done; on a warm instance (a normal
        # redeploy) cloud-init already finished long ago, so this
        # returns immediately.
        cloud-init status --wait >/dev/null 2>&1 || true
        DB_PASSWORD=\$(aws secretsmanager get-secret-value \
          --region ${REGION} --secret-id $db_secret_arn \
          --query SecretString --output text \
          | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')
        DB_URL="postgresql+psycopg://kanban:\${DB_PASSWORD}@${db_endpoint}:5432/kanban"

        aws ecr get-login-password --region ${REGION} | \
          docker login --username AWS --password-stdin ${ECR_URI}

        mkdir -p /opt/kanban
        cat > /opt/kanban/docker-compose.yml <<COMPOSE
        services:
          app:
            image: ${ECR_URI}:${IMAGE_TAG}
            environment:
              KANBAN_DATABASE_URL: "\${DB_URL}"
            restart: unless-stopped
          caddy:
            image: caddy:2
            ports:
              - "80:80"
              - "443:443"
            volumes:
              - /opt/kanban/Caddyfile:/etc/caddy/Caddyfile
              - caddy_data:/data
            restart: unless-stopped
        volumes:
          caddy_data:
        COMPOSE

        cat > /opt/kanban/Caddyfile <<CADDY
        ${APP_HOSTNAME} {
          reverse_proxy app:8000
          tls juliandralle@gmail.com
        }
        CADDY

        cd /opt/kanban && docker-compose up -d
        SCRIPT

        script_b64=$(base64 -w0 /tmp/update-app.sh)
        cmd_id=$(aws ssm send-command \
          --instance-ids "$instance_id" \
          --document-name AWS-RunShellScript \
          --parameters "{\"commands\":[\"echo $script_b64 | base64 -d | bash\"]}" \
          --query "Command.CommandId" --output text)

        for i in $(seq 1 30); do
          status=$(aws ssm get-command-invocation \
            --command-id "$cmd_id" --instance-id "$instance_id" \
            --query Status --output text 2>/dev/null || echo Pending)
          case "$status" in
            Success) echo "App updated"; exit 0 ;;
            Failed|Cancelled|TimedOut)
              echo "SSM command $status" >&2
              aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$instance_id" \
                --query "{Stdout:StandardOutputContent,Stderr:StandardErrorContent}" >&2
              exit 1
              ;;
            *) echo "Attempt $i: $status, retrying in 5s"; sleep 5 ;;
          esac
        done
        echo "SSM command did not complete in time" >&2
        exit 1

    - name: Wait for health check
      shell: bash
      env:
        APP_HOSTNAME: ${{ inputs.hostname }}
      run: |
        for i in $(seq 1 60); do
          code=$(curl -s -o /dev/null -w "%{http_code}" \
            "https://$APP_HOSTNAME/api/health" || true)
          if [ "$code" = "200" ]; then
            echo "Healthy"
            exit 0
          fi
          echo "Attempt $i: got $code, retrying in 10s"
          sleep 10
        done
        echo "App did not become healthy in time" >&2
        exit 1
```

Not `HOSTNAME`: bash sets that variable itself.

- [ ] **Step 3: Diff the new SSM script against the reference**

```bash
S=/tmp/claude-1000/-home-julian-tuberlin-projects-ai-dev-tools-zoomcamp-mini-kanban-board/271a6a41-5ba6-44c5-b66c-78d47d2dd192/scratchpad
awk '/cat > \/tmp\/update-app.sh <<SCRIPT/{f=1} f{print} f&&/^ *SCRIPT$/{f=0}' .github/actions/deploy-env/action.yml \
  | sed -E 's/^ {8}//' | sed 's#/tmp/update-app.sh#/dev/stdout#' > "$S/new-gen.sh"
db_secret_arn=arn:secret db_endpoint=db.host REGION=eu-central-1 \
  ECR_URI=123.dkr.ecr/kanban IMAGE_TAG=20260930-120000-abcdef0 APP_HOSTNAME=dev.example.test \
  bash "$S/new-gen.sh" > "$S/new-rendered.sh"
diff "$S/old-rendered.sh" "$S/new-rendered.sh" && echo IDENTICAL
```

Expected: `IDENTICAL`. Any difference is a bug in Step 2, not in the
reference.

- [ ] **Step 4: Point `deploy.yml`'s deploy job at the action**

Replace everything in the `deploy` job from `- uses: actions/checkout@v4`
(line 95) through the end of `Wait for health check` (line 269) with:

```yaml
      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - uses: ./.github/actions/deploy-env
        with:
          stack-name: ${{ env.STACK_NAME }}
          hostname: ${{ env.SUBDOMAIN }}
          image-tag: ${{ env.IMAGE_TAG }}
          hosted-zone-id: ${{ vars.AWS_HOSTED_ZONE_ID }}
          vpc-id: ${{ vars.AWS_VPC_ID }}
          subnet-ids: ${{ vars.AWS_SUBNET_IDS }}
          region: ${{ env.AWS_REGION }}
          ecr-repository: ${{ env.ECR_REPOSITORY }}
```

Keep the job's `Check environment config` step (Task 2 replaces the job).

- [ ] **Step 5: Read the whole action and job against intent**

Open `action.yml` and `deploy.yml` in full (not just the hunk). Check:
step order is ECR URI → CloudFormation → DNS → SSM → health; no `${{`
remains inside any `run:` block of the action
(`grep -n '\${{' .github/actions/deploy-env/action.yml` shows only
`env:`/`with`-style lines); no step appears twice.

- [ ] **Step 6: Lint**

Run: `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest -color`
Expected: no output, exit 0.

- [ ] **Step 7: Commit**

```bash
git add .github/actions/deploy-env/action.yml .github/workflows/deploy.yml
git commit -m "ci: move deploy steps into a deploy-env composite action

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `deploy.yml` deploys dev on green CI; destroys move to `destroy.yml`

**Files:**
- Modify: `.github/workflows/deploy.yml` (whole file)
- Create: `.github/workflows/destroy.yml`
- Modify: `.github/workflows/auto-destroy.yml:31-32` (comment)

**Interfaces:**
- Consumes: `./.github/actions/deploy-env` (Task 1), `./.github/actions/destroy-env` (existing, inputs `stack-name`, `hostname`, `hosted-zone-id`).
- Produces: `deploy.yml` job `deploy` writes the image tag and a promote command to the job summary; `destroy.yml` dispatch input `environment` (`dev`/`prod`).

- [ ] **Step 1: Write `deploy.yml`**

Replace the whole file:

```yaml
name: Deploy dev

# Every commit on main that passes CI is built once and deployed to
# dev; a dev that is down gets recreated. Manual dispatch deploys
# main's tip (e.g. to bring dev back without a push). Prod only gets
# images through promote.yml; destroys are in destroy.yml. See
# docs/deployment-plan.md step 8.
on:
  workflow_run:
    workflows: [CI]
    types: [completed]
    branches: [main]
  workflow_dispatch:

permissions:
  id-token: write
  contents: read

# One run at a time - an overlapping deploy/destroy would race on the
# same stack and DNS record. destroy.yml and auto-destroy.yml join the
# same group.
concurrency:
  group: deploy-kanban-app-dev
  cancel-in-progress: false

env:
  AWS_REGION: eu-central-1
  ECR_REPOSITORY: kanban
  STACK_NAME: kanban-app-dev
  # The commit CI tested. On workflow_run, GITHUB_SHA is main's tip
  # when this run started, which can be a newer, untested commit.
  COMMIT_SHA: ${{ github.event_name == 'workflow_run' && github.event.workflow_run.head_sha || github.sha }}

jobs:
  build:
    # branches: [main] matches a PR's head branch too, including a fork
    # PR from a branch named main; only a green push to this repo's
    # main deploys.
    if: >-
      github.event_name == 'workflow_dispatch' ||
      (github.event.workflow_run.conclusion == 'success' &&
       github.event.workflow_run.event == 'push' &&
       github.event.workflow_run.head_repository.full_name == github.repository)
    runs-on: ubuntu-latest
    environment: dev
    outputs:
      image_tag: ${{ steps.tag.outputs.tag }}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ env.COMMIT_SHA }}
          submodules: true

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      # YYYYMMDD-HHMMSS-shortsha (UTC). Tags are immutable in ECR, so
      # every run needs a new one, even for the same commit.
      - name: Compute image tag
        id: tag
        run: echo "tag=$(date -u +%Y%m%d-%H%M%S)-${COMMIT_SHA::7}" >> "$GITHUB_OUTPUT"

      - name: Resolve ECR repository URI
        id: ecr
        run: |
          uri=$(aws ecr describe-repositories --repository-names "$ECR_REPOSITORY" \
            --query "repositories[0].repositoryUri" --output text)
          echo "uri=$uri" >> "$GITHUB_OUTPUT"

      - name: Log in to ECR
        run: |
          aws ecr get-login-password --region "$AWS_REGION" | \
            docker login --username AWS --password-stdin "${{ steps.ecr.outputs.uri }}"

      - name: Build and push image
        run: |
          image="${{ steps.ecr.outputs.uri }}:${{ steps.tag.outputs.tag }}"
          docker build -t "$image" .
          docker push "$image"

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: dev
    steps:
      # Same commit as the image, so dev's template matches its image.
      - uses: actions/checkout@v4
        with:
          ref: ${{ env.COMMIT_SHA }}

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - uses: ./.github/actions/deploy-env
        with:
          stack-name: ${{ env.STACK_NAME }}
          hostname: ${{ vars.SUBDOMAIN }}
          image-tag: ${{ needs.build.outputs.image_tag }}
          hosted-zone-id: ${{ vars.AWS_HOSTED_ZONE_ID }}
          vpc-id: ${{ vars.AWS_VPC_ID }}
          subnet-ids: ${{ vars.AWS_SUBNET_IDS }}
          region: ${{ env.AWS_REGION }}
          ecr-repository: ${{ env.ECR_REPOSITORY }}

      - name: Summary
        env:
          IMAGE_TAG: ${{ needs.build.outputs.image_tag }}
        run: |
          {
            echo "Deployed \`$IMAGE_TAG\` (commit \`${COMMIT_SHA::7}\`) to dev."
            echo
            echo "Promote to prod:"
            echo
            echo '```'
            echo "gh workflow run promote.yml -f image_tag=$IMAGE_TAG"
            echo '```'
          } >> "$GITHUB_STEP_SUMMARY"
```

- [ ] **Step 2: Write `destroy.yml`**

```yaml
name: Destroy

# Deletes one environment's DNS record and app stack. Database data is
# lost. No approval, also for prod: the approval gate is on promoting.
on:
  workflow_dispatch:
    inputs:
      environment:
        description: "dev or prod"
        required: true
        type: choice
        options: [dev, prod]

permissions:
  id-token: write
  contents: read

concurrency:
  group: deploy-kanban-app-${{ inputs.environment }}
  cancel-in-progress: false

jobs:
  destroy:
    runs-on: ubuntu-latest
    environment: ${{ inputs.environment }}
    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: eu-central-1

      - uses: ./.github/actions/destroy-env
        with:
          stack-name: kanban-app-${{ inputs.environment }}
          hostname: ${{ vars.SUBDOMAIN }}
          hosted-zone-id: ${{ vars.AWS_HOSTED_ZONE_ID }}
```

- [ ] **Step 3: Update the `auto-destroy.yml` comment**

Change lines 31-32 from:

```yaml
    # Same group as deploy.yml, held from the age check through the
    # deletion: never races a running deploy/destroy.
```

to:

```yaml
    # Same group as deploy.yml, promote.yml and destroy.yml, held from
    # the age check through the deletion: never races a running
    # deploy/destroy.
```

- [ ] **Step 4: Static checks for the trigger guard (Review Focus 1–2)**

```bash
grep -n "workflow_run.conclusion == 'success'" .github/workflows/deploy.yml
grep -n "workflow_run.event == 'push'" .github/workflows/deploy.yml
grep -n "head_repository.full_name == github.repository" .github/workflows/deploy.yml
grep -n "GITHUB_SHA\|github.sha" .github/workflows/deploy.yml
```

Expected: one hit each for the first three, all inside `build`'s `if`;
the last shows only the `COMMIT_SHA` line (the dispatch fallback).
`deploy` needs `build`, so a skipped `build` skips `deploy`.

- [ ] **Step 5: Lint**

Run: `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest -color`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/deploy.yml .github/workflows/destroy.yml .github/workflows/auto-destroy.yml
git commit -m "ci: deploy dev on every green CI run on main; move destroys to destroy.yml

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `promote.yml` and the IAM check

**Files:**
- Create: `.github/workflows/promote.yml`
- Modify: `deploy/verify-iam.sh:129-130`

**Interfaces:**
- Consumes: `./.github/actions/deploy-env` (Task 1); GitHub Environment `prod-approval` (created in Task 5).
- Produces: dispatch input `image_tag`; jobs `check` → `approve` → `deploy`.

- [ ] **Step 1: Test the tag regex locally (Review Focus 3)**

```bash
re='^[0-9]{8}-[0-9]{6}-[0-9a-f]{7}$'
for t in 20260930-120000-abcdef0 20260930-120000-ABCDEF0 '20260930-120000-abcdef0;id' \
         '$(id)' 20260930-120000-abcdef 20260930-1200-abcdef0 ''; do
  if [[ "$t" =~ $re ]]; then echo "accept [$t]"; else echo "reject [$t]"; fi
done
```

Expected: only `accept [20260930-120000-abcdef0]`; every other line `reject`.

- [ ] **Step 2: Write `promote.yml`**

```yaml
name: Promote to prod
run-name: Promote ${{ inputs.image_tag }} to prod

# Deploys an image tag that a dev deploy already built to prod. No
# build. Waits for approval in the prod-approval GitHub Environment.
# See docs/deployment-plan.md step 8.
on:
  workflow_dispatch:
    inputs:
      image_tag:
        description: "Image tag from a dev deploy (YYYYMMDD-HHMMSS-shortsha)"
        required: true
        type: string

permissions:
  id-token: write
  contents: read

env:
  AWS_REGION: eu-central-1
  ECR_REPOSITORY: kanban
  STACK_NAME: kanban-app-prod
  # Only ever used through env, never interpolated into a script.
  IMAGE_TAG: ${{ inputs.image_tag }}

jobs:
  # Fails before anyone is asked to approve.
  check:
    runs-on: ubuntu-latest
    environment: prod
    steps:
      - name: Check tag format
        run: |
          if ! [[ "$IMAGE_TAG" =~ ^[0-9]{8}-[0-9]{6}-[0-9a-f]{7}$ ]]; then
            echo "::error::image_tag must look like YYYYMMDD-HHMMSS-shortsha"
            exit 1
          fi

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Check the image exists
        run: |
          if ! pushed=$(aws ecr describe-images --repository-name "$ECR_REPOSITORY" \
              --image-ids imageTag="$IMAGE_TAG" \
              --query "imageDetails[0].imagePushedAt" --output text); then
            echo "::error::no image $IMAGE_TAG in ECR repo $ECR_REPOSITORY"
            exit 1
          fi
          echo "$IMAGE_TAG pushed $pushed" | tee -a "$GITHUB_STEP_SUMMARY"

  # prod-approval has a required reviewer and no secrets. A reviewer on
  # prod itself would also hold auto-destroy's and destroy.yml's prod
  # jobs.
  approve:
    needs: check
    runs-on: ubuntu-latest
    environment: prod-approval
    steps:
      - run: echo "Approved promoting $IMAGE_TAG to prod"

  deploy:
    needs: approve
    runs-on: ubuntu-latest
    environment: prod
    # Job-level, not workflow-level: a run waiting for approval must not
    # hold the group and block a prod destroy or auto-destroy.
    concurrency:
      group: deploy-kanban-app-prod
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - uses: ./.github/actions/deploy-env
        with:
          stack-name: ${{ env.STACK_NAME }}
          hostname: ${{ vars.SUBDOMAIN }}
          image-tag: ${{ env.IMAGE_TAG }}
          hosted-zone-id: ${{ vars.AWS_HOSTED_ZONE_ID }}
          vpc-id: ${{ vars.AWS_VPC_ID }}
          subnet-ids: ${{ vars.AWS_SUBNET_IDS }}
          region: ${{ env.AWS_REGION }}
          ecr-repository: ${{ env.ECR_REPOSITORY }}
```

- [ ] **Step 3: Static checks (Review Focus 3, 5)**

```bash
grep -n "inputs.image_tag" .github/workflows/promote.yml
grep -n "^concurrency:" .github/workflows/promote.yml
grep -n "docker build\|docker push" .github/workflows/promote.yml
```

Expected: `inputs.image_tag` only in `run-name` and the `IMAGE_TAG` env
line; no workflow-level `concurrency:`; no build/push.

- [ ] **Step 4: Add the IAM checks**

In `deploy/verify-iam.sh`, after `check denied  prod ecr:PutImage "$ECR"`:

```bash
check allowed dev  ecr:DescribeImages "$ECR"
check allowed prod ecr:DescribeImages "$ECR"
```

- [ ] **Step 5: Run the IAM check (read-only)**

Needs a valid SSO session (`aws sso login` by the user if expired).

Run: `scripts/with-secrets AWS_PROFILE -- deploy/verify-iam.sh`
Expected: last line `All checks passed`, including
`ok    prod allowed ecr:DescribeImages`.

- [ ] **Step 6: Lint**

Run: `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest -color && bash -n deploy/verify-iam.sh`
Expected: no output, exit 0.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/promote.yml deploy/verify-iam.sh
git commit -m "ci: approval-gated promote of an existing image tag to prod

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: How-to docs

**Files:**
- Modify: `deploy/README.md` (step 4 of one-time setup; "Running a deploy or a teardown")
- Modify: `README.md:69-86` ("Live deployment (AWS)")

- [ ] **Step 1: `deploy/README.md` one-time setup**

After the `gh variable set SUBDOMAIN --env prod ...` line of step 4, add
inside the same code block:

```bash
# Approval gate for promote.yml: required reviewer, no secrets.
jq -n --argjson id "$(gh api user --jq .id)" \
  '{deployment_branch_policy:{protected_branches:false,custom_branch_policies:true},
    reviewers:[{type:"User",id:$id}], prevent_self_review:false}' \
  | gh api -X PUT repos/Lighfe/mini-kanban-board/environments/prod-approval --input -
gh api -X POST repos/Lighfe/mini-kanban-board/environments/prod-approval/deployment-branch-policies \
  -f name=main -f type=branch
```

and change the step's heading sentence to: "Create the GitHub
Environments, limited to `main`, with each environment's role and
hostname, plus the `prod-approval` gate:".

- [ ] **Step 2: `deploy/README.md` running section**

Replace the section "Running a deploy or a teardown" (from its heading
up to, not including, "## Auto-destroy") with:

````markdown
## Deploying dev

Every commit on `main` that passes CI is deployed to dev by
`.github/workflows/deploy.yml`: it builds the image from that commit,
tags it `YYYYMMDD-HHMMSS-shortsha`, and runs `aws cloudformation deploy`
against `deploy/cloudformation/stack.yaml`, which creates dev if it is
down. It then points the hostname at the instance, pushes the compose
config over SSM, and polls `/api/health` until it returns 200. The run
summary shows the tag and the promote command.

To deploy `main`'s tip without a push (e.g. after auto-destroy):

```bash
gh workflow run deploy.yml
```

## Promoting to prod

```bash
gh workflow run promote.yml -f image_tag=<tag>
```

`promote.yml` checks the tag exists in ECR, then waits for approval in
the `prod-approval` Environment (Actions → the run → **Review
deployments**), then deploys that image to prod, creating prod if it is
down. Nothing is built.

## Destroying

```bash
gh workflow run destroy.yml -f environment=dev   # or prod
```

Deletes the environment's DNS record and its stack (EC2 + RDS).
Database data is lost. No approval, also for prod.

Dev and prod don't wait for each other; runs against the same
environment queue. GitHub keeps one pending run per environment: a
newer run replaces a pending one, so a queued destroy can be cancelled
by a deploy queued after it.
````

Also change the intro's "(steps 6 and 7)" to "(steps 6–8)".

- [ ] **Step 3: `README.md`**

Replace lines 71-79 (from "The app also runs on AWS" through "Use
`environment=prod` for production.") with:

```markdown
The app also runs on AWS (EC2 + RDS via CloudFormation) in two
independent environments, `dev` and `prod` — see
[deploy/README.md](deploy/README.md) for the full how-to. Every commit
on `main` that passes CI is deployed to dev; prod gets an image dev
already built, after an approval:

    gh workflow run promote.yml -f image_tag=<tag>      # dev's tag -> prod
    gh workflow run destroy.yml -f environment=dev      # tear dev down

```

Then read the rest of the section and fix any sentence that still
describes both environments as deployed by hand.

- [ ] **Step 4: Check links and stale references**

```bash
grep -rn "action=deploy\|action=destroy\|deploy.yml -f" README.md deploy/README.md docs/*.md
```

Expected: no hits (step 6/7 history in `deployment-plan.md` may keep
naming `deploy.yml`, not these commands).

- [ ] **Step 5: Commit**

```bash
git add deploy/README.md README.md
git commit -m "docs: how to run dev auto-deploy, promote and destroy

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Review, settings, merge

- [ ] **Step 1: Codex review**

Run a Codex review of the branch against `main` (default model
`gpt-6-astra`, no model override). Give it the spec path and the
postmortem path. Triage: fix what matters, list what's skipped and why.
If a fix changes the design, stop and show the user the uncommitted
diff first.

- [ ] **Step 2: Re-run the checks after any fixes**

actionlint, `bash -n deploy/verify-iam.sh`, Task 1 Step 3's render
diff if `action.yml` changed.

- [ ] **Step 3: Create `prod-approval` (user approval required)**

Show the user the exact commands from Task 4 Step 1 and run them only
after approval. Then check (read-only):

```bash
gh api repos/Lighfe/mini-kanban-board/environments/prod-approval \
  --jq '{name, protection_rules: [.protection_rules[] | {type, prevent_self_review, reviewers: [.reviewers[]?.reviewer.login]}]}'
```

Expected: rules `branch_policy` and `required_reviewers` with
reviewer `Lighfe`, `prevent_self_review: false`.

- [ ] **Step 4: Check both environments are down (read-only)**

```bash
scripts/with-secrets AWS_PROFILE -- aws cloudformation describe-stacks --region eu-central-1 \
  --stack-name kanban-app-dev --query "Stacks[0].StackStatus" --output text
scripts/with-secrets AWS_PROFILE -- aws cloudformation describe-stacks --region eu-central-1 \
  --stack-name kanban-app-prod --query "Stacks[0].StackStatus" --output text
```

Expected: `does not exist` for both. If one is up, ask the user before
continuing (the merge will redeploy dev).

- [ ] **Step 5: Push, PR, merge once CI is green**

Tell the user first: merging starts Task 6 live step 1 (dev is
created, ~10+ min, costs money until destroyed).

```bash
git push -u origin dev-autodeploy-promote
gh pr create --title "Dev auto-deploy and promote to prod" --body "<summary, spec link, test plan>

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
gh pr checks --watch
gh pr merge --squash --delete-branch
```

---

### Task 6: Live verification, step 8, teardown

Explain each live step to the user before running it; one approval
each. After a failure: diagnose from run logs, stack events and
`docker logs` over SSM; state the cause before changing anything.

- [ ] **Step 1: Push to `main` deploys dev from cold**

The merge from Task 5 is the push. Watch:

```bash
gh run list --workflow CI --branch main --limit 1
gh run list --workflow deploy.yml --limit 1   # event: workflow_run
gh run watch <deploy run id>
curl -s -o /dev/null -w "%{http_code}\n" https://dev.katban-10x-cat-productivity.lighfe.dev/api/health
```

Expected: the deploy run's event is `workflow_run`, it built from the
merge commit, `200`. Note the tag `T` from the run summary.

- [ ] **Step 2: Unknown tag fails before approval (Review Focus 4)**

```bash
gh workflow run promote.yml -f image_tag=20000101-000000-0000000
```

Expected: `check` fails at "Check the image exists"; `approve` and
`deploy` are skipped; no approval request.

- [ ] **Step 3: Promote `T`, user approves**

```bash
gh workflow run promote.yml -f image_tag=T
gh run list --workflow promote.yml --limit 1   # status: waiting
```

Expected: `check` passes, run waits on `prod-approval`. The user
approves in the GitHub UI. Then `deploy` runs; the run has no build
step; prod health returns 200:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://katban-10x-cat-productivity.lighfe.dev/api/health
```

- [ ] **Step 4: Prod serves `T` (SSM, user approval)**

```bash
P="scripts/with-secrets AWS_PROFILE --"
iid=$($P aws cloudformation describe-stacks --region eu-central-1 --stack-name kanban-app-prod \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)
cid=$($P aws ssm send-command --region eu-central-1 --instance-ids "$iid" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["docker ps --format \"{{.Names}} {{.Image}}\""]' \
  --query Command.CommandId --output text)
sleep 5
$P aws ssm get-command-invocation --region eu-central-1 --command-id "$cid" --instance-id "$iid" \
  --query StandardOutputContent --output text
```

Expected: the app container's image ends in `kanban:T`.

- [ ] **Step 5: Step 8 in `docs/deployment-plan.md`**

On a new branch `docs-step-8`: replace step 7's closing "Next: …"
paragraph with a pointer to step 8, and add after step 7:

```markdown
### 8. Dev auto-deploy and promote to prod — done

Every commit on `main` that passes CI is built once and deployed to
dev; prod gets an image dev already built, by hand, after an approval.
Design:
[docs/superpowers/specs/2026-09-30-dev-autodeploy-promote-design.md](superpowers/specs/2026-09-30-dev-autodeploy-promote-design.md).

- `deploy.yml` runs on `workflow_run` when CI succeeds for a push to
  `main` (and on manual dispatch), builds from the CI run's commit and
  deploys dev. A dev that is down is recreated, so while pushes happen
  dev is always up; auto-destroy still removes it after about 72–96
  hours.
- `promote.yml` takes an `image_tag`, checks it exists in ECR, waits
  for a required reviewer in the `prod-approval` GitHub Environment,
  then deploys it to prod. The approval sits on a separate Environment
  so prod destroys and auto-destroy don't wait for it.
- Destroys moved to `destroy.yml`. The deploy steps are a composite
  action, `.github/actions/deploy-env`, shared by both workflows.
- No AWS changes; `verify-iam.sh` also checks `ecr:DescribeImages`.

Verified <date>: <what the live steps actually showed, including run
IDs where useful, and anything that failed and was fixed>.
```

Fill the "Verified" paragraph with the real results from Steps 1–4 and
Step 6 below (write Step 6's result as it will be observed and correct
it if it differs). Also update step 6's "Deploy trigger: manual, not
push-to-main" paragraph with one sentence noting step 8 replaced it.
Keep the open questions from the spec in one short list. Commit, push,
PR.

- [ ] **Step 6: Merging the docs PR updates the warm dev**

Merge once CI is green (dev is still up). Expected: `deploy.yml` runs
from `workflow_run`, updates dev in place, health 200; `docker ps` over
SSM on the dev instance (same commands as Step 4 with
`kanban-app-dev`, user approval) shows the new tag. If the result
differs from what Step 5 wrote, fix the docs in a follow-up commit.

- [ ] **Step 7: Destroy both (unless the user says keep)**

```bash
gh workflow run destroy.yml -f environment=dev
gh workflow run destroy.yml -f environment=prod
```

Expected: the prod run doesn't wait for approval. Then (read-only):

```bash
scripts/with-secrets AWS_PROFILE -- aws cloudformation describe-stacks --region eu-central-1 --stack-name kanban-app-dev
scripts/with-secrets AWS_PROFILE -- aws cloudformation describe-stacks --region eu-central-1 --stack-name kanban-app-prod
```

Expected: `does not exist` for both.
