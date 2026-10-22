# agenthub — requirements and work plan

## Goal

A LAN single-user opencode aggregation web shell: a node/project/session tree on the left, and the selected project's opencode Web UI embedded on the right.

## Key decisions

- Projects are reported by each node's opencode `/project` endpoint; the hub does not scan directories.
- v1 uses an iframe and does not rewrite the opencode frontend.
- One reverse-proxy port per node (8671–8679) with injected Basic Auth and SSE/WS passthrough.
- Vanilla HTML/CSS/JS frontend served by FastAPI; no build chain.
- Separation from task orchestrators: agenthub only handles interactive sessions, not task scheduling.

## Management and deployment conventions

- Working tree: repository checkout
- Deploy directory: `/opt/agenthub`
- Hub: `8670`; node proxies: `8671–8679`
- Private config: `~/.config/agenthub/config.yaml`
- Private environment: `~/.config/agenthub/env`
- CI/CD: `make deploy`

## TODOs

- [x] Confirm requirements (`docs/pr.md`)
- [x] Architecture and directory layout
- [x] Ports, services, and unit design
- [x] FastAPI hub + OC project/session aggregation
- [x] Node reverse proxy (Basic Auth/SSE/WS)
- [x] Resizable sidebar + iframe panel
- [x] Local verification of tree/sessions/proxy/iframe
- [x] Deploy to `/opt/agenthub` and enable systemd
- [x] Multi-node credential consistency
- [x] SQLite-persisted favorites
- [x] Native Git / Sessions panels
- [x] Project status bar (git/busy/permission/question)
- [x] Unit + API + proxy/SSE/WS + UI smoke tests
- [x] Restore last/latest session when a project is clicked
- [x] iframe route bridge synchronization
- [x] opencode Web v2 project API compatibility shim
- [x] Bootstrap Web UI project list/current project (fixes file manager directory)
- [x] Reuse and clean up New session draft tabs
- [x] Session index from web `/api/session`, including missing global/subdirectory sessions (merged into an Other project)
- [x] Read-only MCP server (Streamable HTTP/bearer, GET-only, redaction, rate limiting)
- [ ] MCP public access: OAuth 2.1 + tunnel + ChatGPT connector
- [ ] Later: panel type abstraction, other agents, permission/question replies
