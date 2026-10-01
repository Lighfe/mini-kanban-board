# Observability — design

Date: 2026-10-01. Builds on [deployment-plan.md](../../deployment-plan.md)
steps 6–8. Covers phase 3+ of the course module
[DevOps and observability for an AI app](https://aishippingblog.com/p/devops-and-observability-for-an-ai):
OpenTelemetry instrumentation, a collector, a Grafana backend, app
metrics and one actionable alert. Becomes step 9 of the deployment plan.
The course's AI on-call agent is out of scope (step 10).

## Goal

- Dev and prod send traces, metrics and logs to Grafana Cloud, labeled
  by environment and image tag, and the history survives destroys.
- Repeated server errors in either environment email the owner; a
  planned destroy doesn't.
- The course's self-hosted stack (Prometheus, Loki, Tempo, Grafana) runs
  locally against `make up`.

Success: after a deploy, Grafana Cloud shows requests, a trace with its
DB spans, and app logs for that environment and tag; locally, stopping
Postgres makes the alert fire.

## Concepts

- **Signals.** Metrics are cheap aggregates for dashboards and alerts
  (rate, latency, error count). Traces show one request's steps (spans)
  and where its time went. Logs are individual events; with OTel each
  log line carries the trace ID of the request that wrote it.
- **OTLP** is OpenTelemetry's wire format. The app only knows "send OTLP
  to this endpoint"; which backend stores it is the collector's config.
  That's what makes a later switch to (or addition of) CloudWatch a
  collector change, not an app change.
- **Collector.** Receives OTLP, batches it, caps its own memory, adds
  attributes and exports to one or more backends. Keeps the backend
  token out of the app.
- **Resource attributes** describe who sent the data (`service.name`,
  `deployment.environment.name`, `service.version`); dashboards and
  alerts filter on them.
- **Cardinality.** Every distinct combination of metric labels is its
  own time series. Labels only take values from small fixed sets, never
  IDs or raw URLs; series count is what free-tier limits and paid bills
  are based on.

## Decisions

- **Course tooling: OpenTelemetry + Grafana.** AWS-native (CloudWatch)
  is a possible later step, mostly collector exporters, IAM and
  re-creating the alert.
- **Grafana Cloud free tier for dev and prod, self-hosted stack only
  locally.** The full stack needs about 1.5–2 GB RAM: it doesn't fit next
  to the app on the t3.micro, a persistent instance for it would exceed
  the $10 monthly budget, and an ephemeral one loses history. One Grafana
  Cloud stack for both environments, separated by
  `deployment.environment.name`.
- **Collector sidecar, not direct SDK export.** Matches the course,
  keeps the token out of the app, and the app's OTel config is the same
  locally and deployed.
- **Telemetry off unless configured.** Without an OTLP endpoint the SDK
  is disabled, so pytest, CI e2e and plain `make up` are unchanged.
- **Only one alert: server 5xx, both environments.** It only sees data
  from a running app, so destroys stay silent. No uptime probe: it would
  fire on every planned destroy.
- **Token in Secrets Manager, not GitHub.** GitHub keeps only the OIDC
  role ARN (step 6), and passing the token as an SSM command parameter
  would leave it in the command history.

## App instrumentation (`backend/`)

- Dependencies: `opentelemetry-distro`, `opentelemetry-exporter-otlp`,
  FastAPI and SQLAlchemy instrumentation.
- Setup in code or via `opentelemetry-instrument` in the Dockerfile
  `CMD`; the plan picks one. One uvicorn worker as before.
- Python `logging` bridged to OTel logs, so the `logger.exception` in
  `locking.py` reaches Loki with its trace ID. Logs still go to stdout.
- Custom metrics:
  - `kanban.commit.failures` — the rollback path in `locking.py`.
  - `kanban.boards.created`, `kanban.cards.moved`,
    `kanban.share_links.redeemed` (label `role`).
- Configured only through standard `OTEL_*` variables:
  `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME=kanban`,
  `OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=<env>,service.version=<image tag>`.

## Collector

- `otel/opentelemetry-collector-contrib`, pinned version, config in the
  repo.
- Pipelines for traces, metrics and logs: `otlp` receiver →
  `memory_limiter`, `batch` → exporter.
- Deployed: `otlphttp` to Grafana Cloud's OTLP gateway with basic auth
  (instance ID + token). Local: to the local Prometheus, Loki and Tempo.

## Deployed environments

- Compose (in both `UserData` and the SSM script in
  `.github/actions/deploy-env`, kept identical as today) gains an
  `otel-collector` service and the app's `OTEL_*` variables. Environment
  name from the stack name, version from the image tag.
- Secret `kanban-app-<env>-otlp` (JSON: endpoint, instance ID, token),
  one per environment with its own write-only Grafana Cloud access
  policy token. Created once by hand with the admin profile, outside the
  app stack, so it survives destroys. The deploy script reads it on the
  instance like the DB password.

## IAM

- `stack.yaml`: the instance role gets `secretsmanager:GetSecretValue`
  on its own `-otlp` secret.
- No change to the EC2 boundary (already allows `kanban-app-<env>-*`
  secrets) or `deploy-role.yaml`; `verify-iam.sh` must still pass.
- Outbound HTTPS is already allowed by the default security group
  egress.

## Local stack (`observability/`)

- Compose project with the collector, Prometheus, Loki, Tempo and
  Grafana, started alongside `make up` (the plan picks the mechanism,
  e.g. an override file and a Makefile target).
- Grafana provisions its data sources, the dashboard and the alert rule
  from files in `observability/`. The same dashboard and rule are
  imported into Grafana Cloud once by hand, steps in
  `observability/README.md`.

## Dashboard and alert

- Dashboard: request rate, p95 latency, 5xx by route, the custom
  counters, recent error logs; variables for environment and version.
- Alert: at least 3 server 5xx in 5 minutes, per environment. Labels
  environment and version, dashboard link as an annotation. Email contact
  point, repeat interval 4 hours.

## Cost

Grafana Cloud free tier: $0 (limits to confirm at signup). Two secrets:
about $0.80/month. Data transfer: negligible. Budgets unchanged.

## Manual setup (needs approval)

- Grafana Cloud account and stack, two access policy tokens, contact
  point, dashboard and alert import (done by the owner).
- Two `aws secretsmanager create-secret` commands, one per environment.

## Verification

- Local: full stack up, use the app, see traces, metrics and logs in
  local Grafana; stop Postgres, the alert fires.
- `verify-iam.sh` passes.
- Live dev (auto-deploy from the merge): data labeled `dev` and the
  deployed tag arrives in Grafana Cloud; the alert rule evaluates and is
  Normal; a contact point test email arrives; `free -m` over SSM shows
  memory headroom.
- Live prod: promote the same tag; data arrives labeled `prod`,
  separate from dev. Then destroy both environments.

## Docs

- `deployment-plan.md`: step 9 with what was built and verified.
- `observability/README.md`: local stack, Grafana Cloud setup and
  secret format.
- `deploy/README.md`: the `-otlp` secret.

## Open questions

- How to cause a real 5xx in a deployed environment to test the alert
  without a debug endpoint. Stopping RDS or revoking its security group
  rule makes requests hang rather than fail fast. For now: the local
  test plus a contact point test.
- The compose config is duplicated in `UserData` and the SSM script; the
  collector adds to both. Deduplicating is out of scope.
- Whether Grafana Cloud's OTLP gateway turns `deployment.environment.name`
  and `service.version` into metric labels or only puts them on
  `target_info`. If only the latter, the collector copies them onto data
  points.
- Collector memory on the t3.micro. If tight: lower the `memory_limiter`
  cap or add swap.
