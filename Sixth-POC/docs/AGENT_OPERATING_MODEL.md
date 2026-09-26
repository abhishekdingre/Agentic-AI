# Agent Operating Model — isolation, delegation, context trimming, plugins

Generic, reusable rules for running a build with Claude Code subagents. Written by **role** (builder agent, reviewer agent, orchestrator), not by this project's specific agent names, so this file can be copied wholesale into other multi-agent builds. Companion to [[AIDLC_PLAN]].

## Isolation
Every `Agent` tool invocation starts with zero shared history — a subagent never sees the orchestrator's transcript or a sibling agent's reasoning, only what its own prompt contains. Enforce this by construction:
- Each invocation's prompt **restates the relevant contract inline** (the schema/API contract, the exact scope) rather than "go read the design doc" — this makes every call reproducible and auditable independent of any other session's state.
- Prefer stateless, one-shot `Agent` calls by default. Only use `SendMessage`-based continuation for a short, bounded back-and-forth within one fix cycle.
- Filesystem-level isolation (a git worktree per agent) is the natural extension for larger builds, but requires the project to be a git repository. Call this out as a known limitation when it isn't available, rather than silently skipping it.

## Delegation
An explicit ownership table so responsibilities never overlap:
- **Orchestrator** (main session): problem statement, design doc, scaffold/skill/hooks, prompt-engineering iteration on the core logic once builder agents hand off, the live demo. Never writes builder-agent implementation code directly.
- **Builder agents**: each owns exactly one directory tree, stated as a "Scope:" list in its agent-definition body — including what it must **not** touch (a sibling builder's directory).
- **Reviewer agent**: read-only across all builder output; its only write is its own report; it never patches code itself.

## Context trimming
For any subagent re-invoked across multiple rounds (e.g. a builder agent fixing issues from a review report), bound its context growth instead of replaying full history:
- Carry a running summary of everything older than the trim window, capped at roughly **12-15% of the context window**.
- On top of that summary, keep only the **latest 8-10 prompt/response turns** verbatim; older turns fold into the summary.
- In practice: when handing review findings back to a builder agent, send a fresh, tightly-scoped prompt (the specific findings plus the exact file paths involved) rather than replaying the whole original build conversation.

## Plugin / tool access
Extends each agent's `tools:` frontmatter beyond the built-in set.
- **Internal — wrap the domain engine as an MCP server.** Instead of a one-off debug script, expose the system's core engine (here: the RAG retrieval engine) as a small MCP server using the same implementation the live app calls — one implementation, two callers (the app itself, and any agent that wants to sanity-check it independently). Register it project-locally (`.mcp.json`) and grant it to the agents that need it by adding its tool names to their `tools:` frontmatter. This is the reusable piece of this pattern: any project with a non-trivial core engine benefits from exposing it this way for agent-driven testing/review.
- **External — install from the official Claude plugin marketplace (`claude.com/plugins`)** where it saves reinventing something well-covered: a type-checking/LSP plugin for builder agents writing typed code, a live-docs-lookup plugin (e.g. Context7) to cut down on hallucinated library APIs, and code-review plugins for the reviewer agent instead of writing review heuristics from scratch. Record exact install commands here once run, so the setup is reproducible:
  - `Pyright LSP` — backend agent, type-checking while writing schemas/tools.
  - `Context7` — both builder agents, live FastAPI/Pydantic/chromadb/sentence-transformers docs.
  - `Code Review`, `PR Review Toolkit` — reviewer agent, static review pass.

## This project's concrete mapping
See [[AIDLC_PLAN]] for the architecture and `.claude/agents/backend.md` / `frontend.md` / `p3-triage-agent.md` for the three agents this maps to.
