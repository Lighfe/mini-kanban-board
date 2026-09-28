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
