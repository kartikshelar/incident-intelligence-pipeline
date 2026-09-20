"""M6 observability: tracing, structured logs, /metrics, cost per document.

Submodules:
  tracing   OTel tracer provider setup (console exporter by default, OTLP
            configurable) and the span helpers the pipeline uses.
  logctx    Structured JSON logging with a correlation id shared with the
            active trace.
  cost      Cost-per-document from stored token counts (app.settings'
            PRICE_* env vars), the same convention scripts/extraction_run.py
            already uses.
  metrics   The /metrics registry: DB-derived gauges plus the histograms
            tracing.py records as spans complete.

Nothing here is a hard dependency for the rest of the app: every module
that imports app.telemetry (worker, api, extract) still runs if the OTel
SDK is present but unconfigured (console exporter, the default) — there is
no "telemetry off" mode, per M6's brief, but a failure to export spans
must never fail a job.
"""
