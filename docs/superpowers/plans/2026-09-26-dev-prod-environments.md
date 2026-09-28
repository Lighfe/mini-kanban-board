# Dev and Prod Environments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> (recommended) or superpowers:subagent-driven-development to implement this
> plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single ephemeral AWS deployment into two independent
environments, `dev` and `prod`, each with its own scoped deploy role,
plus instance-size IAM caps and a scheduled auto-destroy.

**Architecture:** A new per-environment CloudFormation template
(`deploy-role.yaml`, applied by hand twice) holds each environment's
deploy role and EC2 permissions boundary. `bootstrap.yaml` shrinks to the
shared OIDC provider and ECR repo (tag-immutable). `stack.yaml` derives
all names from its stack name (`kanban-app-dev` / `kanban-app-prod`).
`deploy.yml` gains an `environment` input and splits into a `build` job
(always in the dev GitHub Environment, the only role that can push) and a
`deploy` job in the target environment. A composite action holds the
destroy steps, shared by `deploy.yml` and a new daily `auto-destroy.yml`.

**Tech Stack:** AWS CloudFormation, IAM (condition keys, permissions
boundaries, policy simulator), EC2, RDS, Route 53, SSM, ECR, GitHub
Actions (Environments, OIDC, composite actions, concurrency), bash, jq.

**Spec:** [docs/superpowers/specs/2026-09-26-dev-prod-environments-design.md](../specs/2026-09-26-dev-prod-environments-design.md).
Also read [_docs/deploy-postmortem.md](../../../_docs/deploy-postmortem.md)
before Tasks 9–10: it is the list of ways the last live deploy went wrong.

## Global Constraints

- Region `eu-central-1`; hosted zone `lighfe.dev`, ID `Z0050563VZ2HM7AAD71F`
  (repo variable `AWS_HOSTED_ZONE_ID`).
- Hostnames: prod `katban-10x-cat-productivity.lighfe.dev`, dev
  `dev.katban-10x-cat-productivity.lighfe.dev`.
- Names: app stacks `kanban-app-<env>`; deploy-role stacks
  `kanban-deploy-<env>`; deploy roles `kanban-deploy-<env>`; boundaries
  `kanban-app-<env>-ec2-boundary`; instance roles `kanban-app-<env>-ec2`;
  DB secrets `kanban-app-<env>-db`. `<env>` is exactly `dev` or `prod`.
- Image tags: `YYYYMMDD-HHMMSS-shortsha` (UTC, 7-char SHA). ECR repo
  `kanban` is `IMMUTABLE`, lifecycle keeps the last 30 images.
- Size caps: EC2 `t3.micro`, RDS `db.t4g.micro`.
- Auto-destroy threshold 72 hours on the daily schedule.
- **AWS:** run as `scripts/with-secrets AWS_PROFILE -- aws ...`.
  Read-only commands (`describe-*`, `list-*`, `get-*`,
  `iam simulate-principal-policy`) are free to run. **Every command that
  creates, changes or deletes AWS resources (including
  `cloudformation deploy` and `ssm send-command`) needs the user's
  approval for that exact command, each time.** Never read or print
  `~/.config/mini-kanban-board/secrets.env`.
- Codex reviews use the default model (no `--model`).
- Every commit message ends with a blank line and
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (omitted from
  the commit commands below for brevity).
- Unchanged app constraints: one uvicorn worker; `KANBAN_SECURE_COOKIES`
  unset in AWS.

## Review Focus

1. A deploy dispatched from a branch other than `main` → rejected by the
   GitHub Environment's branch rule before any AWS credentials are
   issued (tested in Task 9 Step 7).
2. A GitHub Environment missing `SUBDOMAIN` → the deploy job fails with a
   clear message before touching AWS, not with a Caddy cert for an empty
   hostname (guard in Task 4, `Check environment config` step).
3. `max_age_hours` that isn't a whole number (`abc`, `-1`, `1.5`) →
   auto-destroy fails at validation, destroys nothing (tested in Task 10
   Step 8).
4. Destroy when the stack is already gone but the DNS record is still
   there (drift) → the record is still deleted, since the action reads
   Route 53 directly (Task 4 action design; checked in Task 10 Step 9).
5. Auto-destroy firing while a deploy runs on the same environment → it
   waits in the shared concurrency group rather than racing. GitHub keeps
   only one *pending* run per group, so a pending auto-destroy can be
   replaced by a newer deploy; the next daily run catches it. Review only.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `deploy/cloudformation/deploy-role.yaml` | create | per-env deploy role + EC2 boundary |
| `deploy/cloudformation/bootstrap.yaml` | modify (Task 2), shrink (Task 11) | shared OIDC provider + ECR repo |
| `deploy/cloudformation/stack.yaml` | modify | per-env app stack, names from stack name |
| `.github/actions/destroy-env/action.yml` | create | delete DNS record + stack |
| `.github/workflows/deploy.yml` | rewrite | build / deploy / destroy per env |
| `.github/workflows/auto-destroy.yml` | create | daily age check + destroy |
| `deploy/verify-iam.sh` | create | IAM simulator checks for both roles |
| `deploy/README.md`, `README.md`, `_docs/deployment-plan.md` | modify | docs |

Lint tools (none are installed globally):

```bash
uvx cfn-lint deploy/cloudformation/*.yaml
docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest -color
docker run --rm -v "$PWD":/mnt koalaman/shellcheck:stable deploy/verify-iam.sh
```

actionlint also checks the local composite action's inputs where
workflows use it, and runs shellcheck on workflow `run:` blocks.

---

### Task 1: Per-environment deploy role template

**Files:**
- Create: `deploy/cloudformation/deploy-role.yaml`

**Interfaces:**
- Produces: stacks `kanban-deploy-<env>` with outputs `DeployRoleArn`,
  `Ec2RoleBoundaryArn`; managed policy `kanban-app-<env>-ec2-boundary`
  (referenced by name from `stack.yaml` in Task 3); role
  `kanban-deploy-<env>` (its ARN becomes the `AWS_DEPLOY_ROLE_ARN`
  secret of GitHub Environment `<env>` in Task 9).

- [ ] **Step 1: Write the template**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: >
  Per-environment deploy access for mini-kanban-board: the IAM role GitHub
  Actions assumes to deploy/destroy the kanban-app-<env> stack, and the
  permissions boundary every kanban-app-<env> EC2 instance role must
  carry. Applied by hand with the admin profile, once per environment
  (stacks kanban-deploy-dev and kanban-deploy-prod) - see
  deploy/README.md. Each role can only touch its own environment.

Parameters:
  Environment:
    Type: String
    AllowedValues: [dev, prod]
    Description: >
      Environment name. dev and prod share no prefix, so kanban-app-dev-*
      patterns never match prod resources and vice versa.
  Hostname:
    Type: String
    Description: >
      The environment's public hostname, lowercase, no trailing dot. The
      only DNS record this role may change.
  HostedZoneId:
    Type: String
    Description: Route 53 hosted zone ID for lighfe.dev.
  GitHubOrg:
    Type: String
    Default: Lighfe
  GitHubRepoName:
    Type: String
    Default: mini-kanban-board
  EcrRepositoryName:
    Type: String
    Default: kanban
    Description: The shared ECR repo from the kanban-bootstrap stack.

Conditions:
  IsDev: !Equals [!Ref Environment, dev]

Resources:
  # Upper bound on what this environment's app EC2 role can do. The
  # deploy role may only create or change that role with this boundary
  # attached (IamGrantWithBoundary below), so it can't escalate through
  # the instance role and its SSM shell - neither to account admin nor
  # to the other environment's DB secret.
  Ec2RoleBoundary:
    Type: AWS::IAM::ManagedPolicy
    Properties:
      ManagedPolicyName: !Sub kanban-app-${Environment}-ec2-boundary
      PolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Sid: EcrPull
            Effect: Allow
            Action:
              - ecr:GetAuthorizationToken
              - ecr:BatchGetImage
              - ecr:GetDownloadUrlForLayer
              - ecr:BatchCheckLayerAvailability
            Resource: "*"
          - Sid: DbSecret
            Effect: Allow
            Action: secretsmanager:GetSecretValue
            Resource: !Sub arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:kanban-app-${Environment}-*
          # The actions of AmazonSSMManagedInstanceCore, which the EC2 role
          # attaches for SSM sessions and the deploy's RunCommand push.
          - Sid: SsmAgent
            Effect: Allow
            Action:
              - ssm:DescribeAssociation
              - ssm:GetDeployablePatchSnapshotForInstance
              - ssm:GetDocument
              - ssm:DescribeDocument
              - ssm:GetManifest
              - ssm:GetParameter
              - ssm:GetParameters
              - ssm:ListAssociations
              - ssm:ListInstanceAssociations
              - ssm:PutInventory
              - ssm:PutComplianceItems
              - ssm:PutConfigurePackageResult
              - ssm:UpdateAssociationStatus
              - ssm:UpdateInstanceAssociationStatus
              - ssm:UpdateInstanceInformation
              - ssmmessages:*
              - ec2messages:*
            Resource: "*"

  DeployRole:
    Type: AWS::IAM::Role
    Properties:
      # Not kanban-app-*: no role pattern below may match the deploy role
      # itself (self-escalation via PutRolePolicy on its own ARN).
      RoleName: !Sub kanban-deploy-${Environment}
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Federated: !Sub arn:aws:iam::${AWS::AccountId}:oidc-provider/token.actions.githubusercontent.com
            Action: sts:AssumeRoleWithWebIdentity
            Condition:
              StringEquals:
                token.actions.githubusercontent.com:aud: sts.amazonaws.com
              StringLike:
                # Jobs that declare `environment:` get an environment
                # subject instead of the branch ref; the main-only rule
                # lives on the GitHub Environment. The "@*" parts match
                # GitHub's numeric org/repo IDs in this repo's sub claim.
                token.actions.githubusercontent.com:sub: !Sub repo:${GitHubOrg}@*/${GitHubRepoName}@*:environment:${Environment}
      Policies:
        - PolicyName: !Sub kanban-deploy-${Environment}
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Sid: CloudFormation
                Effect: Allow
                Action: "cloudformation:*"
                Resource: !Sub arn:aws:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/kanban-app-${Environment}/*

              # No resource-level restriction possible, or nothing to
              # protect (user tags play no part in any condition here).
              - Sid: Ec2Unscoped
                Effect: Allow
                Action:
                  - ec2:DescribeInstances
                  - ec2:DescribeSecurityGroups
                  - ec2:DescribeSubnets
                  - ec2:DescribeVpcs
                  - ec2:DescribeImages
                  - ec2:DescribeNetworkInterfaces
                  - ec2:DescribeTags
                  - ec2:CreateSecurityGroup
                  - ec2:CreateTags
                  - ec2:DeleteTags
                Resource: "*"

              # RunInstances is authorized per resource it touches, so
              # it's split by resource type.
              - Sid: Ec2RunInstanceSize
                Effect: Allow
                Action: ec2:RunInstances
                Resource: !Sub arn:aws:ec2:${AWS::Region}:${AWS::AccountId}:instance/*
                Condition:
                  StringEquals:
                    ec2:InstanceType: t3.micro
              # Only this environment's security groups: prod's RDS
              # trusts prod's EC2 security group.
              - Sid: Ec2RunInstanceSecurityGroup
                Effect: Allow
                Action: ec2:RunInstances
                Resource: !Sub arn:aws:ec2:${AWS::Region}:${AWS::AccountId}:security-group/*
                Condition:
                  StringEquals:
                    "aws:ResourceTag/aws:cloudformation:stack-name": !Sub kanban-app-${Environment}
              - Sid: Ec2RunInstanceOther
                Effect: Allow
                Action: ec2:RunInstances
                Resource:
                  - !Sub arn:aws:ec2:${AWS::Region}:${AWS::AccountId}:subnet/*
                  - !Sub arn:aws:ec2:${AWS::Region}:${AWS::AccountId}:network-interface/*
                  - !Sub arn:aws:ec2:${AWS::Region}:${AWS::AccountId}:volume/*
                  - !Sub arn:aws:ec2:${AWS::Region}::image/*

              # aws:-prefixed tags are set by CloudFormation only and
              # can't be written by any principal, so this can't be
              # forged to reach the other environment.
              - Sid: Ec2OwnStack
                Effect: Allow
                Action:
                  - ec2:TerminateInstances
                  - ec2:StopInstances
                  - ec2:StartInstances
                  - ec2:ModifyInstanceAttribute
                  - ec2:DeleteSecurityGroup
                  - ec2:AuthorizeSecurityGroupIngress
                  - ec2:AuthorizeSecurityGroupEgress
                  - ec2:RevokeSecurityGroupIngress
                  - ec2:RevokeSecurityGroupEgress
                Resource:
                  - !Sub arn:aws:ec2:${AWS::Region}:${AWS::AccountId}:instance/*
                  - !Sub arn:aws:ec2:${AWS::Region}:${AWS::AccountId}:security-group/*
                Condition:
                  StringEquals:
                    "aws:ResourceTag/aws:cloudformation:stack-name": !Sub kanban-app-${Environment}
              # Authorize/Revoke also touch the (untagged) rule resource;
              # the group itself is still checked by Ec2OwnStack.
              - Sid: Ec2SecurityGroupRules
                Effect: Allow
                Action:
                  - ec2:AuthorizeSecurityGroupIngress
                  - ec2:AuthorizeSecurityGroupEgress
                  - ec2:RevokeSecurityGroupIngress
                  - ec2:RevokeSecurityGroupEgress
                Resource: !Sub arn:aws:ec2:${AWS::Region}:${AWS::AccountId}:security-group-rule/*
              # No resizing the stack's own instance. The key is only
              # present when a request changes the instance type, so
              # UserData updates are unaffected.
              - Sid: Ec2DenyResize
                Effect: Deny
                Action: ec2:ModifyInstanceAttribute
                Resource: "*"
                Condition:
                  StringNotEquals:
                    ec2:Attribute/InstanceType: t3.micro

              # CloudFormation-generated RDS names start with the
              # lowercased stack name. No rds:ModifyDBInstance, so the
              # class can't change after creation.
              - Sid: RdsCreateInstanceSize
                Effect: Allow
                Action: rds:CreateDBInstance
                Resource: !Sub arn:aws:rds:${AWS::Region}:${AWS::AccountId}:db:kanban-app-${Environment}-*
                Condition:
                  StringEquals:
                    rds:DatabaseClass: db.t4g.micro
              - Sid: RdsCreateInstanceDependencies
                Effect: Allow
                Action: rds:CreateDBInstance
                Resource:
                  - !Sub arn:aws:rds:${AWS::Region}:${AWS::AccountId}:subgrp:kanban-app-${Environment}-*
                  - !Sub arn:aws:rds:${AWS::Region}:${AWS::AccountId}:pg:default.postgres17
                  - !Sub arn:aws:rds:${AWS::Region}:${AWS::AccountId}:og:default:postgres-17
              - Sid: RdsOwn
                Effect: Allow
                Action:
                  - rds:DeleteDBInstance
                  - rds:CreateDBSubnetGroup
                  - rds:DeleteDBSubnetGroup
                  - rds:AddTagsToResource
                  - rds:ListTagsForResource
                Resource:
                  - !Sub arn:aws:rds:${AWS::Region}:${AWS::AccountId}:db:kanban-app-${Environment}-*
                  - !Sub arn:aws:rds:${AWS::Region}:${AWS::AccountId}:subgrp:kanban-app-${Environment}-*
              - Sid: RdsDescribe
                Effect: Allow
                Action:
                  - rds:DescribeDBInstances
                  - rds:DescribeDBSubnetGroups
                Resource: "*"

              # Granting permissions to the app EC2 role requires this
              # environment's boundary on it.
              # Put/DeleteRolePermissionsBoundary are not granted, so the
              # boundary can't be swapped out.
              - Sid: IamGrantWithBoundary
                Effect: Allow
                Action:
                  - iam:CreateRole
                  - iam:PutRolePolicy
                  - iam:AttachRolePolicy
                Resource: !Sub arn:aws:iam::${AWS::AccountId}:role/kanban-app-${Environment}-ec2
                Condition:
                  StringEquals:
                    iam:PermissionsBoundary: !Ref Ec2RoleBoundary
              - Sid: Iam
                Effect: Allow
                Action:
                  - iam:DeleteRole
                  - iam:GetRole
                  - iam:DeleteRolePolicy
                  - iam:GetRolePolicy
                  - iam:TagRole
                  - iam:DetachRolePolicy
                  - iam:PassRole
                  - iam:CreateInstanceProfile
                  - iam:DeleteInstanceProfile
                  - iam:GetInstanceProfile
                  - iam:AddRoleToInstanceProfile
                  - iam:RemoveRoleFromInstanceProfile
                Resource:
                  - !Sub arn:aws:iam::${AWS::AccountId}:role/kanban-app-${Environment}-ec2
                  - !Sub arn:aws:iam::${AWS::AccountId}:instance-profile/kanban-app-${Environment}-*

              # CloudFormation resolves the AMI's {{resolve:ssm:...}}
              # reference with the deploying principal's permissions.
              - Sid: SsmPublicAmiLookup
                Effect: Allow
                Action: ssm:GetParameters
                Resource: !Sub arn:aws:ssm:${AWS::Region}::parameter/aws/service/ami-amazon-linux-latest/*

              - Sid: SecretsManagerScoped
                Effect: Allow
                Action:
                  - secretsmanager:CreateSecret
                  - secretsmanager:DeleteSecret
                  - secretsmanager:UpdateSecret
                  - secretsmanager:DescribeSecret
                  - secretsmanager:TagResource
                  # CloudFormation resolves the RDS master password's
                  # {{resolve:secretsmanager:...}} with this role.
                  - secretsmanager:GetSecretValue
                Resource: !Sub arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:kanban-app-${Environment}-*
              - Sid: SecretsManagerUnscoped
                Effect: Allow
                Action: secretsmanager:GetRandomPassword
                Resource: "*"

              # SendCommand is authorized on the document and on each
              # instance separately; the tag condition only fits the
              # instance.
              - Sid: SsmRunCommandDocument
                Effect: Allow
                Action: ssm:SendCommand
                Resource: !Sub arn:aws:ssm:${AWS::Region}::document/AWS-RunShellScript
              - Sid: SsmRunCommandInstance
                Effect: Allow
                Action: ssm:SendCommand
                Resource: !Sub arn:aws:ec2:${AWS::Region}:${AWS::AccountId}:instance/*
                Condition:
                  StringEquals:
                    "ssm:resourceTag/aws:cloudformation:stack-name": !Sub kanban-app-${Environment}
              - Sid: SsmRunCommandRead
                Effect: Allow
                Action:
                  - ssm:GetCommandInvocation
                  - ssm:ListCommandInvocations
                  - ssm:DescribeInstanceInformation
                Resource: "*"

              - Sid: EcrDescribe
                Effect: Allow
                Action:
                  - ecr:DescribeRepositories
                  - ecr:DescribeImages
                Resource: !Sub arn:aws:ecr:${AWS::Region}:${AWS::AccountId}:repository/${EcrRepositoryName}
              # Only dev builds and pushes. Prod deploys images that
              # already exist; its instances pull with their own role.
              - !If
                - IsDev
                - Sid: EcrAuth
                  Effect: Allow
                  Action: ecr:GetAuthorizationToken
                  Resource: "*"
                - !Ref AWS::NoValue
              - !If
                - IsDev
                - Sid: EcrPush
                  Effect: Allow
                  Action:
                    - ecr:BatchCheckLayerAvailability
                    - ecr:InitiateLayerUpload
                    - ecr:UploadLayerPart
                    - ecr:CompleteLayerUpload
                    - ecr:PutImage
                    - ecr:BatchGetImage
                    - ecr:GetDownloadUrlForLayer
                  Resource: !Sub arn:aws:ecr:${AWS::Region}:${AWS::AccountId}:repository/${EcrRepositoryName}
                - !Ref AWS::NoValue

              # Every name, type and action in a change batch must match:
              # prod's hostname is the parent of dev's, so e.g. a CAA
              # record at the prod name could block dev's certificate.
              - Sid: Route53Change
                Effect: Allow
                Action: route53:ChangeResourceRecordSets
                Resource: !Sub arn:aws:route53:::hostedzone/${HostedZoneId}
                Condition:
                  ForAllValues:StringEquals:
                    route53:ChangeResourceRecordSetsNormalizedRecordNames: !Ref Hostname
                    route53:ChangeResourceRecordSetsRecordTypes: A
                    route53:ChangeResourceRecordSetsActions: [UPSERT, DELETE]
              - Sid: Route53Read
                Effect: Allow
                Action:
                  - route53:ListResourceRecordSets
                  - route53:GetHostedZone
                Resource: !Sub arn:aws:route53:::hostedzone/${HostedZoneId}

Outputs:
  DeployRoleArn:
    Description: Set as secret AWS_DEPLOY_ROLE_ARN of this environment's GitHub Environment.
    Value: !GetAtt DeployRole.Arn
  Ec2RoleBoundaryArn:
    Value: !Ref Ec2RoleBoundary
```

- [ ] **Step 2: Lint**

Run: `uvx cfn-lint deploy/cloudformation/deploy-role.yaml`
Expected: no output, exit 0. Fix any error; if a warning is a false
positive (e.g. about the colon-containing condition keys), add a
`Metadata: cfn-lint: config: ignore_checks` entry on that resource
with a comment saying why.

- [ ] **Step 3: Commit**

```bash
git add deploy/cloudformation/deploy-role.yaml
git commit -m "deploy: per-environment deploy role and EC2 boundary template"
```

---

### Task 2: Bootstrap — immutable tags, keep 30 images

Phase A of the bootstrap change. The old `kanban-app-deploy` role and
`kanban-app-ec2-boundary` stay until Task 11 (add before remove).

**Files:**
- Modify: `deploy/cloudformation/bootstrap.yaml` (`EcrRepository`)

**Interfaces:**
- Produces: ECR repo `kanban` with `ImageTagMutability: IMMUTABLE`.

- [ ] **Step 1: Edit `EcrRepository`**

Replace the resource with:

```yaml
  EcrRepository:
    Type: AWS::ECR::Repository
    Properties:
      RepositoryName: kanban
      # Shared by dev and prod: dev builds, prod runs the same images.
      # Immutable, so the dev role can't overwrite a tag prod runs.
      ImageTagMutability: IMMUTABLE
      LifecyclePolicy:
        LifecyclePolicyText: |
          {
            "rules": [{
              "rulePriority": 1,
              "description": "Keep only the last 30 images",
              "selection": {
                "tagStatus": "any",
                "countType": "imageCountMoreThan",
                "countNumber": 30
              },
              "action": { "type": "expire" }
            }]
          }
```

- [ ] **Step 2: Lint**

Run: `uvx cfn-lint deploy/cloudformation/bootstrap.yaml`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add deploy/cloudformation/bootstrap.yaml
git commit -m "deploy: make ECR tags immutable, keep 30 images"
```

---

### Task 3: App stack names per environment

**Files:**
- Modify: `deploy/cloudformation/stack.yaml` (lines 2–8, 23–28, 74,
  111–114, 171)

**Interfaces:**
- Consumes: boundary `kanban-app-<env>-ec2-boundary` (Task 1).
- Produces: stack parameters unchanged in name (`VpcId`, `SubnetIds`,
  `EcrRepositoryUri`, `ImageTag`, `SubdomainName`, `AdminEmail`);
  `SubdomainName` now has no default. Outputs unchanged
  (`DBSecretArn`, `DBEndpointAddress`, `InstancePublicIp`, `InstanceId`).

- [ ] **Step 1: Edit the header and parameters**

Description (lines 2–8) becomes:

```yaml
Description: >
  Ephemeral app stack for mini-kanban-board, one per environment
  (kanban-app-dev, kanban-app-prod): EC2 (app + Caddy containers), RDS
  Postgres, and the security groups/secret tying them together. Created
  and destroyed by the deploy/destroy workflow - see
  _docs/deployment-plan.md steps 6 and 7. Deployed by the environment's
  scoped role from deploy/cloudformation/deploy-role.yaml, never by hand
  with AdminAccess. All names derive from the stack name.
```

`ImageTag` and `SubdomainName` become:

```yaml
  ImageTag:
    Type: String
    Description: Image tag to run (YYYYMMDD-HHMMSS-shortsha, from the deploy workflow's build job).
  SubdomainName:
    Type: String
    Description: The environment's public hostname; Caddy requests a TLS cert for it.
```

- [ ] **Step 2: Edit the names**

- Line 74: `Name: !Sub kanban-app-${AWS::StackName}-db` →
  `Name: !Sub ${AWS::StackName}-db`
- Line 111: `RoleName: !Sub kanban-app-${AWS::StackName}-ec2` →
  `RoleName: !Sub ${AWS::StackName}-ec2`
- Lines 112–114 become:

```yaml
      # Required by the deploy role's IAM policy (deploy-role.yaml,
      # Ec2RoleBoundary); caps this role's permissions to this
      # environment's secret.
      PermissionsBoundary: !Sub arn:aws:iam::${AWS::AccountId}:policy/${AWS::StackName}-ec2-boundary
```

- Line 171: `Value: !Sub kanban-app-${AWS::StackName}` →
  `Value: !Sub ${AWS::StackName}`

- [ ] **Step 3: Check nothing else hardcodes the old names**

Run: `grep -n 'kanban-app-\${AWS::StackName}\|kanban-app-ec2-boundary\|katban' deploy/cloudformation/stack.yaml`
Expected: no output.

- [ ] **Step 4: Lint and commit**

Run: `uvx cfn-lint deploy/cloudformation/stack.yaml` → exit 0.

```bash
git add deploy/cloudformation/stack.yaml
git commit -m "deploy: derive app stack names from the stack name"
```

---

### Task 4: Destroy action and per-environment deploy workflow

**Files:**
- Create: `.github/actions/destroy-env/action.yml`
- Rewrite: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: GitHub Environment `<env>` secret `AWS_DEPLOY_ROLE_ARN`,
  variable `SUBDOMAIN`; repo variables `AWS_HOSTED_ZONE_ID`,
  `AWS_VPC_ID`, `AWS_SUBNET_IDS`; stack outputs from Task 3.
- Produces: composite action `./.github/actions/destroy-env` with inputs
  `stack-name`, `hostname`, `hosted-zone-id` (callers check out the repo
  and configure AWS credentials first; region comes from
  `configure-aws-credentials`). Concurrency group name
  `deploy-kanban-app-<env>` (reused by Task 5).

- [ ] **Step 1: Write the composite action**

`.github/actions/destroy-env/action.yml`:

```yaml
name: Destroy environment
description: >
  Deletes an environment's DNS A record and its app CloudFormation stack.
  Callers check out the repo and configure AWS credentials first. A
  missing record or stack counts as already destroyed; any other error
  fails the action.
inputs:
  stack-name:
    description: App stack name, e.g. kanban-app-dev.
    required: true
  hostname:
    description: The environment's hostname (no trailing dot).
    required: true
  hosted-zone-id:
    description: Route 53 hosted zone ID.
    required: true
runs:
  using: composite
  steps:
    # Reads the live record and deletes exactly that, instead of
    # rebuilding it from the stack's IP output (which misses a drifted
    # record and is gone once the stack is). A failure here doesn't stop
    # the stack deletion; the last step reports it.
    - name: Delete DNS A record
      id: dns
      shell: bash
      env:
        RECORD_NAME: ${{ inputs.hostname }}
        ZONE_ID: ${{ inputs.hosted-zone-id }}
      run: |
        set -uo pipefail
        if ! record=$(aws route53 list-resource-record-sets --hosted-zone-id "$ZONE_ID" \
            --start-record-name "$RECORD_NAME" --start-record-type A --max-items 1 \
            --query "ResourceRecordSets[?Name == '${RECORD_NAME}.' && Type == 'A'] | [0]" \
            --output json); then
          echo "failed=true" >> "$GITHUB_OUTPUT"
          exit 0
        fi
        if [ "$record" = "null" ]; then
          echo "No A record for $RECORD_NAME"
          exit 0
        fi
        jq -n --argjson rr "$record" '{Changes: [{Action: "DELETE", ResourceRecordSet: $rr}]}' \
          > "$RUNNER_TEMP/delete-record.json"
        if ! aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" \
            --change-batch "file://$RUNNER_TEMP/delete-record.json"; then
          echo "failed=true" >> "$GITHUB_OUTPUT"
        fi

    - name: Delete stack
      shell: bash
      env:
        STACK_NAME: ${{ inputs.stack-name }}
      run: |
        set -euo pipefail
        if ! aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
            > /dev/null 2> "$RUNNER_TEMP/describe.err"; then
          if grep -q "does not exist" "$RUNNER_TEMP/describe.err"; then
            echo "No stack $STACK_NAME"
            exit 0
          fi
          cat "$RUNNER_TEMP/describe.err" >&2
          exit 1
        fi
        aws cloudformation delete-stack --stack-name "$STACK_NAME"
        aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"

    - name: Report DNS cleanup failure
      if: steps.dns.outputs.failed == 'true'
      shell: bash
      env:
        RECORD_NAME: ${{ inputs.hostname }}
      run: |
        echo "::error::Deleting the DNS A record for $RECORD_NAME failed (see the first step)"
        exit 1
```

- [ ] **Step 2: Rewrite `deploy.yml`**

Replace the whole file with:

````yaml
name: Deploy

# Manual only (not push-to-main): usage is short and infrequent. See
# _docs/deployment-plan.md steps 6 and 7.
on:
  workflow_dispatch:
    inputs:
      action:
        description: "deploy or destroy"
        required: true
        type: choice
        options: [deploy, destroy]
      environment:
        description: "dev or prod"
        required: true
        type: choice
        options: [dev, prod]

permissions:
  id-token: write
  contents: read

# One run per environment at a time - an overlapping deploy/destroy would
# race on the same stack and DNS record. auto-destroy.yml joins the same
# groups. Dev and prod don't block each other.
concurrency:
  group: deploy-kanban-app-${{ inputs.environment }}
  cancel-in-progress: false

env:
  AWS_REGION: eu-central-1
  ECR_REPOSITORY: kanban
  STACK_NAME: kanban-app-${{ inputs.environment }}

jobs:
  # Always runs in the dev GitHub Environment: only the dev deploy role
  # can push to the shared ECR repo. Prod only runs images that already
  # exist there (a later promote workflow replaces building here).
  build:
    if: inputs.action == 'deploy'
    runs-on: ubuntu-latest
    environment: dev
    outputs:
      image_tag: ${{ steps.tag.outputs.tag }}
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: true

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      # YYYYMMDD-HHMMSS-shortsha (UTC). Tags are immutable in ECR, so
      # every run needs a new one, even for the same commit.
      - name: Compute image tag
        id: tag
        run: echo "tag=$(date -u +%Y%m%d-%H%M%S)-${GITHUB_SHA::7}" >> "$GITHUB_OUTPUT"

      - name: Resolve ECR repository URI
        id: ecr
        run: |
          uri=$(aws ecr describe-repositories --repository-names "${{ env.ECR_REPOSITORY }}" \
            --query "repositories[0].repositoryUri" --output text)
          echo "uri=$uri" >> "$GITHUB_OUTPUT"

      - name: Log in to ECR
        run: |
          aws ecr get-login-password --region "${{ env.AWS_REGION }}" | \
            docker login --username AWS --password-stdin "${{ steps.ecr.outputs.uri }}"

      - name: Build and push image
        run: |
          image="${{ steps.ecr.outputs.uri }}:${{ steps.tag.outputs.tag }}"
          docker build -t "$image" .
          docker push "$image"

  deploy:
    needs: build
    if: inputs.action == 'deploy'
    runs-on: ubuntu-latest
    environment: ${{ inputs.environment }}
    env:
      IMAGE_TAG: ${{ needs.build.outputs.image_tag }}
      SUBDOMAIN: ${{ vars.SUBDOMAIN }}
    steps:
      - name: Check environment config
        run: |
          if [ -z "$SUBDOMAIN" ] || [ -z "$IMAGE_TAG" ]; then
            echo "::error::SUBDOMAIN (GitHub Environment variable) or the build's image tag is empty"
            exit 1
          fi

      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Resolve ECR repository URI
        id: ecr
        run: |
          uri=$(aws ecr describe-repositories --repository-names "${{ env.ECR_REPOSITORY }}" \
            --query "repositories[0].repositoryUri" --output text)
          echo "uri=$uri" >> "$GITHUB_OUTPUT"

      - name: Deploy CloudFormation stack
        run: |
          aws cloudformation deploy \
            --template-file deploy/cloudformation/stack.yaml \
            --stack-name "${{ env.STACK_NAME }}" \
            --capabilities CAPABILITY_NAMED_IAM \
            --parameter-overrides \
              VpcId="${{ vars.AWS_VPC_ID }}" \
              SubnetIds="${{ vars.AWS_SUBNET_IDS }}" \
              EcrRepositoryUri="${{ steps.ecr.outputs.uri }}" \
              ImageTag="${{ env.IMAGE_TAG }}" \
              SubdomainName="${{ env.SUBDOMAIN }}"

      - name: Update DNS A record
        # Before starting/restarting the containers: Caddy requests its
        # TLS cert as soon as it starts, and Let's Encrypt/ZeroSSL both
        # do a live DNS lookup as part of that - if the A record isn't
        # in place yet, the lookup gives NXDOMAIN, the attempt fails,
        # and Caddy doesn't retry again for a while (a live deploy
        # served TLS "internal error" for several minutes because of
        # this ordering).
        run: |
          ip=$(aws cloudformation describe-stacks --stack-name "${{ env.STACK_NAME }}" \
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
            --hosted-zone-id "${{ vars.AWS_HOSTED_ZONE_ID }}" \
            --change-batch file:///tmp/change-batch.json

      - name: Push app update via SSM
        # EC2 UserData only runs once at first boot - it does not
        # re-run on the stop/modify/start cycle CloudFormation uses for
        # a UserData-only update (e.g. just a new ImageTag on an
        # existing instance), so every deploy re-applies the compose
        # config here instead of relying on UserData for anything past
        # first-boot bring-up.
        run: |
          instance_id=$(aws cloudformation describe-stacks --stack-name "${{ env.STACK_NAME }}" \
            --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)
          db_secret_arn=$(aws cloudformation describe-stacks --stack-name "${{ env.STACK_NAME }}" \
            --query "Stacks[0].Outputs[?OutputKey=='DBSecretArn'].OutputValue" --output text)
          db_endpoint=$(aws cloudformation describe-stacks --stack-name "${{ env.STACK_NAME }}" \
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
            --region ${{ env.AWS_REGION }} --secret-id $db_secret_arn \
            --query SecretString --output text \
            | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')
          DB_URL="postgresql+psycopg://kanban:\${DB_PASSWORD}@${db_endpoint}:5432/kanban"

          aws ecr get-login-password --region ${{ env.AWS_REGION }} | \
            docker login --username AWS --password-stdin ${{ steps.ecr.outputs.uri }}

          mkdir -p /opt/kanban
          cat > /opt/kanban/docker-compose.yml <<COMPOSE
          services:
            app:
              image: ${{ steps.ecr.outputs.uri }}:${{ env.IMAGE_TAG }}
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
          ${{ env.SUBDOMAIN }} {
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
        run: |
          for i in $(seq 1 60); do
            code=$(curl -s -o /dev/null -w "%{http_code}" \
              "https://${{ env.SUBDOMAIN }}/api/health" || true)
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
    environment: ${{ inputs.environment }}
    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - uses: ./.github/actions/destroy-env
        with:
          stack-name: ${{ env.STACK_NAME }}
          hostname: ${{ vars.SUBDOMAIN }}
          hosted-zone-id: ${{ vars.AWS_HOSTED_ZONE_ID }}
````

(The outer four-backtick fence is only for this plan; the file content
is the YAML inside it.)

- [ ] **Step 3: Diff the whole workflow against intent**

The postmortem's root cause 4: an order-sensitive edit left a duplicate
step. Run `grep -n "name:" .github/workflows/deploy.yml` and confirm the
deploy job's step order is exactly: Check environment config, checkout,
credentials, Resolve ECR repository URI, Deploy CloudFormation stack,
Update DNS A record, Push app update via SSM, Wait for health check — no
duplicates. Also run
`grep -n "github.sha\|steps.tag" .github/workflows/deploy.yml` and
confirm `github.sha` doesn't appear and `steps.tag` only appears in the
build job.

- [ ] **Step 4: Lint**

Run: `docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest -color`
Expected: no findings for `deploy.yml` (fix any). actionlint also checks
the `destroy-env` inputs.

- [ ] **Step 5: Commit**

```bash
git add .github/actions/destroy-env/action.yml .github/workflows/deploy.yml
git commit -m "deploy: per-environment deploy workflow and shared destroy action"
```

---

### Task 5: Scheduled auto-destroy

**Files:**
- Create: `.github/workflows/auto-destroy.yml`

**Interfaces:**
- Consumes: `./.github/actions/destroy-env` and concurrency group
  `deploy-kanban-app-<env>` (Task 4); per-environment secret/variable
  as in Task 4.

- [ ] **Step 1: Write the workflow**

```yaml
name: Auto-destroy

# Safety net against forgotten environments: destroys an environment
# whose app stack has existed longer than max_age_hours (72 on the daily
# schedule). Best effort - GitHub can delay or skip scheduled runs, and
# pauses them in a public repo after 60 days without activity. See
# _docs/deployment-plan.md step 7.
on:
  schedule:
    - cron: "17 3 * * *"
  workflow_dispatch:
    inputs:
      max_age_hours:
        description: "Destroy environments older than this many hours (0 = destroy now)"
        required: true
        default: "72"
        type: string

permissions:
  id-token: write
  contents: read

jobs:
  auto-destroy:
    strategy:
      fail-fast: false
      matrix:
        environment: [dev, prod]
    runs-on: ubuntu-latest
    environment: ${{ matrix.environment }}
    # Same group as deploy.yml, held from the age check through the
    # deletion: never races a running deploy/destroy.
    concurrency:
      group: deploy-kanban-app-${{ matrix.environment }}
      cancel-in-progress: false
    env:
      AWS_REGION: eu-central-1
      STACK_NAME: kanban-app-${{ matrix.environment }}
      # Scheduled runs have no inputs. Not `inputs.max_age_hours || '72'`:
      # that pattern is easy to get wrong for a manual 0.
      MAX_AGE_HOURS: ${{ github.event_name == 'schedule' && '72' || inputs.max_age_hours }}
    steps:
      - name: Validate max age
        run: |
          if ! [[ "$MAX_AGE_HOURS" =~ ^[0-9]+$ ]]; then
            echo "::error::max_age_hours must be a non-negative whole number, got '$MAX_AGE_HOURS'"
            exit 1
          fi

      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      # Creation time counts from the first deploy; redeploys don't
      # reset it.
      - name: Check stack age
        id: age
        run: |
          set -euo pipefail
          if ! created=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
              --query "Stacks[0].CreationTime" --output text 2> "$RUNNER_TEMP/describe.err"); then
            if grep -q "does not exist" "$RUNNER_TEMP/describe.err"; then
              echo "No stack $STACK_NAME, nothing to do"
              echo "expired=false" >> "$GITHUB_OUTPUT"
              exit 0
            fi
            cat "$RUNNER_TEMP/describe.err" >&2
            exit 1
          fi
          age_hours=$(( ( $(date -u +%s) - $(date -u -d "$created" +%s) ) / 3600 ))
          echo "$STACK_NAME created $created, ${age_hours}h ago (limit ${MAX_AGE_HOURS}h)"
          if [ "$age_hours" -ge "$MAX_AGE_HOURS" ]; then
            echo "expired=true" >> "$GITHUB_OUTPUT"
          else
            echo "expired=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Destroy
        if: steps.age.outputs.expired == 'true'
        uses: ./.github/actions/destroy-env
        with:
          stack-name: ${{ env.STACK_NAME }}
          hostname: ${{ vars.SUBDOMAIN }}
          hosted-zone-id: ${{ vars.AWS_HOSTED_ZONE_ID }}
```

- [ ] **Step 2: Check the age arithmetic locally**

Run:
```bash
created="$(date -u -d '-73 hours' +%Y-%m-%dT%H:%M:%S.000000+00:00)"
echo $(( ( $(date -u +%s) - $(date -u -d "$created" +%s) ) / 3600 ))
```
Expected: `73` (confirms `date -d` parses CloudFormation's
`CreationTime` format and the hour math).

- [ ] **Step 3: Lint**

Run: `docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest -color`
Expected: no findings.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/auto-destroy.yml
git commit -m "deploy: daily auto-destroy for environments older than 72h"
```

---

### Task 6: IAM verification script

**Files:**
- Create: `deploy/verify-iam.sh` (executable)

**Interfaces:**
- Consumes: roles `kanban-deploy-dev` and `kanban-deploy-prod` (exist
  after Task 9 Step 3). Run as
  `scripts/with-secrets AWS_PROFILE -- deploy/verify-iam.sh`. Exits 1 if
  any check fails.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Checks both deploy roles with the IAM policy simulator: each role is
# allowed on its own environment's resources, denied on the other's, and
# capped to the small instance sizes. Read-only
# (iam:SimulatePrincipalPolicy). Run with the admin profile once the
# kanban-deploy-dev/-prod stacks exist:
#   scripts/with-secrets AWS_PROFILE -- deploy/verify-iam.sh
# The simulator only sees the context keys given here; the live deploy
# is what proves the real request context matches.
set -euo pipefail

REGION=eu-central-1
PROD_HOST=katban-10x-cat-productivity.lighfe.dev
DEV_HOST=dev.$PROD_HOST
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ZONE_ID=$(aws route53 list-hosted-zones-by-name --dns-name lighfe.dev. --max-items 1 \
  --query "HostedZones[0].Id" --output text)
ZONE_ID=${ZONE_ID#/hostedzone/}

INSTANCE=arn:aws:ec2:$REGION:$ACCOUNT:instance/i-0123456789abcdef0
SG=arn:aws:ec2:$REGION:$ACCOUNT:security-group/sg-0123456789abcdef0
DOC=arn:aws:ssm:$REGION::document/AWS-RunShellScript
ZONE=arn:aws:route53:::hostedzone/$ZONE_ID
ECR=arn:aws:ecr:$REGION:$ACCOUNT:repository/kanban

host() { if [ "$1" = prod ]; then echo "$PROD_HOST"; else echo "$DEV_HOST"; fi; }
stack() { echo "arn:aws:cloudformation:$REGION:$ACCOUNT:stack/kanban-app-$1/00000000-0000-0000-0000-000000000000"; }
secret() { echo "arn:aws:secretsmanager:$REGION:$ACCOUNT:secret:kanban-app-$1-db-AbCdEf"; }
role() { echo "arn:aws:iam::$ACCOUNT:role/kanban-app-$1-ec2"; }
boundary() { echo "arn:aws:iam::$ACCOUNT:policy/kanban-app-$1-ec2-boundary"; }
db() { echo "arn:aws:rds:$REGION:$ACCOUNT:db:kanban-app-$1-dbinstance-abc123"; }

# ctx KEY TYPE VALUE... -> JSON context-entry list with one key
ctx() {
  local key=$1 type=$2
  shift 2
  jq -cn --arg k "$key" --arg t "$type" \
    '[{ContextKeyName: $k, ContextKeyType: $t, ContextKeyValues: $ARGS.positional}]' --args "$@"
}
stack_tag() { ctx "aws:ResourceTag/aws:cloudformation:stack-name" string "kanban-app-$1"; }
join() { jq -cs add <<<"$*"; }
# r53 NAMES TYPES ACTIONS (each comma-separated)
r53() {
  join "$(ctx route53:ChangeResourceRecordSetsNormalizedRecordNames stringList ${1//,/ })" \
       "$(ctx route53:ChangeResourceRecordSetsRecordTypes stringList ${2//,/ })" \
       "$(ctx route53:ChangeResourceRecordSetsActions stringList ${3//,/ })"
}

failures=0
# check EXPECTED(allowed|denied) ENV ACTION RESOURCE [CONTEXT_JSON]
check() {
  local expected=$1 env=$2 action=$3 resource=$4 context=${5:-}
  local args=(--policy-source-arn "arn:aws:iam::$ACCOUNT:role/kanban-deploy-$env"
              --action-names "$action" --resource-arns "$resource"
              --query "EvaluationResults[0].EvalDecision" --output text)
  if [ -n "$context" ]; then args+=(--context-entries "$context"); fi
  local decision got=denied
  decision=$(aws iam simulate-principal-policy "${args[@]}")
  if [ "$decision" = allowed ]; then got=allowed; fi
  if [ "$got" = "$expected" ]; then
    echo "ok    $env $expected $action ${resource##*:} $context"
  else
    echo "FAIL  $env expected $expected, got $decision: $action $resource $context"
    failures=$((failures + 1))
  fi
}

for pair in dev:prod prod:dev; do
  E=${pair%%:*}
  O=${pair##*:}
  check allowed "$E" cloudformation:CreateChangeSet "$(stack "$E")"
  check denied  "$E" cloudformation:DeleteStack "$(stack "$O")"
  check allowed "$E" secretsmanager:GetSecretValue "$(secret "$E")"
  check denied  "$E" secretsmanager:GetSecretValue "$(secret "$O")"
  check allowed "$E" iam:CreateRole "$(role "$E")" "$(ctx iam:PermissionsBoundary string "$(boundary "$E")")"
  check denied  "$E" iam:CreateRole "$(role "$E")" "$(ctx iam:PermissionsBoundary string "$(boundary "$O")")"
  check denied  "$E" iam:PutRolePolicy "$(role "$O")" "$(ctx iam:PermissionsBoundary string "$(boundary "$O")")"
  check denied  "$E" iam:PassRole "$(role "$O")"
  check allowed "$E" rds:CreateDBInstance "$(db "$E")" "$(ctx rds:DatabaseClass string db.t4g.micro)"
  check denied  "$E" rds:CreateDBInstance "$(db "$E")" "$(ctx rds:DatabaseClass string db.m5.large)"
  check denied  "$E" rds:DeleteDBInstance "$(db "$O")"
  check allowed "$E" ec2:RunInstances "$INSTANCE" "$(ctx ec2:InstanceType string t3.micro)"
  check denied  "$E" ec2:RunInstances "$INSTANCE" "$(ctx ec2:InstanceType string t3.large)"
  check allowed "$E" ec2:RunInstances "$SG" "$(stack_tag "$E")"
  check denied  "$E" ec2:RunInstances "$SG" "$(stack_tag "$O")"
  check allowed "$E" ec2:TerminateInstances "$INSTANCE" "$(stack_tag "$E")"
  check denied  "$E" ec2:TerminateInstances "$INSTANCE" "$(stack_tag "$O")"
  check denied  "$E" ec2:AuthorizeSecurityGroupIngress "$SG" "$(stack_tag "$O")"
  check allowed "$E" ec2:ModifyInstanceAttribute "$INSTANCE" "$(stack_tag "$E")"
  check denied  "$E" ec2:ModifyInstanceAttribute "$INSTANCE" \
    "$(join "$(stack_tag "$E")" "$(ctx ec2:Attribute/InstanceType string t3.large)")"
  check allowed "$E" ssm:SendCommand "$DOC"
  check allowed "$E" ssm:SendCommand "$INSTANCE" "$(ctx "ssm:resourceTag/aws:cloudformation:stack-name" string "kanban-app-$E")"
  check denied  "$E" ssm:SendCommand "$INSTANCE" "$(ctx "ssm:resourceTag/aws:cloudformation:stack-name" string "kanban-app-$O")"
  check allowed "$E" route53:ChangeResourceRecordSets "$ZONE" "$(r53 "$(host "$E")" A UPSERT)"
  check denied  "$E" route53:ChangeResourceRecordSets "$ZONE" "$(r53 "$(host "$O")" A UPSERT)"
  check denied  "$E" route53:ChangeResourceRecordSets "$ZONE" "$(r53 "$(host "$E"),$(host "$O")" A,A UPSERT,UPSERT)"
  check denied  "$E" route53:ChangeResourceRecordSets "$ZONE" "$(r53 "$(host "$E")" CAA UPSERT)"
done
check allowed dev  ecr:PutImage "$ECR"
check denied  prod ecr:PutImage "$ECR"

if [ "$failures" -eq 0 ]; then
  echo "All checks passed"
else
  echo "$failures check(s) failed"
  exit 1
fi
```

- [ ] **Step 2: Lint**

Run:
```bash
chmod +x deploy/verify-iam.sh
docker run --rm -v "$PWD":/mnt koalaman/shellcheck:stable deploy/verify-iam.sh
```
Expected: no findings. The unquoted `${1//,/ }` in `r53` is deliberate
word splitting; if shellcheck flags it (SC2086), add
`# shellcheck disable=SC2086` above that function with a one-line reason.

- [ ] **Step 3: Check the helpers without AWS**

Run:
```bash
bash -c 'source <(sed -n "/^ctx()/,/^}/p;/^join()/p" deploy/verify-iam.sh); join "$(ctx a string x)" "$(ctx b stringList y z)"'
```
Expected: `[{"ContextKeyName":"a","ContextKeyType":"string","ContextKeyValues":["x"]},{"ContextKeyName":"b","ContextKeyType":"stringList","ContextKeyValues":["y","z"]}]`

- [ ] **Step 4: Commit**

```bash
git add deploy/verify-iam.sh
git commit -m "deploy: IAM simulator checks for the per-environment deploy roles"
```

---

### Task 7: Docs

**Files:**
- Modify: `_docs/deployment-plan.md` (line 158; step-6 "Implemented
  in" paragraph at ~line 200; new step 7 before `## Out of scope`)
- Rewrite: `deploy/README.md`
- Modify: `README.md` (`### Live deployment (AWS)`, lines 69–83)

- [ ] **Step 1: `_docs/deployment-plan.md`**

Line 157–158: replace `(most sessions under 2 hours, never longer than
3 days)` with `(most sessions under 2 hours; auto-destroyed after
roughly 72–96 hours at most, see step 7)`.

In the step-6 "Implemented in" paragraph, append after its last
sentence: `Step 7 splits this into dev and prod environments; the
deploy role now lives in deploy-role.yaml.`

Add before `## Out of scope`:

```markdown
### 7. Dev and prod environments — in progress

Two independent copies of the step-6 infrastructure in the same account:
`dev` (`dev.katban-10x-cat-productivity.lighfe.dev`) and `prod`
(`katban-10x-cat-productivity.lighfe.dev`), both still ephemeral and
deployed by hand. Design:
[docs/superpowers/specs/2026-09-26-dev-prod-environments-design.md](../docs/superpowers/specs/2026-09-26-dev-prod-environments-design.md).

- `kanban-bootstrap` keeps only the shared OIDC provider and ECR repo
  (tags immutable, last 30 images kept).
- [deploy/cloudformation/deploy-role.yaml](../deploy/cloudformation/deploy-role.yaml),
  applied once per environment, holds the deploy role
  `kanban-deploy-<env>` and the EC2 boundary. Each role is scoped to
  its own stack, secret, DB, instance, security groups and DNS name,
  and can only launch `t3.micro` / `db.t4g.micro`. Only dev can push
  images.
- `deploy.yml` takes an `environment` input. The image is always built
  in the dev GitHub Environment and tagged `YYYYMMDD-HHMMSS-shortsha`;
  the deploy job runs in the target environment.
- `auto-destroy.yml` runs daily and destroys any environment whose
  stack is older than 72 hours. Best effort: it fires on the first run
  after 72 hours, and GitHub can delay scheduled runs or pause them
  after 60 days without repo activity. The threshold is an open
  question to revisit.
- Check the roles with `deploy/verify-iam.sh` (IAM policy simulator).

Next: dev auto-deploy on push and a manual promote-to-prod workflow
(same image, no rebuild).
```

- [ ] **Step 2: `deploy/README.md`**

Replace the whole file with:

````markdown
# Deploying

See [_docs/deployment-plan.md](../_docs/deployment-plan.md) (steps 6
and 7) for the design. This file is the operational how-to.

Two environments of the same shape:

| | dev | prod |
| --- | --- | --- |
| Hostname | `dev.katban-10x-cat-productivity.lighfe.dev` | `katban-10x-cat-productivity.lighfe.dev` |
| App stack (CI) | `kanban-app-dev` | `kanban-app-prod` |
| Deploy role stack (admin) | `kanban-deploy-dev` | `kanban-deploy-prod` |
| GitHub Environment | `dev` | `prod` |

Shared: the `kanban-bootstrap` stack (GitHub OIDC provider, ECR repo
`kanban`), the `lighfe.dev` hosted zone, and the repo variables below.

## One-time account setup (admin, run once)

Everything below uses the admin SSO profile, separate from the scoped
roles GitHub Actions uses. Run `aws sso login` first if needed.

1. Look up the `lighfe.dev` Route 53 hosted zone ID:

   ```bash
   aws route53 list-hosted-zones-by-name --dns-name lighfe.dev. \
     --query "HostedZones[0].Id" --output text
   ```

   Strip the leading `/hostedzone/` from the result.

2. Deploy the shared bootstrap stack:

   ```bash
   aws cloudformation deploy \
     --template-file deploy/cloudformation/bootstrap.yaml \
     --stack-name kanban-bootstrap \
     --parameter-overrides HostedZoneId=<zone-id> \
     --capabilities CAPABILITY_NAMED_IAM
   ```

   If `token.actions.githubusercontent.com` is already registered as an
   OIDC provider in this account, add `CreateOidcProvider=false`.

3. Deploy one deploy-role stack per environment:

   ```bash
   aws cloudformation deploy \
     --template-file deploy/cloudformation/deploy-role.yaml \
     --stack-name kanban-deploy-dev \
     --parameter-overrides Environment=dev \
       Hostname=dev.katban-10x-cat-productivity.lighfe.dev HostedZoneId=<zone-id> \
     --capabilities CAPABILITY_NAMED_IAM

   aws cloudformation deploy \
     --template-file deploy/cloudformation/deploy-role.yaml \
     --stack-name kanban-deploy-prod \
     --parameter-overrides Environment=prod \
       Hostname=katban-10x-cat-productivity.lighfe.dev HostedZoneId=<zone-id> \
     --capabilities CAPABILITY_NAMED_IAM
   ```

   Check both roles: `deploy/verify-iam.sh` (read-only, IAM policy
   simulator) should print `All checks passed`.

4. Create the GitHub Environments, limited to `main`, with each
   environment's role and hostname:

   ```bash
   for env in dev prod; do
     echo '{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}' \
       | gh api -X PUT "repos/Lighfe/mini-kanban-board/environments/$env" --input -
     gh api -X POST "repos/Lighfe/mini-kanban-board/environments/$env/deployment-branch-policies" \
       -f name=main -f type=branch
     gh secret set AWS_DEPLOY_ROLE_ARN --env "$env" --body "$(aws cloudformation describe-stacks \
       --stack-name "kanban-deploy-$env" \
       --query "Stacks[0].Outputs[?OutputKey=='DeployRoleArn'].OutputValue" --output text)"
   done
   gh variable set SUBDOMAIN --env dev --body dev.katban-10x-cat-productivity.lighfe.dev
   gh variable set SUBDOMAIN --env prod --body katban-10x-cat-productivity.lighfe.dev
   ```

5. Set the shared, non-secret repo variables:

   ```bash
   aws ec2 describe-subnets \
     --filters "Name=default-for-az,Values=true" \
     --query "Subnets[*].{Id:SubnetId,Az:AvailabilityZone}"

   gh variable set AWS_HOSTED_ZONE_ID --body "<zone-id>"
   gh variable set AWS_VPC_ID --body "<default VPC id>"
   gh variable set AWS_SUBNET_IDS --body "<subnet-a>,<subnet-b>"
   ```

   `AWS_SUBNET_IDS` needs exactly two subnets in different AZs — RDS
   requires a subnet group spanning ≥2 AZs even for a single-AZ
   instance.

6. If this account has never created an RDS instance, create the RDS
   service-linked role (the deploy roles deliberately can't):

   ```bash
   aws iam get-role --role-name AWSServiceRoleForRDS
   # NoSuchEntity -> create it:
   aws iam create-service-linked-role --aws-service-name rds.amazonaws.com
   ```

Re-running steps 2–3 is safe (`cloudformation deploy` is idempotent)
when a template changes.

## Running a deploy or a teardown

From the GitHub UI: **Actions → Deploy → Run workflow**, choose the
action and the environment. Or from the CLI:

```bash
gh workflow run deploy.yml -f action=deploy -f environment=dev
gh workflow run deploy.yml -f action=destroy -f environment=dev
gh run watch
```

`deploy` builds the image in the `dev` GitHub Environment (only the dev
role can push), tags it `YYYYMMDD-HHMMSS-shortsha`, then in the target
environment runs `aws cloudformation deploy` against
`deploy/cloudformation/stack.yaml`, points the environment's hostname
at the instance, pushes the compose config over SSM, and polls
`/api/health` until it returns 200.

`destroy` deletes the environment's DNS record and its stack (EC2 +
RDS). Database data is lost — both environments are ephemeral.

Dev and prod runs don't wait for each other; two runs against the same
environment queue.

## Auto-destroy

`.github/workflows/auto-destroy.yml` runs daily and destroys any
environment whose stack is older than 72 hours. To destroy everything
now, or to test it:

```bash
gh workflow run auto-destroy.yml -f max_age_hours=0
```

It only reaches `kanban-app-dev` and `kanban-app-prod`, nothing else
in the account. GitHub can delay scheduled runs and pauses them in a
public repo after 60 days without activity; the AWS budget alert
covers that case.

## Removing the shared layer

Auto-destroy doesn't touch the shared layer: `kanban-bootstrap`,
`kanban-deploy-dev`, `kanban-deploy-prod`, the ECR images (cents per
month) and the hosted zone (~$0.50/month). To remove it after both
environments are destroyed:

```bash
aws cloudformation delete-stack --stack-name kanban-deploy-dev
aws cloudformation delete-stack --stack-name kanban-deploy-prod
aws ecr batch-delete-image --repository-name kanban \
  --image-ids "$(aws ecr list-images --repository-name kanban --query imageIds --output json)"
aws cloudformation delete-stack --stack-name kanban-bootstrap
```

The hosted zone belongs to the domain, not this project; leave it.
````

- [ ] **Step 3: `README.md`**

Replace lines 71–83 (from `The app also runs on AWS` to `expected, not
a bug.`) with:

```markdown
The app also runs on AWS (EC2 + RDS via CloudFormation) in two
independent environments, `dev` and `prod`, triggered manually through
a GitHub Actions workflow — see [deploy/README.md](deploy/README.md)
for the full how-to. Short version:

    gh workflow run deploy.yml -f action=deploy -f environment=dev    # bring dev up
    gh workflow run deploy.yml -f action=destroy -f environment=dev   # tear it down

Use `environment=prod` for production. These are **separate stacks
from the local `make up`/`make down` one**, and unlike the local Docker
volume, `destroy` deletes the RDS instance with no snapshot — **all
board data in that environment is lost**. A daily job also destroys
any environment that has been up for more than 72 hours. Both
environments are meant to be ephemeral (see
[_docs/deployment-plan.md](_docs/deployment-plan.md) steps 6 and 7), so
this is expected, not a bug.
```

- [ ] **Step 4: Commit**

```bash
git add _docs/deployment-plan.md deploy/README.md README.md
git commit -m "docs: dev and prod environments (deployment-plan step 7)"
```

---

### Task 8: Codex review of the branch

- [ ] **Step 1: Run the review (read-only, default model)**

```bash
node "/home/julian/.claude/plugins/cache/openai-codex/codex/1.0.6/scripts/codex-companion.mjs" task \
  "Read-only review, do not edit files. Review the diff of this branch against main (git diff main...HEAD) against the spec docs/superpowers/specs/2026-09-26-dev-prod-environments-design.md. Focus: IAM statements in deploy/cloudformation/deploy-role.yaml that would deny something CloudFormation or the workflows actually need (RunInstances, security-group rules, RDS create, SSM SendCommand, Route 53) or allow the dev role to affect prod or vice versa; GitHub Actions bugs in .github/workflows/deploy.yml, auto-destroy.yml and .github/actions/destroy-env (job outputs, env/vars contexts, concurrency, step order, duplicate steps, shell error handling); anything in deploy/verify-iam.sh that would report ok for a wrong policy. For each finding: severity, file:line, what is wrong, concrete fix. End with a short verdict."
```

- [ ] **Step 2: Triage**

Fix findings that are real bugs or isolation gaps; decline
overengineering, saying why. Re-run the lints from the task that owns
the changed file.

- [ ] **Step 3: Commit fixes**

```bash
git add -A deploy .github _docs README.md
git commit -m "deploy: fix Codex review findings"
```

---

### Task 9: Cutover — add the new pieces

Each `(AWS)` step is a mutating command: show it to the user and wait
for approval of that exact command before running it.

- [ ] **Step 1: Recheck nothing is running (read-only)**

```bash
scripts/with-secrets AWS_PROFILE -- aws cloudformation describe-stacks --region eu-central-1 \
  --query "Stacks[].[StackName,StackStatus]" --output text
gh run list --workflow deploy.yml --limit 3
```
Expected: only `kanban-bootstrap`; no deploy run in progress.

- [ ] **Step 2: (AWS) Create the dev deploy-role stack**

```bash
scripts/with-secrets AWS_PROFILE -- aws cloudformation deploy --region eu-central-1 \
  --template-file deploy/cloudformation/deploy-role.yaml \
  --stack-name kanban-deploy-dev \
  --parameter-overrides Environment=dev \
    Hostname=dev.katban-10x-cat-productivity.lighfe.dev HostedZoneId=Z0050563VZ2HM7AAD71F \
  --capabilities CAPABILITY_NAMED_IAM
```
Expected: `Successfully created/updated stack - kanban-deploy-dev`.

- [ ] **Step 3: (AWS) Create the prod deploy-role stack**

```bash
scripts/with-secrets AWS_PROFILE -- aws cloudformation deploy --region eu-central-1 \
  --template-file deploy/cloudformation/deploy-role.yaml \
  --stack-name kanban-deploy-prod \
  --parameter-overrides Environment=prod \
    Hostname=katban-10x-cat-productivity.lighfe.dev HostedZoneId=Z0050563VZ2HM7AAD71F \
  --capabilities CAPABILITY_NAMED_IAM
```

- [ ] **Step 4: Run the IAM checks (read-only)**

Run: `scripts/with-secrets AWS_PROFILE -- deploy/verify-iam.sh`
Expected: every line `ok`, ending `All checks passed`. A `FAIL` means
the policy (or the check) is wrong: fix `deploy-role.yaml`, lint,
commit, re-apply Steps 2–3 (with approval), re-run.

- [ ] **Step 5: (AWS) Update the bootstrap stack (phase A)**

```bash
scripts/with-secrets AWS_PROFILE -- aws cloudformation deploy --region eu-central-1 \
  --template-file deploy/cloudformation/bootstrap.yaml \
  --stack-name kanban-bootstrap \
  --capabilities CAPABILITY_NAMED_IAM
```
Existing parameter values are reused. Then (read-only):
`scripts/with-secrets AWS_PROFILE -- aws ecr describe-repositories --region eu-central-1 --repository-names kanban --query "repositories[0].imageTagMutability"`
→ `"IMMUTABLE"`.

- [ ] **Step 6: Create the GitHub Environments**

Run deploy/README.md setup step 4 (the `for env in dev prod` block and
the two `SUBDOMAIN` lines), with the `aws` call inside prefixed by
`scripts/with-secrets AWS_PROFILE --`. Then check:

```bash
gh api repos/Lighfe/mini-kanban-board/environments --jq '.environments[] | [.name, .deployment_branch_policy.custom_branch_policies] | @tsv'
gh secret list --env dev; gh secret list --env prod
gh variable list --env dev; gh variable list --env prod
```
Expected: `dev true`, `prod true`; each has `AWS_DEPLOY_ROLE_ARN` and
the right `SUBDOMAIN`.

- [ ] **Step 7: Branch rule negative test (Review Focus 1)**

Push the branch, then dispatch the branch's workflow version:

```bash
git push -u origin dev-prod-environments
gh workflow run deploy.yml --ref dev-prod-environments -f action=destroy -f environment=dev
sleep 15; gh run list --workflow deploy.yml --limit 1
gh run view "$(gh run list --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```
Expected: the `destroy` job fails before any step runs with an
environment protection error ("Branch … is not allowed to deploy to
dev"). If GitHub rejects the dispatch outright because `main`'s
workflow lacks the `environment` input, note that and skip — the rule
is still exercised by the setting check in Step 6.

- [ ] **Step 8: PR and merge**

```bash
gh pr create --title "Dev and prod environments" --body "$(cat <<'EOF'
Splits the AWS deployment into independent dev and prod environments
(design: docs/superpowers/specs/2026-09-26-dev-prod-environments-design.md,
plan: docs/superpowers/plans/2026-09-26-dev-prod-environments.md).

- Per-environment deploy roles and EC2 boundaries (deploy-role.yaml)
- Shared ECR repo, tags immutable
- deploy.yml takes an environment input; build always in dev
- Daily auto-destroy after 72 hours; instance-size IAM caps

The new deploy-role stacks and GitHub Environments already exist; the
old role and repo secret are removed in a follow-up after live
verification.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
gh pr checks --watch
gh pr merge --squash --delete-branch
```
Merge only once CI is green.

---

### Task 10: Live verification (from cold)

Every `gh workflow run` here creates or deletes AWS resources through
the scoped roles: confirm with the user before starting this task (one
approval for the sequence below is fine to ask for; any extra
`aws ... send-command` still needs its own). Costs a few cents.

- [ ] **Step 1: Deploy dev**

```bash
gh workflow run deploy.yml -f action=deploy -f environment=dev
sleep 10; gh run watch "$(gh run list --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
curl -s -o /dev/null -w "%{http_code}\n" https://dev.katban-10x-cat-productivity.lighfe.dev/api/health
```
Expected: run succeeds; `200`. On an `AccessDenied`, read the stack
events (`aws cloudformation describe-stack-events --stack-name
kanban-app-dev --max-items 20`), find the exact action and resource,
and fix that statement narrowly — don't widen to `*`.

- [ ] **Step 2: Warm redeploy dev with a new image**

```bash
gh workflow run deploy.yml -f action=deploy -f environment=dev
sleep 10; gh run watch "$(gh run list --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
scripts/with-secrets AWS_PROFILE -- aws cloudformation describe-stacks --region eu-central-1 \
  --stack-name kanban-app-dev --query "Stacks[0].Parameters[?ParameterKey=='ImageTag'].ParameterValue" --output text
```
Then, with approval, check the running container's image:

```bash
iid=$(scripts/with-secrets AWS_PROFILE -- aws cloudformation describe-stacks --region eu-central-1 \
  --stack-name kanban-app-dev --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)
cmd=$(scripts/with-secrets AWS_PROFILE -- aws ssm send-command --region eu-central-1 \
  --instance-ids "$iid" --document-name AWS-RunShellScript \
  --parameters '{"commands":["docker ps --format {{.Image}}"]}' --query Command.CommandId --output text)
sleep 5
scripts/with-secrets AWS_PROFILE -- aws ssm get-command-invocation --region eu-central-1 \
  --command-id "$cmd" --instance-id "$iid" --query StandardOutputContent --output text
```
Expected: the `kanban` image line ends with the new `ImageTag` from the
second run, not the first. This exercises the Stop/Modify/Start + SSM
path that failed in step 6.

- [ ] **Step 3: Deploy prod, both up**

```bash
gh workflow run deploy.yml -f action=deploy -f environment=prod
sleep 10; gh run watch "$(gh run list --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
curl -s -o /dev/null -w "%{http_code}\n" https://katban-10x-cat-productivity.lighfe.dev/api/health
curl -s -o /dev/null -w "%{http_code}\n" https://dev.katban-10x-cat-productivity.lighfe.dev/api/health
```
Expected: both `200`.

- [ ] **Step 4: Destroy dev, prod still serves**

```bash
gh workflow run deploy.yml -f action=destroy -f environment=dev
sleep 10; gh run watch "$(gh run list --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
curl -s -o /dev/null -w "%{http_code}\n" https://katban-10x-cat-productivity.lighfe.dev/api/health
```
Expected: run succeeds; prod `200`.

- [ ] **Step 5: Auto-destroy with a young stack is a no-op**

```bash
gh workflow run auto-destroy.yml
sleep 10; gh run watch "$(gh run list --workflow auto-destroy.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
```
Expected: default 72 — prod job logs `created …, 0h ago (limit 72h)`,
skips Destroy; dev job logs `No stack kanban-app-dev`. Prod still `200`.

- [ ] **Step 6: Auto-destroy with `max_age_hours=0` destroys prod**

```bash
gh workflow run auto-destroy.yml -f max_age_hours=0
sleep 10; gh run watch "$(gh run list --workflow auto-destroy.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
```
Expected: prod job runs Destroy and succeeds; dev job no-op.

- [ ] **Step 7: Second run with nothing up is a no-op**

Repeat Step 6. Expected: both jobs log `No stack …` and succeed.

- [ ] **Step 8: Invalid input fails fast (Review Focus 3)**

```bash
gh workflow run auto-destroy.yml -f max_age_hours=abc
sleep 10; gh run watch "$(gh run list --workflow auto-destroy.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status || true
```
Expected: both jobs fail at `Validate max age`; no credentials step ran.

- [ ] **Step 9: Final state (read-only)**

```bash
scripts/with-secrets AWS_PROFILE -- aws cloudformation describe-stacks --region eu-central-1 \
  --query "Stacks[].StackName" --output text
scripts/with-secrets AWS_PROFILE -- aws route53 list-resource-record-sets \
  --hosted-zone-id Z0050563VZ2HM7AAD71F --query "ResourceRecordSets[?Type=='A'].Name" --output text
```
Expected: only `kanban-bootstrap`, `kanban-deploy-dev`,
`kanban-deploy-prod`; no `A` record for either hostname.

---

### Task 11: Cutover — remove the old pieces, close out

**Files:**
- Modify: `deploy/cloudformation/bootstrap.yaml`
- Modify: `deploy/README.md` (setup step 2), `_docs/deployment-plan.md`
  (step 7 header and verification)

- [ ] **Step 1: Branch and shrink `bootstrap.yaml`**

```bash
git switch main && git pull && git switch -c dev-prod-cleanup
```

In `bootstrap.yaml` delete: the `HostedZoneId` parameter, the
`Ec2RoleBoundary` resource with the comment block above it, the
`DeployRole` resource, and the `DeployRoleArn` output. Replace the
`Description` with:

```yaml
Description: >
  Shared, one-time account setup for the mini-kanban-board deploy
  pipeline: the GitHub OIDC provider and the ECR repository both
  environments use. Applied by hand with the admin profile - see
  deploy/README.md. The per-environment deploy roles live in
  deploy-role.yaml.
```

Run: `grep -n "HostedZoneId\|DeployRole\|Ec2RoleBoundary" deploy/cloudformation/bootstrap.yaml`
Expected: no output. Then `uvx cfn-lint deploy/cloudformation/bootstrap.yaml` → exit 0.

- [ ] **Step 2: (AWS) Apply**

```bash
scripts/with-secrets AWS_PROFILE -- aws cloudformation deploy --region eu-central-1 \
  --template-file deploy/cloudformation/bootstrap.yaml \
  --stack-name kanban-bootstrap \
  --capabilities CAPABILITY_NAMED_IAM
```
Then (read-only): `scripts/with-secrets AWS_PROFILE -- aws iam get-role --role-name kanban-app-deploy`
→ `NoSuchEntity`.

- [ ] **Step 3: Delete the repo-level secret**

```bash
gh secret delete AWS_DEPLOY_ROLE_ARN
gh secret list
```
Expected: no repo-level `AWS_DEPLOY_ROLE_ARN` (the environment secrets
remain).

- [ ] **Step 4: Docs**

- `deploy/README.md` setup step 2: remove
  `--parameter-overrides HostedZoneId=<zone-id> \` from the bootstrap
  command.
- `_docs/deployment-plan.md` step 7: change `— in progress` to
  `— done` and add a short verification paragraph with what Task 10
  actually observed (run results, the image tag check, auto-destroy
  runs, final stack list), including anything that needed fixing live.

- [ ] **Step 5: Commit, PR, merge**

```bash
git add deploy/cloudformation/bootstrap.yaml deploy/README.md _docs/deployment-plan.md
git commit -m "deploy: remove the old shared deploy role; mark step 7 done"
git push -u origin dev-prod-cleanup
gh pr create --title "Remove the old shared deploy role" --body "$(cat <<'EOF'
Follow-up to the dev/prod environments PR, after live verification:
removes kanban-app-deploy and kanban-app-ec2-boundary from
kanban-bootstrap (already applied), deletes the repo-level
AWS_DEPLOY_ROLE_ARN secret, and records the verification in
deployment-plan.md step 7.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
gh pr checks --watch
gh pr merge --squash --delete-branch
```
