# Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dev and prod send OpenTelemetry traces, metrics and logs through a collector sidecar to Grafana Cloud, with one 5xx alert by email; the course's self-hosted stack runs locally.

**Architecture:** The backend sets up the OTel SDK in code (`kanban/telemetry.py`), only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. A collector container next to the app receives OTLP and exports it: locally to Prometheus, Loki and Tempo (Grafana on top); deployed to Grafana Cloud with a token read from Secrets Manager on the instance. The dashboard and alert rule live in `observability/` and are provisioned locally, imported into Grafana Cloud once.

**Tech Stack:** opentelemetry-python SDK 1.45 / contrib 0.66b0, otel/opentelemetry-collector-contrib 0.162.0, Prometheus 3.15, Loki 3.7, Tempo 3.1, Grafana 13.2, Docker Compose, CloudFormation, GitHub Actions.

**Spec:** [docs/superpowers/specs/2026-10-01-observability-design.md](../specs/2026-10-01-observability-design.md)

## Global Constraints

- Telemetry off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set; pytest, CI e2e and `make up` unchanged.
- Resource attributes: `service.name=kanban`, `deployment.environment.name=<env>`, `service.version=<image tag>`.
- Metric labels only from small fixed sets (env, version, route template, status code, role). Never IDs or raw URLs.
- One uvicorn worker, as before.
- Secret name `kanban-app-<env>-otlp`, JSON keys `endpoint`, `instance_id`, `token`; created by hand outside the app stack.
- Only `stack.yaml`'s instance role changes in IAM; boundary and `deploy-role.yaml` unchanged; `deploy/verify-iam.sh` passes.
- Alert: more than 2 server 5xx in 5 minutes per environment; no-data state OK; email; repeat 4 hours.
- AWS create/change/delete commands and GitHub settings changes need the user's approval for that exact command. Secrets only via `scripts/with-secrets`; never read `~/.config/mini-kanban-board/secrets.env`.
- Image versions pinned (no `latest`).

## Review Focus

1. **A destroyed environment must not alert.** Its series disappear, so the rule sees no data; with Grafana's default no-data state that would notify. Pinned in Task 4 (no-data check with the app stopped).
2. **A broken telemetry path must not break the app.** Collector stopped or Grafana Cloud token wrong → requests still succeed; exports fail in the background. Pinned in Task 3 (collector stopped) and Task 1 (disabled path).
3. **Counters must not count non-events.** A redeem that changes nothing, or a failed move (400/403/404), must not increment. Pinned in Task 2.
4. **Labels must stay low-cardinality.** Counter data points carry exactly the documented attributes. Pinned in Task 2 (exact attribute dicts).
5. **The first errors of a new series may be undercounted** by `increase()` (a counter that first appears at 1 has no earlier sample). The local alert test generates well over the threshold and documents this. Pinned in Task 4.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `backend/src/kanban/telemetry.py` (new) | SDK setup (`setup_telemetry`) and the four app counters |
| `backend/src/kanban/main.py` | calls `setup_telemetry(app)` |
| `backend/src/kanban/locking.py`, `routers/{boards,tasks,share_links}.py` | increment counters |
| `backend/tests/test_telemetry.py` (new), `backend/tests/conftest.py` | in-memory metric reader, counter tests |
| `observability/docker-compose.yml` (new) | local stack, layered on the root compose file |
| `observability/otel-collector.yaml` (new) | local collector config |
| `observability/{prometheus,loki,tempo}.yaml` (new) | local backend configs |
| `observability/grafana/provisioning/...` (new) | data sources, dashboard provider, alert rule |
| `observability/grafana/dashboards/kanban.json` (new) | the dashboard (also imported into Grafana Cloud) |
| `observability/README.md` (new) | local use, Grafana Cloud setup, secret creation |
| `deploy/otel-collector.yaml` (new) | deployed collector config (Grafana Cloud exporter) |
| `.github/actions/deploy-env/action.yml` | collector service, OTel env, secret read |
| `deploy/cloudformation/stack.yaml` | instance role reads its `-otlp` secret |
| `.github/workflows/ci.yml` | validates both collector configs |
| `Makefile` | `up-obs`, `down-obs` |
| docs | `backend/README.md`, `deploy/README.md`, `docs/deployment-plan.md` |

Decisions taken here that the spec left open:
- **Setup in code**, not `opentelemetry-instrument`: explicit, testable, and the disabled path is a plain `if`.
- **Local stack as a compose override** (`-f docker-compose.yml -f observability/docker-compose.yml`), via `make up-obs`.
- **Deviation from the spec:** `UserData` is *not* changed. The SSM push in `deploy-env` runs after every CloudFormation deploy and recreates the app with the collector seconds after first boot, so only the SSM script gets the collector. This avoids a third copy of the config and the `${}` escaping of a collector config inside CloudFormation `Fn::Sub`. The collector config is a repo file the action embeds base64-encoded.
- **Resource attributes copied onto metric data points** in the collector (`transform` processor), so the same PromQL works locally and in Grafana Cloud whether or not the backend promotes them (spec open question 3).

---

### Task 1: SDK setup, off by default

**Files:**
- Create: `backend/src/kanban/telemetry.py`
- Modify: `backend/src/kanban/main.py`, `backend/pyproject.toml` (via `uv add`)
- Test: `backend/tests/test_telemetry.py`

**Interfaces:**
- Produces: `kanban.telemetry.setup_telemetry(app: FastAPI) -> bool` (True if enabled); counters `commit_failures`, `boards_created`, `cards_moved`, `share_links_redeemed` (OTel `Counter`s) used in Task 2.

- [ ] **Step 1: Add dependencies**

```bash
cd backend && uv add opentelemetry-sdk opentelemetry-exporter-otlp-proto-http \
  opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-sqlalchemy
```

Only the HTTP exporter: no `grpcio` in the image. Check `uv.lock` resolved SDK 1.45.x and instrumentations 0.66b0.

- [ ] **Step 2: Write the failing test**

`backend/tests/test_telemetry.py`:

```python
from fastapi import FastAPI

from kanban.telemetry import setup_telemetry


def test_setup_is_a_noop_without_an_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    app = FastAPI()
    assert setup_telemetry(app) is False
    assert app.user_middleware == []


def test_main_app_has_no_otel_middleware_in_tests():
    from kanban.main import app

    names = [m.cls.__name__ for m in app.user_middleware]
    assert "OpenTelemetryMiddleware" not in names
```

- [ ] **Step 3: Run it to see it fail**

Run: `cd backend && uv run pytest tests/test_telemetry.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'kanban.telemetry'`.

- [ ] **Step 4: Implement `telemetry.py`**

```python
"""OpenTelemetry setup and the app's own metrics.

Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set (docs/deployment-plan.md
step 9), so tests, CI and plain `make up` don't export anything. All
other configuration comes from the standard OTEL_* variables: the
exporters append /v1/traces etc. to the endpoint, and Resource.create()
reads OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES.

The counters use the OTel API's global meter. Until a MeterProvider is
set they are no-ops, so routers can increment them unconditionally.
Labels must come from small fixed sets: every distinct label
combination is its own time series.
"""

import logging
import os

from fastapi import FastAPI
from opentelemetry import metrics, trace

_meter = metrics.get_meter("kanban")
commit_failures = _meter.create_counter(
    "kanban.commit.failures", description="Requests whose commit failed and were answered 500")
boards_created = _meter.create_counter(
    "kanban.boards.created", description="Boards created via POST /api/boards")
cards_moved = _meter.create_counter(
    "kanban.cards.moved", description="Tasks moved between or within columns")
share_links_redeemed = _meter.create_counter(
    "kanban.share_links.redeemed", description="Redeems that added or upgraded a membership")


def setup_telemetry(app: FastAPI) -> bool:
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from kanban.db import engine

    # Stable HTTP semantic conventions: http.server.request.duration
    # (seconds) with http.response.status_code, instead of the older
    # http.server.duration (ms) with http.status_code.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")
    os.environ.setdefault("OTEL_SERVICE_NAME", "kanban")
    resource = Resource.create()

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    metrics.set_meter_provider(MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
    ))

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    # Root for the app's own loggers; "uvicorn" because uvicorn's logging
    # config stops propagation there (it logs unhandled exceptions as
    # uvicorn.error). Access logs stay stdout-only: traces cover them.
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("uvicorn").addHandler(handler)

    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=engine)
    return True
```

If an import path has moved in SDK 1.45 (the logs SDK was long under `_logs`), find the current one in the installed package (`uv run python -c "import opentelemetry.sdk._logs"`) and adjust; keep behavior.

- [ ] **Step 5: Wire it into `main.py`**

After `register_exception_handlers(app)`:

```python
from kanban.telemetry import setup_telemetry
...
register_exception_handlers(app)
setup_telemetry(app)
```

(put the import with the other `kanban.*` imports). `instrument_app` adds its middleware as the outermost layer, so it also records the 500s that `SerializeRequestsMiddleware` produces.

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest -q`
Expected: all pass (new tests included).

- [ ] **Step 7: Smoke test the enabled path**

Run with an endpoint nobody listens on; the app must still answer:

```bash
cd backend && OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:9 KANBAN_SECURE_COOKIES=false \
  KANBAN_DATABASE_URL=sqlite:///:memory: timeout 15 uv run uvicorn kanban.main:app --port 8011 &
sleep 4; curl -s localhost:8011/api/health; wait
```

Expected: `{"status":"ok"}`; export errors in the log are fine.

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/kanban/telemetry.py backend/src/kanban/main.py backend/tests/test_telemetry.py
git commit -m "feat(backend): OpenTelemetry setup, off unless an OTLP endpoint is set"
```

---

### Task 2: App counters

**Files:**
- Modify: `backend/tests/conftest.py`, `backend/src/kanban/locking.py`, `backend/src/kanban/routers/boards.py`, `backend/src/kanban/routers/tasks.py`, `backend/src/kanban/routers/share_links.py`
- Test: `backend/tests/test_telemetry.py`, `backend/tests/test_locking.py`

**Interfaces:**
- Consumes: counters from Task 1.
- Produces: fixture `metric_reader` and helper `counter_value(reader, name, attributes=None) -> float` in `tests/conftest.py`.

- [ ] **Step 1: Add the in-memory reader to `conftest.py`**

Append:

```python
from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

# One global MeterProvider for the whole session (OTel allows setting it
# once). kanban.telemetry's counters were created against the API's proxy
# meter and bind to this provider. setup_telemetry stays disabled in tests.
_metric_reader = InMemoryMetricReader()
otel_metrics.set_meter_provider(MeterProvider(metric_readers=[_metric_reader]))


@pytest.fixture
def metric_reader():
    return _metric_reader


def counter_value(reader, name, attributes=None):
    """Cumulative sum of a counter, optionally only the data point whose
    attributes equal `attributes` exactly. Tests compare before/after."""
    data = reader.get_metrics_data()
    total = 0
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name != name:
                    continue
                for point in m.data.data_points:
                    if attributes is None or dict(point.attributes) == attributes:
                        total += point.value
    return total
```

- [ ] **Step 2: Write the failing counter tests**

Append to `backend/tests/test_telemetry.py`:

```python
from tests.conftest import counter_value, signup
from tests.test_tasks import add_second_user_as_editor, setup_board


def test_creating_a_board_counts_once_without_labels(client, metric_reader):
    signup(client)
    before = counter_value(metric_reader, "kanban.boards.created", {})
    assert client.post("/api/boards", json={"name": "Work"}).status_code == 201
    assert counter_value(metric_reader, "kanban.boards.created", {}) == before + 1


def test_signup_seed_board_is_not_counted(client, metric_reader):
    before = counter_value(metric_reader, "kanban.boards.created")
    signup(client)
    assert counter_value(metric_reader, "kanban.boards.created") == before


def test_moving_a_task_counts_and_a_rejected_move_does_not(client, metric_reader):
    board_id, columns = setup_board(client)
    task = client.post(f"/api/boards/{board_id}/columns/{columns['Backlog']}/tasks",
                       json={"title": "t"}).json()
    before = counter_value(metric_reader, "kanban.cards.moved", {})
    ok = client.post(f"/api/boards/{board_id}/tasks/{task['id']}/move",
                     json={"toColumnId": columns["Doing"], "toIndex": 0})
    assert ok.status_code == 200
    missing = client.post(f"/api/boards/{board_id}/tasks/{task['id']}/move",
                          json={"toColumnId": "no-such-column", "toIndex": 0})
    assert missing.status_code == 404
    assert counter_value(metric_reader, "kanban.cards.moved", {}) == before + 1


def test_redeem_that_adds_a_member_counts_with_its_role(client, metric_reader):
    board_id, _ = setup_board(client)
    before = counter_value(metric_reader, "kanban.share_links.redeemed", {"role": "editor"})
    add_second_user_as_editor(client, board_id)  # Bob redeems an editor link
    assert counter_value(metric_reader, "kanban.share_links.redeemed", {"role": "editor"}) == before + 1


def test_redeem_without_change_is_not_counted(client, metric_reader):
    signup(client)
    board_id = client.get("/api/boards").json()[0]["id"]
    link = client.post(f"/api/boards/{board_id}/share-links", json={"role": "viewer"}).json()
    client.cookies.clear()
    signup(client, email="bob@example.com", name="Bob", password="pw")
    assert client.post("/api/share-links/redeem", json={"token": link["token"]}).json()["changed"] is True
    before = counter_value(metric_reader, "kanban.share_links.redeemed")
    again = client.post("/api/share-links/redeem", json={"token": link["token"]}).json()
    assert again["changed"] is False
    assert counter_value(metric_reader, "kanban.share_links.redeemed") == before
```

Check the move body field names against `MoveTaskBody` in `routers/tasks.py` and the existing `test_move_task_sets_column_and_reorders`.

In `backend/tests/test_locking.py`, extend `test_commit_failure_returns_500_and_rolls_back`: add `metric_reader` to its parameters, and

```python
    before = counter_value(metric_reader, "kanban.commit.failures", {})
    ... (existing body) ...
    assert counter_value(metric_reader, "kanban.commit.failures", {}) == before + 1
```

with `from tests.conftest import counter_value` at the top.

- [ ] **Step 3: Run to see them fail**

Run: `cd backend && uv run pytest tests/test_telemetry.py tests/test_locking.py -v`
Expected: the counter assertions FAIL (values unchanged); the signup and no-change tests pass already.

- [ ] **Step 4: Increment the counters**

`locking.py` (import `from kanban import telemetry`), in the commit `except`:

```python
            except Exception:
                logger.exception("commit failed; rolled back and answered 500")
                telemetry.commit_failures.add(1)
                db_session.rollback()
```

`routers/boards.py` `create_board`, before `return`: `telemetry.boards_created.add(1)`.

`routers/tasks.py` `move_task`, just before `return task`: `telemetry.cards_moved.add(1)`.

`routers/share_links.py` `redeem_share_link`, before the final `return` (the changed path only):
`telemetry.share_links_redeemed.add(1, {"role": link["role"]})`.

Each with `from kanban import telemetry`. A counted request whose commit then fails is also counted in `commit_failures`; that's acceptable for dashboards.

- [ ] **Step 5: Run the whole suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(backend): count commit failures, boards, moves and redeems"
```

---

### Task 3: Local observability stack

**Files:**
- Create: `observability/docker-compose.yml`, `observability/otel-collector.yaml`, `observability/prometheus.yaml`, `observability/loki.yaml`, `observability/tempo.yaml`, `observability/grafana/provisioning/datasources/datasources.yaml`
- Modify: `Makefile`

**Interfaces:**
- Consumes: the app's OTel env (Task 1).
- Produces: data source UIDs `prometheus`, `loki`, `tempo` (Task 4); metric labels `deployment_environment_name`, `service_version`, `http_route`, `http_response_status_code`.

- [ ] **Step 1: Collector config** — `observability/otel-collector.yaml`

```yaml
# Local collector: app -> OTLP -> Prometheus (metrics), Loki (logs),
# Tempo (traces). deploy/otel-collector.yaml is the deployed twin, with
# Grafana Cloud as the only exporter; keep the processors in sync.
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 100
    spike_limit_mib: 20
  # Copy who-sent-it onto every metric data point, so PromQL can filter
  # by environment and version whether or not the backend promotes
  # resource attributes to labels.
  transform/promote:
    metric_statements:
      - context: datapoint
        statements:
          - set(attributes["deployment.environment.name"], resource.attributes["deployment.environment.name"])
          - set(attributes["service.version"], resource.attributes["service.version"])
  batch: {}

exporters:
  otlphttp/prometheus:
    endpoint: http://prometheus:9090/api/v1/otlp
  otlphttp/loki:
    endpoint: http://loki:3100/otlp
  otlp/tempo:
    endpoint: tempo:4317
    tls:
      insecure: true

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp/tempo]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, transform/promote, batch]
      exporters: [otlphttp/prometheus]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/loki]
```

- [ ] **Step 2: Backend configs**

`observability/prometheus.yaml`:

```yaml
# Metrics arrive by OTLP push (--web.enable-otlp-receiver); nothing to scrape.
otlp:
  translation_strategy: UnderscoreEscapingWithSuffixes
storage:
  tsdb:
    out_of_order_time_window: 30m
```

`observability/loki.yaml`:

```yaml
auth_enabled: false
server:
  http_listen_port: 3100
common:
  instance_addr: 127.0.0.1
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
schema_config:
  configs:
    - from: 2026-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
limits_config:
  allow_structured_metadata: true
```

`observability/tempo.yaml`:

```yaml
server:
  http_listen_port: 3200
distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
storage:
  trace:
    backend: local
    local:
      path: /var/tempo/traces
    wal:
      path: /var/tempo/wal
```

Tempo 3 may have changed its single-binary config. If `tempo` exits on startup, read its log, fix the config against the 3.1 docs, and note the change in the commit message.

- [ ] **Step 3: Grafana data sources** — `observability/grafana/provisioning/datasources/datasources.yaml`

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    url: http://prometheus:9090
    isDefault: true
  - name: Loki
    uid: loki
    type: loki
    url: http://loki:3100
  - name: Tempo
    uid: tempo
    type: tempo
    url: http://tempo:3200
    jsonData:
      tracesToLogsV2:
        datasourceUid: loki
        filterByTraceID: true
```

- [ ] **Step 4: Compose override** — `observability/docker-compose.yml`

```yaml
# Local observability stack (docs/deployment-plan.md step 9), layered on
# the root docker-compose.yml: `make up-obs` / `make down-obs`. Paths are
# relative to the repo root (the first compose file's directory).
# Grafana: http://localhost:3000 (anonymous admin, loopback only).
services:
  app:
    environment:
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4318
      OTEL_RESOURCE_ATTRIBUTES: deployment.environment.name=local,service.version=local

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.162.0
    command: ["--config=/etc/otelcol/config.yaml"]
    volumes:
      - ./observability/otel-collector.yaml:/etc/otelcol/config.yaml:ro

  prometheus:
    image: prom/prometheus:v3.15.0
    command:
      - --config.file=/etc/prometheus/prometheus.yaml
      - --web.enable-otlp-receiver
    volumes:
      - ./observability/prometheus.yaml:/etc/prometheus/prometheus.yaml:ro

  loki:
    image: grafana/loki:3.7.8
    command: ["-config.file=/etc/loki/loki.yaml"]
    volumes:
      - ./observability/loki.yaml:/etc/loki/loki.yaml:ro

  tempo:
    image: grafana/tempo:3.1.0
    command: ["-config.file=/etc/tempo/tempo.yaml"]
    volumes:
      - ./observability/tempo.yaml:/etc/tempo/tempo.yaml:ro

  grafana:
    image: grafana/grafana:13.2.3
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: Admin
      GF_AUTH_DISABLE_LOGIN_FORM: "true"
    volumes:
      - ./observability/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./observability/grafana/dashboards:/var/lib/grafana/dashboards:ro
    ports:
      - "127.0.0.1:3000:3000"
```

Create an empty `observability/grafana/dashboards/.gitkeep` until Task 4 adds the dashboard.

- [ ] **Step 5: Makefile targets**

Add `up-obs down-obs` to `.PHONY` and:

```make
OBS = -f docker-compose.yml -f observability/docker-compose.yml

# The app plus the local observability stack (observability/README.md).
up-obs:
	docker compose $(OBS) up --build

down-obs:
	docker compose $(OBS) down
```

- [ ] **Step 6: Validate the collector config**

```bash
docker run --rm -v "$PWD/observability/otel-collector.yaml:/cfg.yaml:ro" \
  otel/opentelemetry-collector-contrib:0.162.0 validate --config=/cfg.yaml
```

Expected: exit 0, no output.

- [ ] **Step 7: Run it and look at all three signals**

```bash
make up-obs   # in a second terminal, or with -d via: docker compose $(OBS) up --build -d
```

Then sign up, create a board, move a card in the browser at http://localhost:8000. In Grafana (http://localhost:3000 → Explore):
- Prometheus: `http_server_request_duration_seconds_count{deployment_environment_name="local"}` has series with `http_route` and `service_version="local"`; `kanban_boards_created_total` exists.
- Tempo: search service `kanban`; a `POST /api/boards/{boardId}/tasks/{taskId}/move` trace has SQLAlchemy child spans.
- Loki: `{service_name="kanban"}` shows log lines (trigger one with the commit-failure path or any `logger.info`; uvicorn startup lines arrive via the `uvicorn` logger).

If a metric or label name differs from the above (translation strategy, semconv version), record the actual names; Task 4's queries use them.

- [ ] **Step 8: Telemetry path broken, app fine**

```bash
docker compose -f docker-compose.yml -f observability/docker-compose.yml stop otel-collector
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/api/health   # 200
docker compose -f docker-compose.yml -f observability/docker-compose.yml start otel-collector
```

Expected: 200 while the collector is down.

- [ ] **Step 9: Commit**

```bash
git add observability Makefile
git commit -m "feat(observability): local collector, Prometheus, Loki, Tempo and Grafana"
```

---

### Task 4: Dashboard and alert rule

**Files:**
- Create: `observability/grafana/dashboards/kanban.json`, `observability/grafana/provisioning/dashboards/dashboards.yaml`, `observability/grafana/provisioning/alerting/kanban.yaml`
- Delete: `observability/grafana/dashboards/.gitkeep`

**Interfaces:**
- Consumes: data source UIDs and metric names from Task 3.
- Produces: dashboard UID `kanban`, alert rule UID `kanban-5xx` (referenced in `observability/README.md`, Task 6).

- [ ] **Step 1: Dashboard provider** — `observability/grafana/provisioning/dashboards/dashboards.yaml`

```yaml
apiVersion: 1
providers:
  - name: kanban
    folder: kanban
    type: file
    options:
      path: /var/lib/grafana/dashboards
```

- [ ] **Step 2: Dashboard** — `observability/grafana/dashboards/kanban.json`

Data sources are variables (`ds_prom`, `ds_loki`) so the same file imports into Grafana Cloud, whose data source UIDs differ.

```json
{
  "uid": "kanban",
  "title": "Kanban",
  "schemaVersion": 39,
  "time": { "from": "now-1h", "to": "now" },
  "refresh": "30s",
  "templating": {
    "list": [
      { "name": "ds_prom", "label": "Metrics", "type": "datasource", "query": "prometheus" },
      { "name": "ds_loki", "label": "Logs", "type": "datasource", "query": "loki" },
      {
        "name": "env", "label": "Environment", "type": "query",
        "datasource": { "type": "prometheus", "uid": "${ds_prom}" },
        "query": "label_values(http_server_request_duration_seconds_count, deployment_environment_name)",
        "includeAll": true, "multi": true, "refresh": 2
      },
      {
        "name": "version", "label": "Version", "type": "query",
        "datasource": { "type": "prometheus", "uid": "${ds_prom}" },
        "query": "label_values(http_server_request_duration_seconds_count{deployment_environment_name=~\"$env\"}, service_version)",
        "includeAll": true, "multi": true, "refresh": 2
      }
    ]
  },
  "panels": [
    {
      "id": 1, "type": "timeseries", "title": "Requests/s by route",
      "gridPos": { "x": 0, "y": 0, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "${ds_prom}" },
      "targets": [{ "refId": "A", "legendFormat": "{{http_route}}",
        "expr": "sum by (http_route) (rate(http_server_request_duration_seconds_count{deployment_environment_name=~\"$env\", service_version=~\"$version\"}[$__rate_interval]))" }]
    },
    {
      "id": 2, "type": "timeseries", "title": "p95 latency",
      "gridPos": { "x": 12, "y": 0, "w": 12, "h": 8 },
      "fieldConfig": { "defaults": { "unit": "s" }, "overrides": [] },
      "datasource": { "type": "prometheus", "uid": "${ds_prom}" },
      "targets": [{ "refId": "A", "legendFormat": "p95",
        "expr": "histogram_quantile(0.95, sum by (le) (rate(http_server_request_duration_seconds_bucket{deployment_environment_name=~\"$env\", service_version=~\"$version\"}[$__rate_interval])))" }]
    },
    {
      "id": 3, "type": "timeseries", "title": "Server errors (5xx) by route",
      "gridPos": { "x": 0, "y": 8, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "${ds_prom}" },
      "targets": [{ "refId": "A", "legendFormat": "{{deployment_environment_name}} {{http_route}} {{http_response_status_code}}",
        "expr": "sum by (deployment_environment_name, http_route, http_response_status_code) (increase(http_server_request_duration_seconds_count{deployment_environment_name=~\"$env\", service_version=~\"$version\", http_response_status_code=~\"5..\"}[$__rate_interval]))" }]
    },
    {
      "id": 4, "type": "timeseries", "title": "App events",
      "gridPos": { "x": 12, "y": 8, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "${ds_prom}" },
      "targets": [{ "refId": "A", "legendFormat": "{{__name__}}",
        "expr": "sum by (__name__) (increase({__name__=~\"kanban_.+_total\", deployment_environment_name=~\"$env\", service_version=~\"$version\"}[$__rate_interval]))" }]
    },
    {
      "id": 5, "type": "logs", "title": "Warnings and errors",
      "gridPos": { "x": 0, "y": 16, "w": 24, "h": 10 },
      "datasource": { "type": "loki", "uid": "${ds_loki}" },
      "targets": [{ "refId": "A",
        "expr": "{service_name=\"kanban\"} | deployment_environment_name=~\"$env\" | severity_text=~\"WARN.*|ERROR|CRITICAL\"" }]
    }
  ]
}
```

- [ ] **Step 3: Alert rule, contact point, policy** — `observability/grafana/provisioning/alerting/kanban.yaml`

```yaml
# Same rule as in Grafana Cloud (set up by hand, observability/README.md).
# noDataState OK: a destroyed environment sends nothing and must not alert.
apiVersion: 1
contactPoints:
  - orgId: 1
    name: local-log
    receivers:
      - uid: local-log
        type: webhook
        settings:
          url: http://localhost:9/discard   # local only: look at the alert list instead
policies:
  - orgId: 1
    receiver: local-log
    group_by: [alertname, deployment_environment_name]
    repeat_interval: 4h
groups:
  - orgId: 1
    name: kanban
    folder: kanban
    interval: 1m
    rules:
      - uid: kanban-5xx
        title: Kanban server errors
        condition: C
        data:
          - refId: A
            relativeTimeRange: { from: 600, to: 0 }
            datasourceUid: prometheus
            model:
              refId: A
              instant: true
              expr: >-
                sum by (deployment_environment_name, service_version)
                (increase(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[5m]))
          - refId: C
            datasourceUid: __expr__
            model:
              refId: C
              type: threshold
              expression: A
              conditions:
                - evaluator: { type: gt, params: [2] }
        noDataState: OK
        execErrState: Error
        for: 0s
        annotations:
          summary: >-
            {{ $labels.deployment_environment_name }}: server errors in the last 5 minutes
            (version {{ $labels.service_version }})
          __dashboardUid__: kanban
          __panelId__: "3"
```

- [ ] **Step 4: Restart Grafana and check provisioning**

```bash
docker compose -f docker-compose.yml -f observability/docker-compose.yml up -d --force-recreate grafana
docker compose -f docker-compose.yml -f observability/docker-compose.yml logs grafana | grep -iE "error|provision" | tail -20
```

Expected: no provisioning errors; Dashboards → kanban → Kanban shows data in panels 1, 2 and 4; Alerting → Alert rules shows `Kanban server errors` as Normal.

- [ ] **Step 5: Real 5xx → the alert fires**

```bash
jar=$(mktemp)
curl -s -o /dev/null -c "$jar" -H 'content-type: application/json' \
  -d '{"email":"alert-test@example.com","name":"x","password":"pw"}' localhost:8000/api/auth/signup
docker compose -f docker-compose.yml -f observability/docker-compose.yml stop db
for i in $(seq 1 15); do curl -s -o /dev/null -w "%{http_code} " -b "$jar" localhost:8000/api/boards; done; echo
```

Signed in, so the session lookup itself hits the stopped database. Expected: 500s. Within about 2 minutes the rule is Firing for `deployment_environment_name=local`; panel 3 shows the errors. Fewer than 3 errors may not fire: `increase()` misses a series' first sample (Review Focus 5).

- [ ] **Step 6: No data → no alert**

```bash
docker compose -f docker-compose.yml -f observability/docker-compose.yml start db
docker compose -f docker-compose.yml -f observability/docker-compose.yml stop app
```

Wait about 7 minutes. Expected: the rule goes back to Normal (not NoData/Alerting) while the app sends nothing. Start `app` again.

- [ ] **Step 7: Commit**

```bash
git add observability
git commit -m "feat(observability): kanban dashboard and 5xx alert rule"
```

---

### Task 5: Deployed collector, IAM and CI check

**Files:**
- Create: `deploy/otel-collector.yaml`
- Modify: `.github/actions/deploy-env/action.yml` (step "Push app update via SSM"), `deploy/cloudformation/stack.yaml` (`Ec2InstanceRole` policy), `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: secret `kanban-app-<env>-otlp` with JSON keys `endpoint`, `instance_id`, `token`.
- Produces: collector service `otel-collector` on the instance; app env `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_RESOURCE_ATTRIBUTES`.

- [ ] **Step 1: Deployed collector config** — `deploy/otel-collector.yaml`

```yaml
# Deployed collector (docs/deployment-plan.md step 9): app -> OTLP ->
# Grafana Cloud. The deploy-env action ships this file to the instance;
# the GRAFANA_* variables come from the kanban-app-<env>-otlp secret.
# observability/otel-collector.yaml is the local twin; keep the
# processors in sync.
extensions:
  basicauth/grafana:
    client_auth:
      username: ${env:GRAFANA_INSTANCE_ID}
      password: ${env:GRAFANA_TOKEN}

receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 100
    spike_limit_mib: 20
  transform/promote:
    metric_statements:
      - context: datapoint
        statements:
          - set(attributes["deployment.environment.name"], resource.attributes["deployment.environment.name"])
          - set(attributes["service.version"], resource.attributes["service.version"])
  batch: {}

exporters:
  otlphttp/grafana:
    endpoint: ${env:GRAFANA_OTLP_ENDPOINT}
    auth:
      authenticator: basicauth/grafana

service:
  extensions: [basicauth/grafana]
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/grafana]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, transform/promote, batch]
      exporters: [otlphttp/grafana]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/grafana]
```

Validate:

```bash
docker run --rm -v "$PWD/deploy/otel-collector.yaml:/cfg.yaml:ro" \
  -e GRAFANA_OTLP_ENDPOINT=https://example.invalid/otlp -e GRAFANA_INSTANCE_ID=0 -e GRAFANA_TOKEN=x \
  otel/opentelemetry-collector-contrib:0.162.0 validate --config=/cfg.yaml
```

Expected: exit 0.

- [ ] **Step 2: CI job** — in `.github/workflows/ci.yml`, a new job next to `backend-test`:

```yaml
  collector-config:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Validate collector configs
        run: |
          for f in deploy/otel-collector.yaml observability/otel-collector.yaml; do
            echo "== $f"
            docker run --rm -v "$PWD/$f:/cfg.yaml:ro" \
              -e GRAFANA_OTLP_ENDPOINT=https://example.invalid/otlp \
              -e GRAFANA_INSTANCE_ID=0 -e GRAFANA_TOKEN=x \
              otel/opentelemetry-collector-contrib:0.162.0 validate --config=/cfg.yaml
          done
```

`deploy.yml` runs on CI success, so this also gates dev deploys.

- [ ] **Step 3: Instance role** — `deploy/cloudformation/stack.yaml`, add to the `kanban-app-ec2` policy statements after `DbPassword`:

```yaml
              # Grafana Cloud OTLP credentials for the collector, created
              # by hand outside this stack (observability/README.md) so
              # they survive destroys. The EC2 boundary already allows
              # kanban-app-<env>-* secrets.
              - Sid: OtlpCredentials
                Effect: Allow
                Action: secretsmanager:GetSecretValue
                Resource: !Sub arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:${AWS::StackName}-otlp-*
```

Lint: `uvx cfn-lint deploy/cloudformation/stack.yaml` → no errors.

- [ ] **Step 4: deploy-env action**

In step "Push app update via SSM":

a) Add to the step's `env`: nothing new (it already has `STACK_NAME`, `IMAGE_TAG`, `REGION`). At the top of `run:`, after the `describe-stacks` lookups:

```bash
        env_name=${STACK_NAME#kanban-app-}
        collector_b64=$(base64 -w0 deploy/otel-collector.yaml)
```

b) In the rendered script, after the `DB_URL=` line:

```bash
        # Grafana Cloud OTLP credentials, read on the instance like the DB
        # password so they never appear in the SSM command.
        read -r OTLP_ENDPOINT OTLP_INSTANCE_ID OTLP_TOKEN < <(aws secretsmanager get-secret-value \
          --region ${REGION} --secret-id ${STACK_NAME}-otlp \
          --query SecretString --output text \
          | python3 -c 'import json,sys; s=json.load(sys.stdin); print(s["endpoint"], s["instance_id"], s["token"])')
```

c) After `mkdir -p /opt/kanban`:

```bash
        echo ${collector_b64} | base64 -d > /opt/kanban/otel-collector.yaml
```

d) In the compose heredoc, the `app` service's `environment` gains two lines and a new service follows `caddy`:

```yaml
              KANBAN_DATABASE_URL: "\${DB_URL}"
              OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4318
              OTEL_RESOURCE_ATTRIBUTES: deployment.environment.name=${env_name},service.version=${IMAGE_TAG}
```

```yaml
          otel-collector:
            image: otel/opentelemetry-collector-contrib:0.162.0
            command: ["--config=/etc/otelcol/config.yaml"]
            environment:
              GRAFANA_OTLP_ENDPOINT: "\${OTLP_ENDPOINT}"
              GRAFANA_INSTANCE_ID: "\${OTLP_INSTANCE_ID}"
              GRAFANA_TOKEN: "\${OTLP_TOKEN}"
            volumes:
              - /opt/kanban/otel-collector.yaml:/etc/otelcol/config.yaml:ro
            mem_limit: 150m
            restart: unless-stopped
```

Escaping follows the existing `DB_URL` pattern: `${env_name}`, `${IMAGE_TAG}`, `${collector_b64}` expand in Actions; `\${...}` expands on the instance. Add a comment above the step noting that `UserData` intentionally has no collector (this push runs after every CloudFormation deploy).

- [ ] **Step 5: Check the rendered script without AWS**

Render the script locally with dummy values and inspect it in full (the postmortem's lesson: read the whole order-sensitive file, not the hunk):

```bash
STACK_NAME=kanban-app-dev IMAGE_TAG=20261001-000000-abc1234 REGION=eu-central-1 \
ECR_URI=1.dkr.ecr.eu-central-1.amazonaws.com/kanban APP_HOSTNAME=dev.example.com \
db_secret_arn=arn:x db_endpoint=db.example env_name=dev collector_b64=$(base64 -w0 deploy/otel-collector.yaml) \
bash -c "$(sed -n '/cat > \/tmp\/update-app.sh <<SCRIPT/,/^        SCRIPT$/p' .github/actions/deploy-env/action.yml | sed 's/^        //')" \
&& bash -n /tmp/update-app.sh && cat /tmp/update-app.sh
```

Expected: `bash -n` passes; the script reads the secret with `--secret-id kanban-app-dev-otlp`, contains `deployment.environment.name=dev,service.version=20261001-000000-abc1234`, keeps `\${OTLP_TOKEN}` as `${OTLP_TOKEN}` (to expand on the instance), and the base64 line decodes back to `deploy/otel-collector.yaml` (`echo <b64> | base64 -d | diff - deploy/otel-collector.yaml`). Run actionlint: `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest` → no new findings.

- [ ] **Step 6: verify-iam.sh still passes** (read-only)

```bash
scripts/with-secrets AWS_PROFILE -- deploy/verify-iam.sh
```

Expected: `All checks passed` (deploy roles are unchanged; this guards against accidental edits).

- [ ] **Step 7: Commit**

```bash
git add deploy/otel-collector.yaml .github/actions/deploy-env/action.yml deploy/cloudformation/stack.yaml .github/workflows/ci.yml
git commit -m "feat(deploy): collector sidecar exporting to Grafana Cloud"
```

---

### Task 6: Docs

**Files:**
- Create: `observability/README.md`
- Modify: `backend/README.md`, `deploy/README.md`, `docs/deployment-plan.md`

- [ ] **Step 1: `observability/README.md`**, sections:
  - *Local:* `make up-obs`, Grafana at http://localhost:3000, what's where (Explore per signal, the Kanban dashboard, the alert rule), `make down-obs`.
  - *Grafana Cloud setup (once, by the owner):* create a free account and stack; Connections → OpenTelemetry (OTLP) gives the OTLP endpoint and instance ID; create an access policy with `metrics:write`, `logs:write`, `traces:write` and two tokens (`kanban-dev`, `kanban-prod`); import `grafana/dashboards/kanban.json` (pick the Grafana Cloud Prometheus and Loki data sources); create the alert rule by hand from `grafana/provisioning/alerting/kanban.yaml` (same query, threshold `> 2`, no data → OK, evaluation every 1m, summary and dashboard link); email contact point to the owner's address; default notification policy grouped by `alertname, deployment_environment_name`, repeat 4h.
  - *Secrets (once per environment, admin profile, needs approval):* the owner runs, so the token never passes through the agent:

    ```bash
    read -rs TOKEN   # paste the kanban-<env> token
    scripts/with-secrets AWS_PROFILE -- aws secretsmanager create-secret \
      --region eu-central-1 --name kanban-app-<env>-otlp \
      --secret-string "$(jq -cn --arg e '<endpoint>' --arg i '<instance id>' --arg t "$TOKEN" \
        '{endpoint:$e, instance_id:$i, token:$t}')"
    unset TOKEN
    ```

  - *Concepts:* three short bullets (signals, collector, cardinality) linking to the spec's concepts section.
- [ ] **Step 2: `backend/README.md`** — a "Telemetry" section: off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set; the `OTEL_*` variables used; the four counters.
- [ ] **Step 3: `deploy/README.md`** — in one-time setup, a step for the two `-otlp` secrets pointing to `observability/README.md`; note the deploy fails at the SSM step if the secret is missing.
- [ ] **Step 4: `docs/deployment-plan.md`** — step 9 heading "Observability — in progress": a short summary of what's built (link the spec), the UserData deviation, and the open questions from the spec (minus the ones resolved: label promotion is now handled by the collector). Verification is added in Task 9.
- [ ] **Step 5: Commit**

```bash
git add observability/README.md backend/README.md deploy/README.md docs/deployment-plan.md
git commit -m "docs: observability setup and step 9"
```

---

### Task 7: Grafana Cloud and secrets (manual, before merge)

Must be done before merging: the merge deploys dev, and the SSM step fails without `kanban-app-dev-otlp`.

- [ ] **Step 1:** The owner does the Grafana Cloud setup from `observability/README.md` and tells the agent the OTLP endpoint and instance ID (not secret).
- [ ] **Step 2:** The owner runs the two `create-secret` commands (dev, prod). The agent checks with a read-only call: `scripts/with-secrets AWS_PROFILE -- aws secretsmanager describe-secret --region eu-central-1 --secret-id kanban-app-dev-otlp --query Name` (same for prod).
- [ ] **Step 3:** Optional pre-merge check of the token without AWS: run the deployed collector config locally against Grafana Cloud by pointing `make up-obs`'s app at a collector started with `deploy/otel-collector.yaml` and the real env vars, entered by the owner. Skip if the owner prefers to verify on dev.

---

### Task 8: Review, PR, merge

- [ ] **Step 1:** Codex review of the branch (default model `gpt-6-astra`). If not finished after ~10 minutes: check its log, cancel, use what it produced. Triage by severity; fix what matters; if fixes change the design, leave them uncommitted and show the diff first.
- [ ] **Step 2:** Re-read the full `action.yml` and `stack.yaml` diffs against intent (postmortem lesson 3).
- [ ] **Step 3:** Push, open PR, wait for CI (including `collector-config`), merge. The merge triggers `deploy.yml` → dev.

---

### Task 9: Live verification (each command explained first, one approval each)

- [ ] **Step 1: Dev deploy.** Watch the `deploy.yml` run from the merge; it must pass the health check.
- [ ] **Step 2: Dev telemetry.** Use dev in the browser (sign up, board, move). In Grafana Cloud: metrics with `deployment_environment_name="dev"` and `service_version=<tag>`; a move trace with DB spans; logs from `service_name="kanban"`. Kanban dashboard shows dev.
- [ ] **Step 3: Alert path.** Rule evaluates and is Normal for dev; contact point "Test" sends an email that arrives.
- [ ] **Step 4: Memory.** Over SSM (approval): `free -m` and `docker stats --no-stream` on the dev instance. Record app/collector/caddy usage.
- [ ] **Step 5: Prod.** Promote the dev tag with `promote.yml` (approval in `prod-approval`). Data appears labeled `prod`, separate from dev on the dashboard.
- [ ] **Step 6: Destroy both** with `destroy.yml`; after ~10 minutes the alert rule is still Normal (no-data → OK in the cloud too).
- [ ] **Step 7: Record step 9** in `docs/deployment-plan.md`: mark done, add the verification (date, tags, memory numbers, anything that differed). Branch, PR, merge on green CI (this merge redeploys dev; destroy it again afterwards unless the owner says otherwise).
