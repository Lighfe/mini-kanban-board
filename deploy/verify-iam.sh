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
export AWS_DEFAULT_REGION=$REGION
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
SGRULE=arn:aws:ec2:$REGION:$ACCOUNT:security-group-rule/sgr-0123456789abcdef0
# Other resources RunInstances is authorized on (no conditions expected).
RUN_OTHER=(
  "arn:aws:ec2:$REGION::image/ami-0123456789abcdef0"
  "arn:aws:ec2:$REGION:$ACCOUNT:subnet/subnet-0123456789abcdef0"
  "arn:aws:ec2:$REGION:$ACCOUNT:network-interface/eni-0123456789abcdef0"
  "arn:aws:ec2:$REGION:$ACCOUNT:volume/vol-0123456789abcdef0"
)

host() { if [ "$1" = prod ]; then echo "$PROD_HOST"; else echo "$DEV_HOST"; fi; }
stack() { echo "arn:aws:cloudformation:$REGION:$ACCOUNT:stack/kanban-app-$1/00000000-0000-0000-0000-000000000000"; }
secret() { echo "arn:aws:secretsmanager:$REGION:$ACCOUNT:secret:kanban-app-$1-db-AbCdEf"; }
role() { echo "arn:aws:iam::$ACCOUNT:role/kanban-app-$1-ec2"; }
boundary() { echo "arn:aws:iam::$ACCOUNT:policy/kanban-app-$1-ec2-boundary"; }
db() { echo "arn:aws:rds:$REGION:$ACCOUNT:db:kanban-app-$1-dbinstance-abc123"; }
subgrp() { echo "arn:aws:rds:$REGION:$ACCOUNT:subgrp:kanban-app-$1-dbsubnetgroup-abc123"; }

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
# shellcheck disable=SC2086  # deliberate word splitting into ctx values
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
  check denied  "$E" rds:CreateDBInstance "$(db "$O")" "$(ctx rds:DatabaseClass string db.t4g.micro)"
  check allowed "$E" rds:CreateDBInstance "$(subgrp "$E")"
  check allowed "$E" rds:CreateDBInstance "arn:aws:rds:$REGION:$ACCOUNT:pg:default.postgres17"
  check allowed "$E" rds:CreateDBInstance "arn:aws:rds:$REGION:$ACCOUNT:og:default:postgres-17"
  check allowed "$E" rds:CreateDBSubnetGroup "$(subgrp "$E")"
  check denied  "$E" rds:CreateDBSubnetGroup "$(subgrp "$O")"
  check allowed "$E" rds:DeleteDBInstance "$(db "$E")"
  check denied  "$E" rds:DeleteDBInstance "$(db "$O")"
  check allowed "$E" ec2:RunInstances "$INSTANCE" "$(ctx ec2:InstanceType string t3.micro)"
  check denied  "$E" ec2:RunInstances "$INSTANCE" "$(ctx ec2:InstanceType string t3.large)"
  check allowed "$E" ec2:RunInstances "$SG" "$(stack_tag "$E")"
  check denied  "$E" ec2:RunInstances "$SG" "$(stack_tag "$O")"
  check allowed "$E" ec2:TerminateInstances "$INSTANCE" "$(stack_tag "$E")"
  check denied  "$E" ec2:TerminateInstances "$INSTANCE" "$(stack_tag "$O")"
  for res in "${RUN_OTHER[@]}"; do
    check allowed "$E" ec2:RunInstances "$res"
  done
  for action in AuthorizeSecurityGroupIngress AuthorizeSecurityGroupEgress \
                RevokeSecurityGroupIngress RevokeSecurityGroupEgress; do
    check allowed "$E" "ec2:$action" "$SG" "$(stack_tag "$E")"
    check denied  "$E" "ec2:$action" "$SG" "$(stack_tag "$O")"
  done
  # Authorize also creates a rule resource; Revoke is only authorized on
  # the group (the simulator denies Revoke on a rule ARN outright).
  check allowed "$E" ec2:AuthorizeSecurityGroupIngress "$SGRULE"
  check allowed "$E" ec2:AuthorizeSecurityGroupEgress "$SGRULE"
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
check allowed dev  ecr:DescribeImages "$ECR"
check allowed prod ecr:DescribeImages "$ECR"

if [ "$failures" -eq 0 ]; then
  echo "All checks passed"
else
  echo "$failures check(s) failed"
  exit 1
fi
