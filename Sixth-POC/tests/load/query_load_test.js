// k6 load test for POST /api/query.
//
// Each request runs the FULL agentic loop (multiple Anthropic API calls,
// embeddings, a Chroma query per search_corpus call) synchronously, so
// this is a load test of the whole RAG pipeline's latency/error-rate under
// concurrency, not just HTTP overhead. Keep VUs modest — this also spends
// real money on every iteration via the underlying Anthropic API calls.
//
// Run:
//   BASE_URL=http://localhost:8000 k6 run tests/load/query_load_test.js
//
// (Install k6: https://k6.io/docs/get-started/installation/)

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

const errorRate = new Rate('rag_error_rate');
const refusalRate = new Rate('rag_refusal_rate');
const claimCount = new Trend('rag_claim_count');

// Mixed questions/domains: a mix of richly-evidenced, contradictory, and
// deliberately-unanswerable questions, exercising all four hard commitments
// under load (not just the happy path).
const QUESTIONS = [
  {
    question: 'What is the evidence that CRB3 is a validated target for AXN-2401 in PF-7 pulmonary fibrosis?',
    domains: ['literature', 'clinical_trials', 'internal_reports'],
  },
  {
    question: 'What is the nonclinical NOAEL for AXN-2401 and which report should be cited for it?',
    domains: ['internal_reports'],
  },
  {
    question: 'Is there a cardiac safety (hERG) signal for AXN-2401, and do internal and external data agree?',
    domains: ['literature', 'internal_reports', 'clinical_trials'],
  },
  {
    question: 'What clinical efficacy data supports using AXN-2401 to treat renal fibrosis?',
    domains: ['literature', 'clinical_trials', 'internal_reports', 'patents'],
  },
  {
    question: 'Summarize the Phase 2a proof-of-concept efficacy results for AXN-2401 in PF-7.',
    domains: ['clinical_trials'],
  },
  {
    question: 'What patents cover AXN-2401 composition of matter and its method of treating PF-7?',
    domains: ['patents'],
  },
];

export const options = {
  scenarios: {
    ramping_queries: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 3 },
        { duration: '1m', target: 6 },
        { duration: '30s', target: 0 },
      ],
    },
  },
  thresholds: {
    // Agentic loop with real model calls is slow; budget generously.
    http_req_duration: ['p(95)<60000'],
    rag_error_rate: ['rate<0.05'],
    http_req_failed: ['rate<0.05'],
  },
};

export default function () {
  const pick = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];

  const res = http.post(`${BASE_URL}/api/query`, JSON.stringify(pick), {
    headers: { 'Content-Type': 'application/json' },
    timeout: '120s',
  });

  const ok = check(res, {
    'status is 200': (r) => r.status === 200,
    'has claims field': (r) => {
      try {
        return Array.isArray(r.json('claims'));
      } catch (_e) {
        return false;
      }
    },
    'has overall_confidence field': (r) => {
      try {
        const bucket = r.json('overall_confidence');
        return ['High', 'Moderate', 'Low', 'Insufficient'].includes(bucket);
      } catch (_e) {
        return false;
      }
    },
  });

  errorRate.add(!ok);

  if (res.status === 200) {
    try {
      const body = res.json();
      refusalRate.add(body.refused === true);
      claimCount.add(Array.isArray(body.claims) ? body.claims.length : 0);
    } catch (_e) {
      // malformed JSON already counted via the `ok` check above
    }
  }

  sleep(1);
}
