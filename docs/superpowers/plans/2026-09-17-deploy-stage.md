# Deploy (Stage 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task-by-task (not subagent-driven — this work is a
> single sequential thread against one live AWS account/stack, with state
> that must carry between tasks; splitting it across fresh subagents would
> lose that state and can't be tested in isolation).

**Goal:** Ship stage 6 of the deployment plan: a CloudFormation stack
(EC2 + RDS + IAM/OIDC), EC2 user-data running the app + Caddy containers,
and a GitHub Actions `deploy`/`destroy` workflow, verified with a real
deploy → health check → destroy cycle.

**Architecture:** Two CloudFormation templates. `deploy/cloudformation/bootstrap.yaml`
is applied once, by hand, by the account admin (via AdminAccess SSO) — it
creates the GitHub OIDC provider, the scoped deploy IAM role CI assumes, and
a persistent ECR repository (persistent because it must survive
stack create/destroy cycles and exist before the first image push).
`deploy/cloudformation/stack.yaml` is the ephemeral per-run stack: EC2,
RDS, security groups, an EC2 instance role, a Secrets Manager DB password,
and an SSM `SecureString` parameter holding the assembled
`KANBAN_DATABASE_URL`. EC2 user-data (inline in the template) installs
Docker, logs into ECR, fetches the DB URL from SSM at boot, and runs the
existing app image alongside a `caddy` container via Docker Compose, with
Caddy doing automatic Let's Encrypt TLS and reverse-proxying to the app's
internal port. `.github/workflows/deploy.yml` adds `workflow_dispatch`
with a `deploy`/`destroy` choice input: `deploy` builds+pushes the image,
runs `aws cloudformation deploy`, upserts the Route 53 A record, and polls
`/api/health`; `destroy` deletes the stack and the A record.

**Tech Stack:** AWS CloudFormation, EC2 (Amazon Linux 2023, arm64/t4g),
RDS Postgres 17, ECR, Secrets Manager, SSM Parameter Store, Route 53,
IAM/OIDC, Docker Compose, Caddy, GitHub Actions (`aws-actions/configure-aws-credentials`).

**Spec:** [_docs/deployment-plan.md](../../../_docs/deployment-plan.md)
(step 6) and [_docs/process.md](../../../_docs/process.md) (stage
workflow: finish, Codex review, commit).

## Global Constraints

- Domain: `katban-10x-cat-productivity.lighfe.dev`. `lighfe.dev` is
  registered with a Route 53 hosted zone already created in the AWS
  account (NS + SOA records present).
- No load balancer, no Elastic IP — DNS A record is updated to the new
  instance's public IP on every deploy.
- Exactly one uvicorn worker (existing app constraint, unrelated to this
  stage but not to be violated by any container/user-data change).
- `KANBAN_SECURE_COOKIES` must stay unset (default `true`) in the
  deployed environment — only Compose-for-local sets it to `false`.
- Secrets never hardcoded into the CFN template or GitHub: DB URL is
  assembled at EC2 boot from Secrets Manager (password) + SSM Parameter
  Store (the assembled URL), and GitHub Actions holds only the deploy
  role ARN.
- IAM role for CI is scoped to the specific actions this stack needs
  (cloudformation, scoped ec2/rds/iam/ssm/secretsmanager/ecr/route53
  actions), not `AdminAccess`.
- This repo's process: finish the stage, run a Codex review, commit —
  see [process.md](../../../_docs/process.md).

---

## File Structure

- `deploy/cloudformation/bootstrap.yaml` — one-time: OIDC provider, CI
  deploy role + policy, ECR repo. Applied manually by the admin, not by
  CI (chicken-and-egg: CI has no role until this exists).
- `deploy/cloudformation/stack.yaml` — the ephemeral app stack: SGs,
  RDS, Secrets Manager secret, EC2 instance role + profile, SSM
  parameter, EC2 instance with inline `UserData`. Outputs the instance's
  public IP.
- `.github/workflows/deploy.yml` — `workflow_dispatch` with a
  `deploy`/`destroy` choice input.
- `deploy/README.md` — one-time setup instructions for the admin
  (bootstrap parameters, GitHub secret to add) and how the two workflow
  actions work.
- `_docs/deployment-plan.md` — step 6 marked done with what was actually
  built, once verified.

## Task 1: Bootstrap template (OIDC provider, deploy role, ECR repo)

**Files:**
- Create: `deploy/cloudformation/bootstrap.yaml`
- Create: `deploy/README.md`

**Interfaces:**
- Produces: stack outputs `DeployRoleArn` (goes into the GitHub secret
  `AWS_DEPLOY_ROLE_ARN`) and `EcrRepositoryUri` (consumed by
  `deploy.yml` and referenced as a `stack.yaml` parameter).

- [ ] **Step 1: Write `bootstrap.yaml`**

Resources:
- `AWS::IAM::OIDCProvider` for `token.actions.githubusercontent.com`
  (thumbprint list per GitHub's documented root CA thumbprints;
  `ClientIdList: [sts.amazonaws.com]`). Guard with a `Condition` +
  parameter `CreateOidcProvider` (default `true`) so a re-run (or an
  account that already has the provider from another project) can skip
  it — only one OIDC provider per URL is allowed per account.
- `AWS::IAM::Role` `DeployRole`: trust policy allows
  `sts:AssumeRoleWithWebIdentity` from the OIDC provider, condition
  `token.actions.githubusercontent.com:sub` equals
  `repo:Lighfe/mini-kanban-board:ref:refs/heads/main` (manual dispatch
  only runs off a ref, main is the only branch this runs from) and
  `:aud` equals `sts.amazonaws.com`.
- Inline policy on `DeployRole`, scoped (no `AdminAccess`):
  - `cloudformation:*` on
    `arn:aws:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/kanban-app/*`
    (create/update/delete/describe the one named app stack only).
  - `ec2:*` — EC2 create/describe/delete actions mostly don't support
    resource-level restriction, so this is `Resource: "*"`; documented
    in a comment as the one unavoidably-broad grant, offset by the
    `cloudformation:*` scoping (the role can only ever act through the
    `kanban-app` stack's own resources in practice since it never calls
    `ec2:RunInstances` etc. directly — only `cloudformation deploy`
    does).
  - `rds:CreateDBInstance`, `DeleteDBInstance`, `DescribeDBInstances`,
    `CreateDBSubnetGroup`, `DeleteDBSubnetGroup`,
    `DescribeDBSubnetGroups`, `AddTagsToResource`, `ListTagsForResource`
    on `Resource: "*"` (RDS also lacks resource-level perms for
    subnet-group/instance creation prior to existing).
  - `iam:CreateRole`, `DeleteRole`, `GetRole`, `PutRolePolicy`,
    `DeleteRolePolicy`, `CreateInstanceProfile`,
    `DeleteInstanceProfile`, `AddRoleToInstanceProfile`,
    `RemoveRoleFromInstanceProfile`, `PassRole` scoped to
    `arn:aws:iam::${AWS::AccountId}:role/kanban-app-*` and
    `.../instance-profile/kanban-app-*`.
  - `ssm:PutParameter`, `DeleteParameter`, `GetParameter`,
    `AddTagsToResource` scoped to
    `arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:parameter/kanban-app/*`.
  - `secretsmanager:CreateSecret`, `DeleteSecret`, `DescribeSecret`,
    `TagResource`, `GetRandomPassword` scoped to
    `arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:kanban-app-*`
    (`GetRandomPassword` has no resource type, `Resource: "*"`).
  - `ecr:GetAuthorizationToken` (`Resource: "*"`, this action has no
    resource type) and `ecr:BatchCheckLayerAvailability`,
    `InitiateLayerUpload`, `UploadLayerPart`, `CompleteLayerUpload`,
    `PutImage`, `BatchGetImage`, `GetDownloadUrlForLayer`,
    `DescribeRepositories` scoped to the `kanban` ECR repo ARN.
  - `route53:ChangeResourceRecordSets`, `ListResourceRecordSets`
    scoped to the `lighfe.dev` hosted zone ARN (parameter
    `HostedZoneId`), `route53:GetHostedZone` same scope.
- `AWS::ECR::Repository` `kanban`, `ImageTagMutability: IMMUTABLE` is
  tempting but the workflow always deploys `latest`-per-run by digest
  via CFN parameter, so use `MUTABLE` with lifecycle policy keeping the
  last 5 images (avoids unbounded storage growth across repeated
  deploys).

Parameters: `GitHubRepo` (default `Lighfe/mini-kanban-board`),
`HostedZoneId` (no default — admin looks it up and passes it),
`CreateOidcProvider` (default `true`).

Outputs: `DeployRoleArn`, `EcrRepositoryUri`.

- [ ] **Step 2: Write `deploy/README.md`**

Document, as literal commands:
1. Look up the `lighfe.dev` hosted zone ID:
   `aws route53 list-hosted-zones-by-name --dns-name lighfe.dev.
   --query "HostedZones[0].Id" --output text`
2. Apply the bootstrap stack (one time, using the AdminAccess SSO
   profile):
   `aws cloudformation deploy --template-file
   deploy/cloudformation/bootstrap.yaml --stack-name kanban-bootstrap
   --parameter-overrides HostedZoneId=<id-from-step-1>
   --capabilities CAPABILITY_NAMED_IAM`
3. Read the outputs and add `AWS_DEPLOY_ROLE_ARN` as a GitHub Actions
   repo secret:
   `aws cloudformation describe-stacks --stack-name kanban-bootstrap
   --query "Stacks[0].Outputs"`
   `gh secret set AWS_DEPLOY_ROLE_ARN --body "<DeployRoleArn output>"`
4. Also record `HostedZoneId` and the two subnet IDs `stack.yaml` needs
   as GitHub Actions repo **variables** (not secrets, not sensitive):
   `gh variable set AWS_HOSTED_ZONE_ID --body "<id>"`
   `gh variable set AWS_VPC_ID --body "<default vpc id>"`
   `gh variable set AWS_SUBNET_IDS --body "<subnet-a>,<subnet-b>"`
   with the `aws ec2 describe-subnets
   --filters "Name=default-for-az,Values=true"` command to find them.
- [ ] **Step 3: Validate the template syntactically**

Run: `aws cloudformation validate-template --template-body
file://deploy/cloudformation/bootstrap.yaml`
Expected: no error (this only validates syntax, not IAM correctness —
step "Live verification" in Task 6 is the real test).

- [ ] **Step 4: Commit**

```bash
git add deploy/cloudformation/bootstrap.yaml deploy/README.md
git commit -m "deploy: add one-time OIDC bootstrap stack"
```

## Task 2: App stack template — networking, RDS, secrets

**Files:**
- Create: `deploy/cloudformation/stack.yaml`

**Interfaces:**
- Consumes: nothing from earlier tasks (parallel input: bootstrap's
  `EcrRepositoryUri`, passed as a CLI parameter override at deploy time,
  not a cross-stack reference — keeps the two stacks independently
  deployable).
- Produces: Outputs `InstancePublicIp`, `DBSecretArn`, used by Task 3
  (user-data references the SSM parameter name, not these outputs
  directly) and by `deploy.yml` (Task 4, for the DNS update).

- [ ] **Step 1: Write parameters and security groups in `stack.yaml`**

Parameters: `VpcId` (`AWS::EC2::VPC::Id`), `SubnetIds`
(`List<AWS::EC2::Subnet::Id>`, exactly 2 — RDS subnet groups require
≥2 AZs even for a single-AZ instance), `EcrRepositoryUri` (`String`),
`ImageTag` (`String`, the git SHA the workflow just pushed),
`SubdomainName` (`String`, default
`katban-10x-cat-productivity.lighfe.dev`), `AdminEmail` (`String`, for
Caddy's ACME registration — use `juliandralle@gmail.com`).

Resources:
- `Ec2SecurityGroup`: ingress 443/tcp and 80/tcp (80 needed for the
  Let's Encrypt HTTP-01 challenge Caddy performs) from `0.0.0.0/0`;
  egress all.
- `RdsSecurityGroup`: ingress 5432/tcp with `SourceSecurityGroupId: !Ref
  Ec2SecurityGroup` only; no egress rule needed beyond default.
- `DbSubnetGroup` (`AWS::RDS::DBSubnetGroup`) using `!Ref SubnetIds`.

- [ ] **Step 2: Add the DB secret, SSM parameter, and RDS instance**

- `DbSecret` (`AWS::SecretsManager::Secret`): `GenerateSecretString`
  with `SecretStringTemplate: '{"username":"kanban"}'`,
  `GenerateStringKey: password`, `ExcludeCharacters: '"@/\\'` (must
  exclude characters that break a Postgres connection URL).
- `DbInstance` (`AWS::RDS::DBInstance`): `Engine: postgres`,
  `EngineVersion: '17'`, `DBInstanceClass: db.t4g.micro`,
  `AllocatedStorage: '20'`, `StorageType: gp3`, `MultiAZ: false`,
  `PubliclyAccessible: false`, `DBName: kanban`, `MasterUsername:
  kanban`, `MasterUserPassword: !Sub
  '{{resolve:secretsmanager:${DbSecret}:SecretString:password}}'`,
  `DBSubnetGroupName: !Ref DbSubnetGroup`, `VPCSecurityGroups: [!Ref
  RdsSecurityGroup]`, `DeletionProtection: false` (must delete cleanly
  on `delete-stack`, per the ephemeral design).
- `DatabaseUrlParameter` (`AWS::SSM::Parameter`, `Type: SecureString`,
  `Name: !Sub '/kanban-app/${AWS::StackName}/database-url'`, `Value:
  !Sub 'postgresql+psycopg://kanban:{{resolve:secretsmanager:${DbSecret}:SecretString:password}}@${DbInstance.Endpoint.Address}:5432/kanban'`).
  CloudFormation resolves the nested secretsmanager dynamic reference
  when the parameter value is set, so the plaintext URL is never in the
  template or in CFN's change-set diff output.

- [ ] **Step 3: Validate**

Run: `aws cloudformation validate-template --template-body
file://deploy/cloudformation/stack.yaml`
Expected: no error. (Full semantic validation happens in Task 6's live
deploy — CFN parameter cross-references like `!GetAtt
DbInstance.Endpoint.Address` can't be checked without a real deploy.)

- [ ] **Step 4: Commit**

```bash
git add deploy/cloudformation/stack.yaml
git commit -m "deploy: add app stack networking, RDS, and secrets"
```

## Task 3: App stack template — EC2 instance role and user-data

**Files:**
- Modify: `deploy/cloudformation/stack.yaml`

**Interfaces:**
- Consumes: `DbInstance`, `RdsSecurityGroup`, `Ec2SecurityGroup`,
  `EcrRepositoryUri`, `ImageTag`, `SubdomainName`, `AdminEmail` from
  Task 2.
- Produces: `Ec2Instance`, Outputs `InstancePublicIp` (consumed by
  `deploy.yml` in Task 4).

- [ ] **Step 1: Add the EC2 instance role**

`Ec2InstanceRole` (`AWS::IAM::Role`), trust policy for
`ec2.amazonaws.com`. Inline policy:
- `ecr:GetAuthorizationToken` (`Resource: "*"`, required — this action
  has no resource type) plus `ecr:BatchGetImage`,
  `GetDownloadUrlForLayer`, `BatchCheckLayerAvailability` scoped to the
  ECR repo ARN built from `EcrRepositoryUri`.
- `ssm:GetParameter` scoped to
  `arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:parameter/kanban-app/${AWS::StackName}/database-url`.
- `kms:Decrypt` on `Resource: "*"` with `Condition: {StringEquals:
  {kms:ViaService: !Sub 'ssm.${AWS::Region}.amazonaws.com'}}` (needed
  to decrypt the `SecureString` — the default `aws/ssm` key has no
  resource-level ARN CFN can reference directly, so this is scoped by
  the `ViaService` condition instead).

`Ec2InstanceProfile` (`AWS::IAM::InstanceProfile`) wrapping the role.

- [ ] **Step 2: Add the EC2 instance with inline `UserData`**

`Ec2Instance` (`AWS::EC2::Instance`): `InstanceType: t4g.micro`,
`ImageId` via the SSM public parameter type
`{{resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64}}`,
`IamInstanceProfile: !Ref Ec2InstanceProfile`, `SecurityGroupIds: [!Ref
Ec2SecurityGroup]`, `SubnetId: !Select [0, !Ref SubnetIds]`.

`UserData` (base64-encoded `Fn::Sub` bash script):

```bash
#!/bin/bash
set -euo pipefail
dnf install -y docker
systemctl enable --now docker
curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64 \
  -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

DB_URL=$(aws ssm get-parameter --region ${AWS::Region} \
  --name /kanban-app/${AWS::StackName}/database-url \
  --with-decryption --query Parameter.Value --output text)

aws ecr get-login-password --region ${AWS::Region} | \
  docker login --username AWS --password-stdin ${EcrRepositoryUri}

mkdir -p /opt/kanban
cat > /opt/kanban/docker-compose.yml <<COMPOSE
services:
  app:
    image: ${EcrRepositoryUri}:${ImageTag}
    environment:
      KANBAN_DATABASE_URL: "$${DB_URL}"
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
${SubdomainName} {
  reverse_proxy app:8000
  tls ${AdminEmail}
}
CADDY

cd /opt/kanban && docker-compose up -d
```

(`$${DB_URL}` — the doubled `$` is required: `Fn::Sub` treats a single
`${...}` as a CFN substitution, so a literal shell variable reference
must escape it. `DB_URL` itself is fetched fresh in-instance via the
IAM role, never passed as a template parameter, so it's not embedded in
the CFN template or in EC2 console/metadata history beyond the running
`UserData` script, which is standard practice for this pattern.)

- [ ] **Step 3: Add Outputs**

```yaml
Outputs:
  InstancePublicIp:
    Value: !GetAtt Ec2Instance.PublicIp
  DBSecretArn:
    Value: !Ref DbSecret
```

- [ ] **Step 4: Validate**

Run: `aws cloudformation validate-template --template-body
file://deploy/cloudformation/stack.yaml`
Expected: no error.

- [ ] **Step 5: Commit**

```bash
git add deploy/cloudformation/stack.yaml
git commit -m "deploy: add EC2 instance role and user-data (app + Caddy)"
```

## Task 4: GitHub Actions deploy workflow

**Files:**
- Create: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: repo secret `AWS_DEPLOY_ROLE_ARN`, repo variables
  `AWS_HOSTED_ZONE_ID`, `AWS_VPC_ID`, `AWS_SUBNET_IDS` (all set in Task
  1's README steps), stack Output `InstancePublicIp` (Task 3).

- [ ] **Step 1: Write `deploy.yml`**

```yaml
name: Deploy

on:
  workflow_dispatch:
    inputs:
      action:
        description: "deploy or destroy"
        required: true
        type: choice
        options: [deploy, destroy]

permissions:
  id-token: write
  contents: read

env:
  AWS_REGION: eu-central-1
  STACK_NAME: kanban-app
  ECR_REPOSITORY: kanban
  SUBDOMAIN: katban-10x-cat-productivity.lighfe.dev

jobs:
  deploy:
    if: inputs.action == 'deploy'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: true

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Log in to ECR
        run: |
          aws ecr get-login-password --region ${{ env.AWS_REGION }} | \
            docker login --username AWS --password-stdin \
            ${{ steps.ecr-uri.outputs.uri }}
        id: ecr-login

      - name: Resolve ECR repository URI
        id: ecr-uri
        run: |
          uri=$(aws ecr describe-repositories --repository-names ${{ env.ECR_REPOSITORY }} \
            --query "repositories[0].repositoryUri" --output text)
          echo "uri=$uri" >> "$GITHUB_OUTPUT"

      - name: Build and push image
        run: |
          docker build -t ${{ steps.ecr-uri.outputs.uri }}:${{ github.sha }} .
          docker push ${{ steps.ecr-uri.outputs.uri }}:${{ github.sha }}

      - name: Deploy CloudFormation stack
        run: |
          aws cloudformation deploy \
            --template-file deploy/cloudformation/stack.yaml \
            --stack-name ${{ env.STACK_NAME }} \
            --capabilities CAPABILITY_NAMED_IAM \
            --parameter-overrides \
              VpcId=${{ vars.AWS_VPC_ID }} \
              SubnetIds=${{ vars.AWS_SUBNET_IDS }} \
              EcrRepositoryUri=${{ steps.ecr-uri.outputs.uri }} \
              ImageTag=${{ github.sha }} \
              SubdomainName=${{ env.SUBDOMAIN }}

      - name: Update DNS A record
        run: |
          ip=$(aws cloudformation describe-stacks --stack-name ${{ env.STACK_NAME }} \
            --query "Stacks[0].Outputs[?OutputKey=='InstancePublicIp'].OutputValue" \
            --output text)
          cat > /tmp/change-batch.json <<JSON
          {
            "Changes": [{
              "Action": "UPSERT",
              "ResourceRecordSet": {
                "Name": "${{ env.SUBDOMAIN }}",
                "Type": "A",
                "TTL": 60,
                "ResourceRecords": [{"Value": "$ip"}]
              }
            }]
          }
          JSON
          aws route53 change-resource-record-sets \
            --hosted-zone-id ${{ vars.AWS_HOSTED_ZONE_ID }} \
            --change-batch file:///tmp/change-batch.json

      - name: Wait for health check
        run: |
          for i in $(seq 1 60); do
            code=$(curl -s -o /dev/null -w "%{http_code}" \
              https://${{ env.SUBDOMAIN }}/api/health || true)
            if [ "$code" = "200" ]; then
              echo "Healthy"
              exit 0
            fi
            echo "Attempt $i: got $code, retrying in 10s"
            sleep 10
          done
          echo "App did not become healthy in time" >&2
          exit 1

  destroy:
    if: inputs.action == 'destroy'
    runs-on: ubuntu-latest
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Delete DNS A record
        run: |
          ip=$(aws cloudformation describe-stacks --stack-name ${{ env.STACK_NAME }} \
            --query "Stacks[0].Outputs[?OutputKey=='InstancePublicIp'].OutputValue" \
            --output text 2>/dev/null || true)
          if [ -n "$ip" ] && [ "$ip" != "None" ]; then
            cat > /tmp/change-batch.json <<JSON
          {
            "Changes": [{
              "Action": "DELETE",
              "ResourceRecordSet": {
                "Name": "${{ env.SUBDOMAIN }}",
                "Type": "A",
                "TTL": 60,
                "ResourceRecords": [{"Value": "$ip"}]
              }
            }]
          }
          JSON
            aws route53 change-resource-record-sets \
              --hosted-zone-id ${{ vars.AWS_HOSTED_ZONE_ID }} \
              --change-batch file:///tmp/change-batch.json || true
          fi

      - name: Delete stack
        run: |
          aws cloudformation delete-stack --stack-name ${{ env.STACK_NAME }}
          aws cloudformation wait stack-delete-complete --stack-name ${{ env.STACK_NAME }}
```

- [ ] **Step 2: Lint the workflow YAML**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy.yml'))"`
Expected: no error (catches YAML syntax mistakes before pushing;
`actionlint` if available is a better check — use it if installed,
fall back to the Python check otherwise).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "deploy: add deploy/destroy GitHub Actions workflow"
```

## Task 5: One-time account setup (you run this)

This is manual work for the account admin (AdminAccess SSO), not
something CI or this plan's automation does — it only needs to happen
once, ever, per AWS account.

- [ ] Run the 4 steps in `deploy/README.md` (Task 1): look up the
  hosted zone ID, deploy `bootstrap.yaml`, set the
  `AWS_DEPLOY_ROLE_ARN` secret and the three `AWS_*` repo variables.
- [ ] Confirm `aws sts get-caller-identity` works locally under the
  AdminAccess profile before running the bootstrap deploy (already
  confirmed missing at plan start — this step needs your AWS SSO
  login: `aws sso login`).

## Task 6: Live verification and stage close-out

**Files:**
- Modify: `_docs/deployment-plan.md` (mark step 6 done, record what was
  actually built, same style as steps 1-5).

- [ ] **Step 1: Trigger `deploy`**

Run: `gh workflow run deploy.yml -f action=deploy` then watch it:
`gh run watch`.
Expected: workflow succeeds, ending on a 200 from the health check.

- [ ] **Step 2: Manually confirm HTTPS end-to-end**

Run: `curl -sf https://katban-10x-cat-productivity.lighfe.dev/api/health`
and open the URL in a browser to confirm the frontend loads (not just
the API) and the TLS certificate is valid (not self-signed/expired).
Expected: JSON health response over HTTPS with a valid Let's Encrypt
cert; the SPA shell loads in-browser.

- [ ] **Step 3: Trigger `destroy`**

Run: `gh workflow run deploy.yml -f action=destroy` then `gh run watch`.
Expected: workflow succeeds.

- [ ] **Step 4: Confirm the stack is gone**

Run: `aws cloudformation describe-stacks --stack-name kanban-app`
Expected: `ValidationError: Stack ... does not exist` (or
`DELETE_COMPLETE` if it hasn't been fully removed from the API yet —
re-check after a minute).

- [ ] **Step 5: Update `_docs/deployment-plan.md`**

Mark "### 6. Deploy" as "— done", following the existing steps'
style: what was built, how it was verified (the deploy→health→destroy
cycle from Steps 1-4), and any deviations from the plan discovered
along the way (e.g. actual region used, actual IAM scoping tradeoffs).

- [ ] **Step 6: Commit**

```bash
git add _docs/deployment-plan.md
git commit -m "docs: mark deployment-plan step 6 (deploy) done"
```

## Task 7: Codex review

Per [process.md](../../../_docs/process.md): run a Codex review of the
whole stage's diff before considering it done, and address anything it
flags (particularly worth a close look: the IAM policy scoping in
`bootstrap.yaml` and the `Ec2InstanceRole` policy in `stack.yaml` —
least-privilege mistakes are the highest-cost category of bug here).

---

## Self-Review Notes

- **Spec coverage:** CloudFormation stack (EC2+RDS+SGs+IAM/OIDC) → Tasks
  1-3. User-data running app+Caddy → Task 3. `workflow_dispatch`
  deploy/destroy → Task 4. DNS update + health poll → Task 4. Secrets
  from CFN output/SSM, not hardcoded → Task 2 (SSM `SecureString`) +
  Task 3 (fetched at boot, never templated). GitHub secrets hold only
  the role ARN → Task 4 (only `AWS_DEPLOY_ROLE_ARN` is a secret; the
  rest are non-secret repo variables). Live deploy/destroy verification
  → Task 6.
- **Open judgment call:** `AWS_REGION` is set to `eu-central-1` in
  `deploy.yml` — pick the region your Route 53 zone's typical latency
  target and existing AWS setup (if any) favor; change the one `env:`
  line if a different region is wanted. Nothing else in the plan is
  region-specific.
