# Deploying

See [_docs/deployment-plan.md](../_docs/deployment-plan.md) (step 6) for
the design. This file is the operational how-to.

## One-time account setup (admin, run once)

Everything below uses an AdminAccess/SSO AWS CLI profile — it's separate
from the scoped role GitHub Actions uses for every deploy/destroy run.
Run `aws sso login` first if needed.

1. Look up the `lighfe.dev` Route 53 hosted zone ID:

   ```bash
   aws route53 list-hosted-zones-by-name --dns-name lighfe.dev. \
     --query "HostedZones[0].Id" --output text
   ```

   Strip the leading `/hostedzone/` from the result — you want just the
   ID (e.g. `Z0123456789ABC`).

2. Deploy the bootstrap stack (creates the GitHub OIDC provider, the
   scoped deploy IAM role, and the persistent ECR repository):

   ```bash
   aws cloudformation deploy \
     --template-file deploy/cloudformation/bootstrap.yaml \
     --stack-name kanban-bootstrap \
     --parameter-overrides HostedZoneId=<id-from-step-1> \
     --capabilities CAPABILITY_NAMED_IAM
   ```

   If `token.actions.githubusercontent.com` is already registered as an
   OIDC provider in this account (from another project), add
   `CreateOidcProvider=false` to `--parameter-overrides` — AWS only
   allows one provider per URL per account.

3. Read the outputs and wire the deploy role into GitHub Actions:

   ```bash
   aws cloudformation describe-stacks --stack-name kanban-bootstrap \
     --query "Stacks[0].Outputs"

   gh secret set AWS_DEPLOY_ROLE_ARN --body "<DeployRoleArn output>"
   ```

4. Set the non-secret configuration the workflow needs as repo
   **variables** (not secrets):

   ```bash
   aws ec2 describe-subnets \
     --filters "Name=default-for-az,Values=true" \
     --query "Subnets[*].{Id:SubnetId,Az:AvailabilityZone}"

   gh variable set AWS_HOSTED_ZONE_ID --body "<id-from-step-1>"
   gh variable set AWS_VPC_ID --body "<default VPC id>"
   gh variable set AWS_SUBNET_IDS --body "<subnet-a>,<subnet-b>"
   ```

   `AWS_SUBNET_IDS` needs exactly two subnets in different AZs — RDS
   requires a subnet group spanning ≥2 AZs even for a single-AZ
   instance. Any two default subnets from the query above work.

5. If this account has never created an RDS instance before, create the
   RDS service-linked role (a one-time per-account prerequisite; the
   scoped deploy role deliberately can't do this itself —
   `iam:CreateServiceLinkedRole` is broad enough that it belongs with
   the rest of this manual setup, not in CI's permissions):

   ```bash
   aws iam get-role --role-name AWSServiceRoleForRDS
   # NoSuchEntity -> create it:
   aws iam create-service-linked-role --aws-service-name rds.amazonaws.com
   ```

Re-running step 2 is safe (`cloudformation deploy` is idempotent) if the
IAM policy in `bootstrap.yaml` ever changes.

The app EC2 role carries the `kanban-app-ec2-boundary` permissions
boundary from `bootstrap.yaml`, and the deploy role can't add a boundary
to an existing role. If an app stack created before the boundary existed
is still up, run the destroy workflow before the next deploy.

## Running a deploy or a teardown

From the GitHub UI: **Actions → Deploy → Run workflow**, choose `deploy`
or `destroy`. Or from the CLI:

```bash
gh workflow run deploy.yml -f action=deploy
gh workflow run deploy.yml -f action=destroy
gh run watch
```

`deploy` builds the image, pushes it to ECR, runs
`aws cloudformation deploy` against `deploy/cloudformation/stack.yaml`,
points `katban-10x-cat-productivity.lighfe.dev` at the new instance's
public IP, and polls `/api/health` until it returns 200.

`destroy` deletes the DNS record and the CloudFormation stack (EC2 +
RDS). Database data is lost — this stack is designed to be ephemeral
(see deployment-plan.md step 6).
