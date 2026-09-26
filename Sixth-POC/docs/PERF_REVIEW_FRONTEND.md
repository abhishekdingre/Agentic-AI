# Frontend Performance Review — `frontend/index.html`, `frontend/styles.css`, `frontend/app.js`

Scope: efficiency/performance only, ahead of a refactor. Does **not** re-litigate schema-rendering
fidelity or client-side citation-metadata construction (that's `p3-triage-agent`'s job). Reviewed
against the six checklist areas in this agent's role definition, with the "Frontend contract" section
of `docs/AIDLC_PLAN.md` and `docs/PROBLEM_STATEMENT.md` as context for expected usage (a chat-style
list of turns, each with a source-trail panel: confidence badge, citation pills, gaps warnings, and a
collapsible retrieval log — used over a real demo/session, not a single request).

No code was changed. All line numbers refer to the current state of the three files
(`app.js`: 386 lines, `index.html`: 68 lines, `styles.css`: 591 lines).

---

## P1 — would meaningfully hurt responsiveness or memory over a real demo/usage session

### P1-1. Chat history / DOM is unbounded — no cap, virtualization, or clear-history affordance

**What:** Every submitted question permanently appends a new `.turn` element to `#results-area`, and
nothing ever removes, collapses-by-default, or virtualizes old turns. There is no "clear history"
control anywhere in `index.html`.

**How found:** `app.js:57` — `resultsArea.appendChild(turnEl);` is the only place `#results-area`
is mutated at the list level. A repo-wide grep for `removeChild|\.remove(|splice(` across
`frontend/*.js` returns zero matches — nothing is ever detached from the DOM once inserted. Each
turn also carries its full rendered payload permanently: every claim card, every citation pill's
full quote `<blockquote>` (built in `renderCitation`, `app.js:294-306`, and left in the DOM as
`hidden`, not deferred), and the full retrieval log `<ol>` (`renderRetrievalLog`, `app.js:337-363`,
built eagerly regardless of whether the `<details>` is ever opened). `index.html` has no button or
mechanism to clear/reset `#results-area`.

**Impact:** For a single query this is invisible. Over a longer demo/session — the scenario this
review is explicitly scoped to — DOM node count, retained citation-quote text (chunks up to
`CHUNK_MAX_CHARS=700` each, per `AIDLC_PLAN.md`), and retrieval-log entries (up to `MAX_ITERATIONS=6`
per turn) all grow strictly linearly with the number of questions asked, with no ceiling. Tens of
queries in one sitting (a realistic demo/stress-click scenario) accumulate thousands of DOM nodes and
non-trivial retained text/JS memory that is never reclaimed while the tab stays open, degrading
scroll/paint performance and steadily increasing the page's memory footprint. This compounds with
P2-1 and P2-2 below.

**Suggested direction:** Cap rendered turns (e.g., keep the last N fully rendered, older ones
collapsed to a one-line summary or removed from the DOM entirely / virtualized), and/or add an
explicit "clear history" action. Independently of capping the turn count, avoid eagerly building
detail content that may never be viewed (see P2-2) — that alone removes a large fraction of the
per-turn memory cost for citation-heavy or refusal-free answers.

---

## P2 — worth fixing, moderate impact

### P2-1. Citation-pill click handlers are per-element closures, not delegated

**What:** Every citation pill gets its own `click` listener via a fresh closure, rather than a single
delegated listener on a stable ancestor (`#results-area` or a turn's `.turn-body`).

**How found:** `app.js:308-312`, inside `renderCitation` (called once per citation, itself called
once per claim's citation list — `app.js:257`): `pill.addEventListener('click', () => { ... })`. A
repo-wide grep for `addEventListener` in `frontend/*.js` returns exactly two hits total: the module-level
form submit listener (`app.js:33`, attached once, fine) and this per-pill listener. Each pill is
created exactly once and never re-rendered in place, so this is *not* the classic "leaks because
handlers are re-attached on every re-render" bug — there's no double-binding. But because pills are
also never removed (P1-1), the listener count grows 1:1 with the total number of citations rendered
across the whole session, and none of it is ever released.

**Impact:** Moderate on its own (listener objects are cheap relative to the DOM/text they're attached
to), but it's a design choice that doesn't scale with session length, and it's exactly the pattern the
review was asked to check for ("attached once on a stable container" vs. per-element). Combined with
P1-1's unbounded node growth, total retained listener count is unbounded too.

**Suggested direction:** Delegate: one `click` listener on `#results-area` (or per `.turn-body`) that
does `event.target.closest('.citation-pill')`, then toggles that pill's adjacent `.citation-quote`
`hidden`/`aria-expanded` state. Listener count becomes O(1) (or O(turns)) instead of O(total
citations), independent of how many citations end up in the DOM.

### P2-2. Retrieval log and citation quotes are built eagerly, not on first expand

**What:** The full retrieval-log `<ol>` (all iterations) and every citation's full quote `<blockquote>`
are constructed and inserted into the DOM unconditionally as part of rendering each answer — even
though both are collapsed/hidden by default and the user may never open them.

**How found:** `renderRetrievalLog` (`app.js:319-366`) is called unconditionally from `renderAnswer`
(`app.js:234`) and builds the entire `<ol>` of log entries up front, before the `<details>` is ever
opened. Likewise `renderCitation` (`app.js:269-317`) always builds the `.citation-quote` block
(`app.js:294-306`, containing the full `blockquote` text) at render time; it's merely `hidden = true`
(`app.js:296`), not deferred.

**Impact:** At current expected scale (a handful of claims/citations, ≤6 log entries per turn from
`MAX_ITERATIONS`) this is cheap per turn. But it means every turn unconditionally pays the full
DOM-construction and text-retention cost for content most users won't expand for most turns,
directly feeding the unbounded-growth problem in P1-1 — the "collapsed by default" UI pattern isn't
actually saving any construction/memory cost today, only visual/layout cost.

**Suggested direction:** Build the log `<ol>` / quote `<blockquote>` lazily on first `toggle`/click of
the `<details>`/pill (cache the built nodes after first build so re-collapsing/re-expanding is free).
This defers cost to only the turns a user actually inspects.

---

## P3 — minor / micro-optimization, low impact

### P3-1. Double-submit protection has a single point of enforcement

**What:** Concurrent/duplicate submissions are prevented only by `submitBtn.disabled = true`
(`setSubmitting`, `app.js:97-100`), set synchronously before the `await fetch` in `handleSubmit`
(`app.js:60-63`). This does work correctly today — verified there is no separate `keydown`/Enter
handler on the `<textarea>` (`index.html:47-52`) that could bypass the disabled button and re-trigger
`handleSubmit` — but it's a single guard with no independent in-flight flag or `AbortController`
backing it up.

**How found:** Grep for `fetch(` in `frontend/*.js` returns exactly one call site (`app.js:63`); no
duplicate/retry-loop calls per user action were found, so the "redundant network calls" check
otherwise comes back clean. The only latent risk is fragility if the disable logic is ever refactored
independently of the submit path.

**Impact:** None currently; noted only as a low-risk fragility point, not an active issue.

**Suggested direction:** If the submit path is refactored, consider an explicit `let requestInFlight`
boolean (or `AbortController` to cancel a stale in-flight request) as a second, code-level guard
rather than relying solely on the DOM `disabled` attribute.

### P3-2. Retrieval-log args are re-stringified from scratch on every render

**What:** `stringifySafe(entry.args)` (`app.js:349-351`, used at `app.js:380-386`) runs a fresh
`JSON.stringify` per log entry every time `renderAnswer` runs.

**How found:** `app.js:351` inside the `entries.forEach` loop in `renderRetrievalLog`.

**Impact:** Negligible at current scale (`MAX_ITERATIONS = 6`, small arg objects); purely cosmetic.

**Suggested direction:** No action needed unless log entries grow much larger; not worth restructuring
on its own.

### P3-3. `createTurn` builds via `innerHTML` template then re-queries for two text nodes

**What:** `createTurn` (`app.js:112-139`) sets a template via `innerHTML` (`app.js:118-132`), then
immediately does two `querySelector` calls back into that same freshly-built subtree to set
`.textContent` on two of its nodes (`app.js:134-136`).

**How found:** `app.js:112-139`.

**Impact:** Negligible — this is a small, fixed-size, detached-element template (not related to
history length; it doesn't touch `#results-area`'s existing content), so it does not exhibit the
"full list rebuild" pattern this review was checking for. Listed only as a minor style
simplification, not a performance problem.

**Suggested direction:** Could build the two text nodes directly instead of the
template-then-requery round trip, but this is optional polish, not a fix.

---

## Checked, no issue found

For completeness, since the role's checklist explicitly calls these out:

- **Full-list rebuild (checklist #1):** No occurrence of `#results-area`/`resultsArea` being
  rebuilt via `innerHTML =`. Every new turn is an incremental `appendChild` (`app.js:57`); the two
  `bodyEl.innerHTML = ''` resets (`app.js:142`, `app.js:158`) are scoped to a single turn's own body
  (bounded, one-time cost per turn), not the whole history. There is therefore no history-length-
  dependent rebuild cost at all — by design, not by luck.
- **Layout thrashing (checklist #4):** Repo-wide grep for
  `offsetHeight|offsetWidth|clientHeight|clientWidth|scrollHeight|scrollTop|getBoundingClientRect|getComputedStyle`
  across `frontend/*` returns zero matches. No interleaved layout read/write loops exist. (The one
  `turnEl.scrollIntoView(...)` call at `app.js:58` is a single call, not part of a loop.)
- **Redundant network calls (checklist #5):** Single `fetch` call site (`app.js:63`); double-submit
  is blocked by synchronously disabling `#submit-btn` before the `await` (see P3-1 for the one caveat).
- **Inline style churn (checklist #6):** Zero `.style.xxx = ` assignments anywhere in `frontend/*.js`.
  Confidence badges and status states are styled entirely through class-name assignment
  (`makeBadge`, `app.js:368-374`) and CSS attribute selectors (`.citation-pill[aria-expanded="true"]`
  in `styles.css:440-443`) plus the native `hidden` boolean attribute — exactly the class-toggle
  pattern the checklist asks for, not per-property inline mutation.
