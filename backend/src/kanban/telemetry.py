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
