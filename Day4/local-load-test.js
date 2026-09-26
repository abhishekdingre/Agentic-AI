/**
 * SIP Calculator — Production-ready k6 load test
 *
 * Stages:
 *   0:00 – 0:15  Smoke ramp: 0 → 2 VUs  (baseline sanity)
 *   0:15 – 0:30  Ramp-up:    2 → 10 VUs
 *   0:30 – 0:50  Steady:        10 VUs  (core load window)
 *   0:50 – 1:00  Ramp-down:  10 → 0 VUs
 *
 * Thresholds (test fails if breached):
 *   http_req_failed   rate < 1 %
 *   http_req_duration p(95) < 500 ms, p(99) < 1000 ms
 *   sip_success_rate  rate > 99 %  (business-logic validity)
 *
 * Usage:
 *   k6 run local-load-test.js
 *   BASE_URL=http://localhost:4000 k6 run local-load-test.js
 *   k6 run --out influxdb=http://localhost:8086/k6 local-load-test.js
 *   k6 run --out json=k6-results.json local-load-test.js
 */

import http, { setResponseCallback } from 'k6/http';
import { check, sleep, group } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

// ─── Custom metrics ───────────────────────────────────────────────────────────
const sipCalcDuration  = new Trend('sip_calculation_duration_ms', true);
const sipSuccessRate   = new Rate('sip_success_rate');
const sipErrors        = new Counter('sip_errors_total');
const sipBadRequest    = new Counter('sip_bad_request_total'); // expected 400s

// ─── Config ───────────────────────────────────────────────────────────────────
const BASE_URL = __ENV.BASE_URL || 'http://localhost:4000';
const CALC_URL = `${BASE_URL}/api/sip/calculate`;

// ─── Options ──────────────────────────────────────────────────────────────────
export const options = {
  scenarios: {
    sip_load: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '15s', target: 2  }, // smoke: catch obvious breakage
        { duration: '15s', target: 10 }, // ramp up to target load
        { duration: '20s', target: 10 }, // hold: collect steady-state metrics
        { duration: '10s', target: 0  }, // ramp down gracefully
      ],
      gracefulRampDown: '10s',
    },
  },

  thresholds: {
    // ── HTTP transport ──────────────────────────────────────────────
    http_req_failed:   ['rate<0.01'],         // < 1 % transport errors
    http_req_duration: [
      'p(95)<500',                            // 95th percentile under 500 ms
      'p(99)<1000',                           // 99th percentile under 1 s
    ],
    // ── Business logic ──────────────────────────────────────────────
    sip_success_rate:          ['rate>0.99'],  // > 99 % valid calculations
    sip_calculation_duration_ms: ['p(95)<400'], // our own timing (excludes think time)
    // ── Named-endpoint granularity ──────────────────────────────────
    'http_req_duration{endpoint:calculate}': ['p(95)<500'],
  },
};

// Mark 400 responses as "expected" so intentional bad-payload tests don't
// inflate http_req_failed (which tracks transport/server errors, not 4xx intent).
setResponseCallback(http.expectedStatuses(200, 400));

// ─── Setup: verify server is reachable before wasting VUs ────────────────────
export function setup() {
  const probe = http.post(
    CALC_URL,
    JSON.stringify({ monthlyInvestment: 5000, annualReturnRate: 12, years: 10 }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  if (probe.status !== 200) {
    throw new Error(`Setup probe failed: ${probe.status} — is the backend running on ${BASE_URL}?`);
  }
  const body = probe.json();
  console.log(
    `✓ Backend reachable. Sample: invested=₹${body.investedAmount}  ` +
    `totalValue=₹${body.totalValue}  returns=₹${body.estimatedReturns}`
  );
  return { baseUrl: BASE_URL };
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function randomValidPayload() {
  return {
    monthlyInvestment: Math.round((1000 + Math.random() * 49000) / 500) * 500,
    annualReturnRate:  parseFloat((6 + Math.random() * 14).toFixed(1)),
    years:             Math.floor(5 + Math.random() * 25),
  };
}

// 5 % of calls send an intentionally bad payload to verify 400 handling
function maybeInvalidPayload() {
  if (Math.random() < 0.05) {
    const bad = [
      { monthlyInvestment: -1000, annualReturnRate: 12, years: 10 },
      { monthlyInvestment: 5000,  annualReturnRate: 12, years: 99 },
      {},
    ];
    return { payload: bad[Math.floor(Math.random() * bad.length)], invalid: true };
  }
  return { payload: randomValidPayload(), invalid: false };
}

// ─── Default (VU) function ────────────────────────────────────────────────────
export default function (_data) {
  const { payload, invalid } = maybeInvalidPayload();

  group('POST /api/sip/calculate', () => {
    const t0 = Date.now();
    const res = http.post(
      CALC_URL,
      JSON.stringify(payload),
      {
        headers: { 'Content-Type': 'application/json' },
        tags:    { endpoint: 'calculate' },
      }
    );
    const elapsed = Date.now() - t0;

    // ── Checks ──────────────────────────────────────────────────────
    const ok = check(res, {
      'status 200 or 400':        (r) => r.status === 200 || r.status === 400,
      'response is JSON':         (r) => {
        try { JSON.parse(r.body); return true; } catch { return false; }
      },
      'valid payload → 200':      (r) => invalid || r.status === 200,
      'invalid payload → 400':    (r) => !invalid || r.status === 400,
      '200 has totalValue':       (r) => {
        if (r.status !== 200) return true;
        const b = r.json();
        return typeof b.totalValue === 'number' && b.totalValue > 0;
      },
      '200 has yearlyBreakdown':  (r) => {
        if (r.status !== 200) return true;
        return Array.isArray(r.json().yearlyBreakdown);
      },
    });

    // ── Custom metric recording ──────────────────────────────────────
    if (!invalid) {
      sipCalcDuration.add(elapsed);
      sipSuccessRate.add(res.status === 200);
      if (res.status !== 200) sipErrors.add(1);
    } else {
      sipBadRequest.add(1);
    }
  });

  // Think time: 0.5 – 1 s (realistic user pacing)
  sleep(0.5 + Math.random() * 0.5);
}

// ─── Summary: write JSON results for the HTML viewer ─────────────────────────
export function handleSummary(data) {
  // Always write machine-readable results for the HTML results viewer
  return {
    'k6-results.json': JSON.stringify(data, null, 2),
  };
}
