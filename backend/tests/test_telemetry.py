from fastapi import FastAPI

from kanban.telemetry import setup_telemetry


def test_setup_is_a_noop_without_an_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    app = FastAPI()
    assert setup_telemetry(app) is False
    assert app.user_middleware == []


def test_main_app_has_no_otel_middleware_in_tests():
    from kanban.main import app

    # instrument_app wraps build_middleware_stack instead of adding to
    # user_middleware; this flag is what it sets.
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)
