# Observability Setup — SIP Calculator

Full-stack observability using OpenTelemetry, Prometheus, Grafana Tempo, and Grafana.

## Architecture

```
SIP Calculator Backend (Node.js + Express)
  └─ otel-setup.js (TracerProvider + MeterProvider)
       │
       │  OTLP HTTP :4318
       ▼
  OTel Collector
       ├─ Traces ──► Grafana Tempo  :4317 (internal) → query via :3200
       └─ Metrics ─► Prometheus scrape :9464 → stored at :9090
                                            ▼
                                       Grafana :3000
                              (Prometheus + Tempo pre-wired)
```

## Quick Start

```bash
./start.sh
```

This command:
1. Starts OTel Collector, Prometheus, Tempo, and Grafana via Docker Compose.
2. Waits 10 s for the collector to be ready.
3. Launches the Express backend (`backend/server.js`) with `--require ./otel-setup.js`
   so all HTTP spans and Node.js system metrics are captured automatically.

### Start only the observability stack (without the backend)

```bash
docker compose up -d
```

### Start only the backend with OTel (stack already running)

```bash
cd backend
OTEL_SERVICE_NAME=sip-calculator-backend \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
npm run start:otel
```

---

## Verifying Traces in Grafana

1. Open **http://localhost:3000** in your browser.
   - Login is not required (anonymous admin access is enabled for local dev).

2. In the left sidebar click **Explore** (compass icon).

3. Select the **Tempo** data source from the dropdown at the top-left.

4. Use **Search** tab → click **Run query**. You should see traces for
   `sip-calculator-backend` appearing within seconds of making a request.

5. To generate a trace: open a new tab and call the API:
   ```bash
   curl -s -X POST http://localhost:4000/api/sip/calculate \
     -H 'Content-Type: application/json' \
     -d '{"monthlyInvestment":5000,"annualReturnRate":12,"years":10}' | jq
   ```
   Refresh the Tempo query — you will see a new trace with an HTTP POST span.

---

## Verifying Metrics in Grafana

1. In Grafana **Explore**, switch the data source to **Prometheus**.

2. In the metric query box, type:
   ```
   sip_calculator_
   ```
   and browse the autocomplete list. Metrics prefixed `sip_calculator_` are
   emitted by the OpenTelemetry Collector's Prometheus exporter.

3. Try these example queries in the Prometheus Explore view:

   | What it shows | PromQL |
   |---|---|
   | HTTP request rate | `rate(sip_calculator_http_server_request_duration_milliseconds_count[1m])` |
   | P95 response latency | `histogram_quantile(0.95, rate(sip_calculator_http_server_request_duration_milliseconds_bucket[5m]))` |
   | Active connections | `sip_calculator_nodejs_active_handles_total` |
   | Heap used | `sip_calculator_nodejs_heap_size_used_bytes` |

4. You can also browse raw metrics at **http://localhost:9090** (Prometheus UI)
   and **http://localhost:9464/metrics** (live OTel Collector scrape endpoint).

---

## Verifying the Collector Pipeline

```bash
# Collector logs (should show batches being flushed to Tempo and Prometheus)
docker compose logs -f otel-collector

# Check the Prometheus scrape target is healthy
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[].health'
# Expected: "up"

# Send a test trace directly to the collector (requires grpcurl)
# grpcurl -d '{}' -plaintext localhost:4317 opentelemetry.proto.collector.trace.v1.TraceService/Export
```

---

## Stopping Everything

```bash
docker compose down
```

To also remove persisted Grafana data:

```bash
docker compose down -v
```

---

## File Layout

```
Day4/
├── backend/
│   ├── otel-setup.js              # OTel SDK init (TracerProvider + MeterProvider)
│   └── server.js                  # Express API (unchanged)
├── otel-collector-config.yaml     # Collector: OTLP in → Tempo + Prometheus out
├── tempo.yaml                     # Grafana Tempo trace storage config
├── prometheus/
│   └── prometheus.yaml            # Prometheus scrape config
├── grafana/
│   └── provisioning/
│       └── datasources/
│           └── datasources.yaml   # Auto-wires Prometheus + Tempo in Grafana
├── docker-compose.yaml            # Orchestrates the full observability stack
└── start.sh                       # One-command start script
```
