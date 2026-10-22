20261021

# agenthub requirements

## Goal

Build a single-user LAN web shell that shows opencode projects from all nodes on one page and opens each project's opencode Web UI directly. Later extensions include favorites, project management, git status, and opencode status.

## Core features

- Node registry: API/Web addresses and credentials for each node.
- Project aggregation: project lists come from each node's opencode `/project` endpoint; agenthub does not scan the filesystem.
- Resizable left sidebar: node → project → session; supports collapsing, filtering, and favorites.
- Right panel tabs: opencode (iframe), Git (changed files), Sessions (session list).
- Clicking a project opens its last opened session, or the latest session; if there is none, open a new session draft scoped to that directory.
- Session changes inside the iframe are synchronized back to the shell through a bridge.
- Compatibility with the opencode Web v2 project API: map `/api/project`, `/api/project/current`, and `/api/worktree` to v1, and bootstrap the Web UI project list/current project to fix the empty Projects view and wrong file-manager directory.
- Reuse and clean up `New session` draft tabs so empty tabs do not accumulate.
- Deep links: project or specific session.
- Node status: online/offline, version, errors; session status (busy/idle).
- Session index from web `/api/session`, grouped by projectID; global sessions go to an Other project per node; up to 500 sessions per project.
- Project status: git file count/added/deleted lines, pending permission/question, busy sessions.
- Favorites: SQLite persistence.

## Relationship to existing components

- opencode serve (4096): project/session/event API.
- opencode web (4097): the embedded full Web UI.
- External task orchestrators: agenthub only handles interactive sessions and does no task scheduling.
- Zed: ACP client, out of scope.

## Shape

- Single-machine deployment (the hub runs on one machine) with a web UI reaching all LAN nodes.
- Python FastAPI + vanilla frontend, no build step.
- Tests: unittest + API/proxy/SSE/WS + headless Chrome UI smoke.
- One reverse-proxy port per node (8671–8679), injecting Basic Auth and passing through SSE/WebSocket.

## MCP (read-only)

- Separate `sah-mcp` process using Streamable HTTP, default `127.0.0.1:8680/mcp`.
- Bearer token (`AGENTHUB_MCP_TOKEN`), rate limiting, Host/Origin checks, request audit.
- Tools: `list_hosts`, `get_host_status`, `list_projects`, `list_sessions`, `get_project_status`, `get_session_meta`.
- The implementation only issues GET requests; directories must match exposed projects; message bodies, diffs, and credentials are never returned; home/state/config paths, skill locations, LSP roots, and share URLs are removed.
- Before exposing to ChatGPT over the public internet, add OAuth 2.1/authorization pages + a TLS tunnel; do not expose the current bearer-only version publicly.

## Deployment

- Source: repository checkout
- Deploy: `/opt/agenthub`
- systemd user units: `agenthub.service`, `agenthub-mcp.service`
- Ports: hub `8670`, node proxies `8671–8679`, MCP `8680`
- `make deploy` / `make deploy-mcp`

## Boundaries

- No multi-user or public-internet exposure.
- Do not rewrite the opencode frontend; v1 uses an iframe.
- No cross-node session migration.
- Other agents are optional; later add panels via terminal/adapter types.

## Release

- LAN single-user only; authentication, TLS, isolation, and other security controls must be added by the deployer. See [security.md](security.md).
- License: MIT, Copyright (c) 2026 The softagenthub authors.
