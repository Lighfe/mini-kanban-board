# Observability

The app sends OpenTelemetry traces, metrics and logs (OTLP) to a
collector next to it. Locally the collector exports to Prometheus, Loki
and Tempo with Grafana on top (this directory); dev and prod export to
Grafana Cloud ([deploy/otel-collector.yaml](../deploy/otel-collector.yaml)).
Design: [docs/superpowers/specs/2026-10-01-observability-design.md](../docs/superpowers/specs/2026-10-01-observability-design.md).

## Local

```bash
make up-obs     # app + Postgres + collector, Prometheus, Loki, Tempo, Grafana
make down-obs
```

App at http://localhost:8000, Grafana at http://localhost:3000
(anonymous admin, loopback only; Grafana 13 marks the anonymous Admin
role deprecated but still honors it).

- Explore → Prometheus: `http_server_request_duration_seconds_count`,
  `kanban_*_total`; filter on `deployment_environment_name="local"`.
- Explore → Tempo: service `kanban`; request spans with SQLAlchemy
  child spans.
- Explore → Loki: `{service_name="kanban"}`.
- Dashboards → kanban → Kanban.
- Alerting → Alert rules → Kanban server errors. Locally it notifies
  nobody; stopping the `db` service while signed in fires it within
  about 2 minutes.

Metrics are exported every 60 s, so new data takes up to a minute to
show up.

## Grafana Cloud setup (once, by the owner)

1. Create a free account and stack.
2. Connections → OpenTelemetry (OTLP): note the OTLP endpoint and
   instance ID.
3. Create an access policy with `metrics:write`, `logs:write`,
   `traces:write`, and two tokens: `kanban-dev`, `kanban-prod`.
4. Dashboards → Import `grafana/dashboards/kanban.json`; pick the
   stack's Prometheus and Loki data sources.
5. Alerting → Contact points: email to the owner's address.
6. Alerting → Notification policies: default policy to that contact
   point, grouped by `alertname, deployment_environment_name`, repeat
   interval 4h.
7. Alerting → Alert rules → New, as in
   `grafana/provisioning/alerting/kanban.yaml`: name `Kanban server
   errors`, the stack's Prometheus data source, query (instant)

   ```promql
   sum by (deployment_environment_name, service_version) (
   (http_server_request_duration_seconds_count{http_response_status_code=~"5.."}
   unless http_server_request_duration_seconds_count{http_response_status_code=~"5.."} offset 5m)
   or increase(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[5m]))
   ```

   threshold `> 2`, evaluated every 1m, pending period 0s, no data →
   **OK** (a destroyed environment must not alert), summary
   annotation as in the file, linked to the Kanban dashboard panel
   "Server errors (5xx) by route".

## Secrets (once per environment, admin profile)

The deploy reads `kanban-app-<env>-otlp` on the instance. It lives
outside the app stack, so it survives destroys. The owner runs this,
so the token never passes through an agent:

```bash
read -rs TOKEN   # paste the kanban-<env> token
scripts/with-secrets AWS_PROFILE -- aws secretsmanager create-secret \
  --region eu-central-1 --name kanban-app-<env>-otlp \
  --secret-string "$(jq -cn --arg e '<endpoint>' --arg i '<instance id>' --arg t "$TOKEN" \
    '{endpoint:$e, instance_id:$i, token:$t}')"
unset TOKEN
```

## Concepts

- **Signals:** metrics for dashboards and alerts, traces for one
  request's steps, logs for single events (with the trace ID).
- **Collector:** the app only knows "send OTLP here"; the collector
  batches, caps its memory, and holds the backend credentials.
- **Cardinality:** each label combination is a time series; labels
  only take values from small fixed sets.

More in the spec's [Concepts](../docs/superpowers/specs/2026-10-01-observability-design.md#concepts).
