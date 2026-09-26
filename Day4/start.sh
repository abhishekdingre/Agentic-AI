#!/usr/bin/env bash
# Start the full observability stack and then the SIP Calculator backend
# with OpenTelemetry instrumentation enabled.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "==> Starting observability stack (OTel Collector + Prometheus + Tempo + Grafana)..."
docker compose -f "$ROOT/docker-compose.yaml" up -d

echo "==> Waiting for the collector to be ready (10 s)..."
sleep 10

echo "==> Verifying collector is accepting connections on :4318..."
if curl -sf http://localhost:4318 >/dev/null 2>&1 || \
   curl -sf http://localhost:4318/v1/traces -o /dev/null --max-time 2 2>&1 | grep -qv "000"; then
  echo "    Collector is up."
else
  echo "    (Collector not yet responding — continuing anyway; it may still be starting.)"
fi

echo "==> Starting SIP Calculator backend with OpenTelemetry instrumentation..."
echo "    Traces and metrics will be sent to http://localhost:4318"
echo "    Grafana:    http://localhost:3000"
echo "    Prometheus: http://localhost:9090"
echo ""

cd "$ROOT/backend"

OTEL_SERVICE_NAME=sip-calculator-backend \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
exec node --require ./otel-setup.js server.js
