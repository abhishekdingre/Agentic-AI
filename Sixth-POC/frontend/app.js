// app.js — vanilla JS UI logic for the agentic-RAG frontend.
// The UI only ever renders the server's FinalAnswer JSON (see docs/AIDLC_PLAN.md,
// "Fixed output schema"). Nothing here invents or guesses citation metadata.

// Single place to change the API base URL. Leave empty for same-origin ('/api/query').
// Set to e.g. 'http://localhost:8000' if the frontend is served from a different
// origin/port than the backend during local dev.
const API_BASE = 'http://localhost:8000';

const DOMAIN_LABELS = {
  literature: 'Literature',
  patents: 'Patents',
  clinical_trials: 'Clinical Trials',
  internal_reports: 'Internal Reports'
};

const CONFIDENCE_BADGE_CLASS = {
  High: 'badge-high',
  Moderate: 'badge-moderate',
  Low: 'badge-low',
  Insufficient: 'badge-insufficient'
};

// Cap on how many turns stay mounted in #results-area at once. Older turns are
// evicted (removed from the DOM) once this is exceeded, so a long session doesn't
// accumulate unbounded DOM nodes/listeners/retained text. See PERF_REVIEW_FRONTEND.md P1-1.
const MAX_RENDERED_TURNS = 20;

const form = document.getElementById('query-form');
const questionInput = document.getElementById('question-input');
const resultsArea = document.getElementById('results-area');
const resultsEmpty = document.getElementById('results-empty');
const formError = document.getElementById('form-error');
const submitBtn = document.getElementById('submit-btn');

let turnCounter = 0;

// Ordered list of turn elements currently mounted in #results-area (oldest first).
// Used to evict the oldest turn once MAX_RENDERED_TURNS is exceeded (P1-1). Deliberately
// does not include #results-empty — that element is never pushed here.
const renderedTurns = [];

form.addEventListener('submit', handleSubmit);

// Single delegated listener for all citation-pill clicks, across all turns, for the
// lifetime of the page. Replaces the old per-pill `addEventListener` (P2-1): listener
// count is now O(1) instead of O(total citations rendered across the session). Also
// builds each pill's quote content lazily on first click instead of eagerly at render
// time (P2-2) — see renderCitation/handleResultsAreaClick below.
resultsArea.addEventListener('click', handleResultsAreaClick);

async function handleSubmit(event) {
  event.preventDefault();

  const question = questionInput.value.trim();
  const domains = getCheckedDomains();

  hideFormError();

  if (!question) {
    showFormError('Enter a research question before submitting.');
    questionInput.focus();
    return;
  }
  if (domains.length === 0) {
    showFormError('Select at least one domain to search.');
    return;
  }

  const turnEl = createTurn(question, domains);
  if (resultsEmpty) {
    resultsEmpty.hidden = true;
  }
  resultsArea.appendChild(turnEl);
  renderedTurns.push(turnEl);
  if (renderedTurns.length > MAX_RENDERED_TURNS) {
    const oldest = renderedTurns.shift();
    oldest.remove();
  }
  turnEl.scrollIntoView({ behavior: 'smooth', block: 'start' });

  setSubmitting(true);

  try {
    const response = await fetch(`${API_BASE}/api/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, domains })
    });

    if (!response.ok) {
      let detail = '';
      try {
        detail = await response.text();
      } catch (_readErr) {
        // ignore — body may be empty or unreadable
      }
      throw new Error(
        `Server returned ${response.status} ${response.statusText}${detail ? ` — ${detail}` : ''}`
      );
    }

    const data = await response.json();
    renderAnswer(turnEl.querySelector('.turn-body'), data);
  } catch (err) {
    const message = err instanceof TypeError
      ? `Network error — could not reach the server (${err.message}).`
      : (err && err.message) || String(err);
    renderError(turnEl.querySelector('.turn-body'), message);
  } finally {
    setSubmitting(false);
  }
}

function getCheckedDomains() {
  return Array.from(form.querySelectorAll('input[name="domain"]:checked')).map((el) => el.value);
}

function setSubmitting(isSubmitting) {
  submitBtn.disabled = isSubmitting;
  submitBtn.textContent = isSubmitting ? 'Asking…' : 'Ask';
}

function showFormError(message) {
  formError.textContent = message;
  formError.hidden = false;
}

function hideFormError() {
  formError.hidden = true;
  formError.textContent = '';
}

function createTurn(question, domains) {
  turnCounter += 1;
  const turn = document.createElement('article');
  turn.className = 'turn';
  turn.dataset.turnId = String(turnCounter);

  turn.innerHTML = `
    <div class="turn-question">
      <span class="turn-question-label">Q${turnCounter}</span>
      <div class="turn-question-content">
        <p class="turn-question-text"></p>
        <p class="turn-domains"></p>
      </div>
    </div>
    <div class="turn-body">
      <div class="loading-state">
        <span class="spinner" aria-hidden="true"></span>
        <span>Retrieving evidence…</span>
      </div>
    </div>
  `;

  turn.querySelector('.turn-question-text').textContent = question;
  turn.querySelector('.turn-domains').textContent =
    `Domains: ${domains.map(formatDomainLabel).join(', ')}`;

  return turn;
}

function renderError(bodyEl, message) {
  bodyEl.innerHTML = '';
  const banner = document.createElement('div');
  banner.className = 'banner banner-error';
  banner.setAttribute('role', 'alert');

  const strong = document.createElement('strong');
  strong.textContent = 'Request failed.';
  const span = document.createElement('span');
  span.textContent = ` ${message}`;

  banner.appendChild(strong);
  banner.appendChild(span);
  bodyEl.appendChild(banner);
}

function renderAnswer(bodyEl, answer) {
  bodyEl.innerHTML = '';

  const wrapper = document.createElement('div');
  wrapper.className = 'answer';

  // --- Overall confidence badge ---
  const confidenceRow = document.createElement('div');
  confidenceRow.className = 'confidence-row';
  confidenceRow.appendChild(makeBadge(answer.overall_confidence, 'Overall confidence'));
  wrapper.appendChild(confidenceRow);

  // --- Refusal banner ---
  if (answer.refused) {
    const banner = document.createElement('div');
    banner.className = 'banner banner-refusal';
    banner.setAttribute('role', 'alert');

    const strong = document.createElement('strong');
    strong.textContent = 'Refused:';
    const span = document.createElement('span');
    span.textContent = ` ${answer.refusal_reason || 'No reason provided.'}`;
    banner.appendChild(strong);
    banner.appendChild(span);

    if (answer.escalate_for_human_review) {
      const note = document.createElement('div');
      note.className = 'escalate-note';
      note.textContent = 'This question has been flagged for human review.';
      banner.appendChild(note);
    }
    wrapper.appendChild(banner);
  } else if (answer.escalate_for_human_review) {
    // Partial-degrade path: not a full refusal, but flagged anyway.
    const banner = document.createElement('div');
    banner.className = 'banner banner-escalate';
    banner.setAttribute('role', 'alert');
    banner.innerHTML = '<strong>Flagged for human review.</strong> <span>Part of this answer did not fully ground in the retrieved evidence — a human should verify it.</span>';
    wrapper.appendChild(banner);
  }

  // --- Claims ---
  const claims = Array.isArray(answer.claims) ? answer.claims : [];
  if (claims.length > 0) {
    const claimsList = document.createElement('div');
    claimsList.className = 'claims';
    claims.forEach((claim) => claimsList.appendChild(renderClaim(claim)));
    wrapper.appendChild(claimsList);
  } else if (!answer.refused) {
    const empty = document.createElement('p');
    empty.className = 'no-claims';
    empty.textContent = 'No claims were returned.';
    wrapper.appendChild(empty);
  }

  // --- Gaps / caveats ---
  const gaps = Array.isArray(answer.gaps_or_caveats) ? answer.gaps_or_caveats : [];
  if (gaps.length > 0) {
    const gapsSection = document.createElement('div');
    gapsSection.className = 'gaps-section';

    const heading = document.createElement('h4');
    heading.textContent = 'Gaps & caveats';
    gapsSection.appendChild(heading);

    const list = document.createElement('ul');
    list.className = 'gaps-list';
    gaps.forEach((gap) => {
      const li = document.createElement('li');
      li.textContent = gap;
      list.appendChild(li);
    });
    gapsSection.appendChild(list);
    wrapper.appendChild(gapsSection);
  }

  // --- Retrieval log (collapsible, collapsed by default) ---
  wrapper.appendChild(renderRetrievalLog(answer.retrieval_log));

  bodyEl.appendChild(wrapper);
}

function renderClaim(claim) {
  const card = document.createElement('article');
  card.className = 'claim-card';

  const header = document.createElement('div');
  header.className = 'claim-header';

  const statement = document.createElement('p');
  statement.className = 'claim-statement';
  statement.textContent = claim.statement || '';
  header.appendChild(statement);
  header.appendChild(makeBadge(claim.confidence));
  card.appendChild(header);

  const citations = Array.isArray(claim.citations) ? claim.citations : [];
  const pillsWrap = document.createElement('div');
  pillsWrap.className = 'citation-pills';
  if (citations.length > 0) {
    citations.forEach((citation) => pillsWrap.appendChild(renderCitation(citation)));
  } else {
    const none = document.createElement('span');
    none.className = 'no-citations';
    none.textContent = 'No citations.';
    pillsWrap.appendChild(none);
  }
  card.appendChild(pillsWrap);

  return card;
}

function renderCitation(citation) {
  const item = document.createElement('div');
  item.className = 'citation-item';

  const similarityPct = typeof citation.similarity === 'number'
    ? `${Math.round(citation.similarity * 100)}%`
    : '—';
  const version = citation.version != null ? citation.version : '—';

  const pill = document.createElement('button');
  pill.type = 'button';
  pill.className = 'citation-pill';
  pill.setAttribute('aria-expanded', 'false');

  const pillTitle = document.createElement('span');
  pillTitle.className = 'pill-title';
  pillTitle.textContent = citation.title || citation.source_id || 'Untitled source';

  const pillMeta = document.createElement('span');
  pillMeta.className = 'pill-meta';
  pillMeta.textContent = `${formatDomainLabel(citation.domain)} · v${version} · ${similarityPct}`;

  pill.appendChild(pillTitle);
  pill.appendChild(pillMeta);

  // Stash the quote data on the pill itself rather than building the
  // `.citation-quote`/`<blockquote>` DOM now. Most pills are never expanded, so this
  // defers that construction cost to first click (P2-2), via the delegated listener
  // (handleResultsAreaClick) rather than a per-pill listener (P2-1).
  pill.dataset.quote = citation.quote || '';
  pill.dataset.sourceId = citation.source_id || '';
  pill.dataset.chunkId = citation.chunk_id || '';

  // Placeholder sibling; populated lazily on first click, then just toggled.
  const quote = document.createElement('div');
  quote.className = 'citation-quote';
  quote.hidden = true;

  item.appendChild(pill);
  item.appendChild(quote);
  return item;
}

// Delegated click handler for every `.citation-pill`, across every turn, attached once
// on #results-area (see module init above). On a pill's first click, lazily builds its
// `.citation-quote` content (same structure as before: a `<blockquote>` of the quote
// text plus a `.quote-meta` "source_id · chunk_id" line) from the data stashed on the
// pill's dataset, caches it in the DOM, then toggles aria-expanded/hidden. Subsequent
// clicks just toggle the already-built node.
function handleResultsAreaClick(event) {
  const pill = event.target.closest('.citation-pill');
  if (!pill) {
    return;
  }

  const quote = pill.nextElementSibling;
  if (!quote || !quote.classList.contains('citation-quote')) {
    return;
  }

  if (!pill.dataset.quoteBuilt) {
    const blockquote = document.createElement('blockquote');
    blockquote.textContent = pill.dataset.quote || '';

    const quoteMeta = document.createElement('div');
    quoteMeta.className = 'quote-meta';
    quoteMeta.textContent = `${pill.dataset.sourceId || ''} · ${pill.dataset.chunkId || ''}`;

    quote.appendChild(blockquote);
    quote.appendChild(quoteMeta);
    pill.dataset.quoteBuilt = 'true';
  }

  const expanded = pill.getAttribute('aria-expanded') === 'true';
  pill.setAttribute('aria-expanded', String(!expanded));
  quote.hidden = expanded;
}

function renderRetrievalLog(log) {
  const entries = Array.isArray(log) ? log : [];

  const details = document.createElement('details');
  details.className = 'retrieval-log';

  const summary = document.createElement('summary');
  summary.textContent = `Show retrieval log ▾ (${entries.length})`;
  details.appendChild(summary);

  if (entries.length === 0) {
    const p = document.createElement('p');
    p.className = 'log-empty';
    p.textContent = 'No retrieval steps recorded.';
    details.appendChild(p);
    return details;
  }

  // Defer building the <ol> of log entries until the <details> is actually opened —
  // most turns' logs are never expanded (P2-2). `<details>`/`<summary>` fire a native
  // `toggle` event; { once: true } means this only ever runs on the first open (the
  // element starts closed, so the first toggle is always an open transition), and the
  // built <ol> is simply left in the DOM (cached) for all subsequent opens/closes.
  details.addEventListener('toggle', () => {
    if (!details.open) {
      return;
    }

    const ol = document.createElement('ol');
    entries.forEach((entry) => {
      const li = document.createElement('li');

      const iteration = document.createElement('span');
      iteration.className = 'log-iteration';
      iteration.textContent = `Iteration ${entry.iteration}`;

      const tool = document.createElement('span');
      tool.className = 'log-tool';
      tool.textContent = entry.tool || '';

      const args = document.createElement('code');
      args.className = 'log-args';
      args.textContent = stringifySafe(entry.args);

      const result = document.createElement('span');
      result.className = 'log-result';
      result.textContent = entry.result_summary || '';

      li.appendChild(iteration);
      li.appendChild(tool);
      li.appendChild(args);
      li.appendChild(result);
      ol.appendChild(li);
    });
    details.appendChild(ol);
  }, { once: true });

  return details;
}

function makeBadge(confidence, prefix) {
  const span = document.createElement('span');
  const cls = CONFIDENCE_BADGE_CLASS[confidence] || 'badge-unknown';
  span.className = `badge badge-confidence ${cls}`;
  span.textContent = prefix ? `${prefix}: ${confidence}` : (confidence || 'Unknown');
  return span;
}

function formatDomainLabel(domain) {
  return DOMAIN_LABELS[domain] || domain || 'Unknown domain';
}

function stringifySafe(value) {
  try {
    return JSON.stringify(value);
  } catch (_err) {
    return String(value);
  }
}
