# Graph Report - Day4  (2026-09-19)

## Corpus Check
- Corpus is ~1,980 words - fits in a single context window. You may not need a graph.

## Summary
- 54 nodes · 56 edges · 7 communities (6 shown, 1 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 2 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- SIP API Contract & Frontend
- MCP Server Core
- Backend Package Config
- SIP Calculation Engine
- Backend Architecture Concepts
- MCP Configuration
- MCP Dependencies

## God Nodes (most connected - your core abstractions)
1. `fetch /api/sip/calculate (POST)` - 6 edges
2. `POST /api/sip/calculate Endpoint` - 5 edges
3. `renderChart(yearlyBreakdown)` - 5 edges
4. `Express API (server.js)` - 3 edges
5. `renderTable(yearlyBreakdown)` - 3 edges
6. `yearlyBreakdown Response Data` - 3 edges
7. `sip-mutual-fund-data` - 2 edges
8. `scripts` - 2 edges
9. `express` - 2 edges
10. `round2()` - 2 edges

## Surprising Connections (you probably didn't know these)
- `POST /api/sip/calculate Endpoint` --semantically_similar_to--> `SIP Input Form`  [INFERRED] [semantically similar]
  .claude/agents/backend.md → frontend/index.html
- `POST /api/sip/calculate Endpoint` --references--> `fetch /api/sip/calculate (POST)`  [EXTRACTED]
  .claude/agents/backend.md → frontend/index.html

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **SIP Calculation Request/Response Data Flow** — frontend_index_sip_form, frontend_index_fetch_api_sip_calculate, _claude_agents_backend_api_sip_calculate, frontend_index_yearly_breakdown, frontend_index_renderchart, frontend_index_rendertable [EXTRACTED 1.00]
- **Backend Computation Contract: Formula + Validation + Endpoint** — _claude_agents_backend_sip_fv_formula, _claude_agents_backend_request_validation, _claude_agents_backend_api_sip_calculate [EXTRACTED 1.00]

## Communities (7 total, 1 thin omitted)

### Community 0 - "SIP API Contract & Frontend"
Cohesion: 0.21
Nodes (13): POST /api/sip/calculate Endpoint, Request Body Validation, SIP Future Value Formula, Growth Chart Card, Chart.js CDN Dependency, currency() — INR Intl.NumberFormat helper, fetch /api/sip/calculate (POST), renderChart(yearlyBreakdown) (+5 more)

### Community 1 - "MCP Server Core"
Cohesion: 0.17
Nodes (10): server, transport, description, main, name, private, type, version (+2 more)

### Community 2 - "Backend Package Config"
Cohesion: 0.18
Nodes (10): dependencies, express, description, main, name, private, scripts, start (+2 more)

### Community 3 - "SIP Calculation Engine"
Cohesion: 0.29
Nodes (6): app, calculateSip(), express, path, round2(), ref_path

### Community 4 - "Backend Architecture Concepts"
Cohesion: 0.67
Nodes (3): Express API (server.js), MCP Server / Backend Separation Principle, SIP Calculator Backend Agent

### Community 6 - "MCP Dependencies"
Cohesion: 0.67
Nodes (3): dependencies, @modelcontextprotocol/sdk, zod

## Knowledge Gaps
- **25 isolated node(s):** `node`, `name`, `version`, `private`, `description` (+20 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 32 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `express` connect `Backend Package Config` to `SIP Calculation Engine`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Why does `POST /api/sip/calculate Endpoint` connect `SIP API Contract & Frontend` to `Backend Architecture Concepts`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._
- **What connects `node`, `name`, `version` to the rest of the system?**
  _25 weakly-connected nodes found - possible documentation gaps or missing edges._