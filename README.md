<p align="center"><img src="assets/logo.svg" width="112" height="112" alt="softagenthub logo"></p>

<h1 align="center">softagenthub</h1>

<p align="center"><strong>One command deck for every opencode you run.</strong><br>
A single-operator web shell that turns a fleet of opencode nodes into one calm, legible workspace.</p>

<span class="center align-center">[中文](README.zh-CN.md) · [Design notes](docs/pr.md) · [Security](docs/security.md)</span>

![softagenthub demo fleet](docs/snap-overview.png)

<p align="center"><sub>Demo fleet with sample data. The embedded pane illustrates the opencode session view.</sub></p>

> Public name: **softagenthub**. Internal service names, config paths, and the SQLite database keep the shorter `agenthub` identifier.

## The short version

opencode is already a strong local operator. What it lacks is the view from above: many machines, many projects, many sessions, and no single place to see what is running, what is waiting, and what needs a decision.

softagenthub is that place.

It does not replace opencode, wrap it in a proprietary workflow, or pretend the network is safe. It aggregates nodes, embeds the real opencode Web UI, adds a native inspection layer, and exposes a deliberately narrow read-only MCP surface for other agents.

The thesis is simple: **a good cockpit should make the machinery more legible, not hide it.**

## Why it feels different

- **One tree, every machine.** Node → project → session, with favorites, filtering, live counts, and attention badges.
- **The real opencode UI, embedded.** No reimplementation, no feature lag inside the editor. The proxy injects credentials and preserves SSE and WebSocket traffic, so the iframe behaves like a local opencode Web UI.
- **An inbox for things that need a human.** Permissions, questions, finished runs, and failures across the whole fleet, with inline replies and follow-up prompts.
- **Native inspection when you need it.** Host status, project status, Git changes, session lists, and a file pane with preview and diff.
- **A skill-mining lab, if you want it.** Skillcell reads real sessions, extracts evidence, and proposes reusable skills, rules, and workflows for review. It does not silently edit your skill library.
- **A read-only MCP bridge.** Let other agents inspect the fleet without exposing message bodies, diffs, credentials, or arbitrary paths.
- **No build chain.** FastAPI and vanilla JavaScript. Read it, fork it, change it.

## Scenarios

### 1. Multi-project, multi-platform remote operations

A Linux workstation, a Mac mini, a GPU box, and a VPS each run opencode. Instead of opening four browser tabs and remembering four passwords, you open one shell: every project is in the left tree, every session is one click away, and every pending permission is in the same inbox.

This is the core use case. The hub is the operator's console, not a scheduler.

### 2. Let another agent drive, with a short leash

A voice assistant such as ChatGPT can be the front door: ask what is running, which project is blocked, or which sessions finished overnight. Through the read-only MCP server it can query hosts, projects, sessions, Git stats, and pending work. You still make the decisions in softagenthub.

The write path is intentionally absent. A remote agent can observe; it cannot approve permissions, edit files, or start commands through MCP. That boundary is the feature.

### 3. Triage for long-running work

Multiple agents run for hours. softagenthub keeps the fleet visible: busy sessions, completed runs, failures, pending questions, and Git drift. The inbox turns "what needs me now?" into a short list instead of a tab hunt.

## Reality check: this is a tinkerer's tool

softagenthub is a well-machined tool, not a managed service. It assumes you can read a config file, understand a reverse proxy, and take responsibility for your own network.

- **LAN / single operator / trusted users.** There is no login layer, no RBAC, and no tenant isolation. The Settings write API is unauthenticated. Anyone who can reach the hub can change host configuration.
- **Do not port-forward it to the internet.** `8670–8680` are not internet-facing services. Add HTTPS, authentication, access control, and a firewall yourself.
- **opencode moves fast.** Deep links, iframe behavior, and the Web v2 compatibility shim are verified against recent versions, but upgrades can break assumptions. Re-test after upgrading.
- **The shell is opinionated.** It is built around opencode sessions and a single-user workflow. Other agents are integrated through adapters or MCP, not through a plugin marketplace.
- **You own the blast radius.** The hub proxies credentials and embeds a tool-capable UI. Access to the hub is effectively access to every node's projects, files, and command execution.

If you want a polished SaaS with accounts, quotas, and a support contract, this is not it. If you want a cockpit that fits your fleet, keep reading.

## MCP: power, with the safety catch left to you

The bundled `sah-mcp` server is intentionally conservative:

- It is **read-only** and issues GET requests only.
- Tool arguments cannot contain arbitrary URLs or file paths; project directories must match an exposed project.
- It removes absolute home/state/config paths, skill locations, LSP roots, and share URLs before returning data.
- It never returns message bodies, diffs, tokens, or credentials.
- It supports bearer authentication, Host/Origin checks, rate limiting, and request audit.

That is a starting line, not a finish line.

**MCP security is your problem to solve for your environment.** If you want ChatGPT or another public client to reach it, you must add the missing internet-grade layers yourself: OAuth 2.1 and authorization pages, a TLS tunnel, token rotation, strict Host/Origin policy, audit retention, and network isolation. The current bearer-only endpoint is suitable for a LAN or a private tunnel with additional controls, not for direct public exposure.

Read [`docs/security.md`](docs/security.md) before exposing anything.

## Feature map

| Area | What you get |
| --- | --- |
| Fleet tree | Nodes, projects, sessions, favorites, filtering, drag-to-resize sidebar, persisted layout |
| Embedded opencode | Real Web UI in an iframe, injected Basic Auth, SSE/WebSocket passthrough, deep links |
| Compatibility | Web v2 `/api/project` mapping, project bootstrap, last-session restore, draft-tab cleanup |
| Attention inbox | Permissions, questions, completed runs, failures; inline replies and follow-up prompts |
| Host panel | Version, models, providers, MCP connections, skills, agents, LSP, formatter, paths |
| Project panel | Worktree, sessions, busy state, permissions, questions, Git summary |
| Git panel | Changed files, additions/deletions, per-file diff |
| Sessions panel | Session index by project, status, tokens, cost, last activity |
| File pane | Read-only tree, text/image preview, Markdown rendering, diff view, file search |
| Skillcell | Optional session-to-skill audit pipeline with evidence, proposals, review, and chat |
| MCP | Read-only tools for hosts, projects, sessions, status, and Git/permission/question counts |
| Operations | One-command systemd deployment, hot config reload, SQLite favorites, test suite |

### Attention inbox

The inbox is the operational heart of the shell. It collects:

- permission requests that need an allow/always/reject decision,
- questions that need an answer,
- sessions that finished successfully,
- sessions that failed.

You can reply without leaving the hub, or send a follow-up prompt to the same session. This is the part that makes many concurrent agents manageable.

### Project click behavior

- Clicking a project opens its **last opened session**, otherwise the **latest session**.
- If the project has no sessions, it opens a **new session draft** scoped to that directory.
- Draft tabs are reused and cleaned up so every click does not create another "New session" tab.
- Navigation inside the iframe is synced back to the shell, so reloads and deep links return to the same session.
- opencode has no explicit "create project" API; projects come from directories and sessions. The `/:dir` route is a new-session draft, not a project page.

### Skillcell (optional)

Skillcell is a separate audit pipeline, disabled by default. It scans recent opencode sessions from selected nodes, extracts evidence (corrections, successful patterns, repeated steps, tool errors, skill gaps), and asks an opencode model to propose reusable artifacts: skills, rules, workflows, MCP tools, or references.

Proposals live in their own SQLite database and keep their evidence. You can promote, defer, reject, or reopen them, and chat with the audit model about a proposal. The reasoner and chat sessions run with editing tools disabled. Skillcell does not modify your skill library automatically.

Evidence is redacted before storage and prompting, but it still leaves the source node for the executor model. Treat the executor node and model provider as part of your trust boundary.

Start with `auto_scan: false`, a short lookback window, and a low `max_sessions` value. Raise them once you trust the output.

## A closer look

The shell is built to be scanned: dense enough for a real fleet, quiet enough to spot the one thing that needs you.

**Attention inbox.** Permissions, questions, failures, and finished runs share one queue. Answer without opening another tab.

![Attention inbox with permission, question, error, and done items](docs/snap-inbox.png)

**Host status.** Version, models, providers, MCP connections, skills, agents, LSP, formatters, and paths for the selected node.

![Host status panel](docs/snap-host.png)

**Sessions.** A compact session index with status, model, agent, diff counts, tokens, cost, and last activity.

![Sessions panel](docs/snap-sessions.png)

**Skillcell.** Evidence-backed skill proposals with review decisions and an audit chat, all in the same shell.

![Skillcell proposal review](docs/snap-skillcell.png)

All screenshots use a demo fleet with sample data.

## Quick start

### Prerequisites

- Python 3.10+.
- One or more opencode nodes with both the API server and Web UI reachable over the network.
- A browser that can reach the hub and the node proxy ports.
- No Node.js build step for the hub itself. Node is only needed for the optional JavaScript syntax check.

### Install

```bash
git clone <your-repository-url> softagenthub
cd softagenthub
make setup
```

### Configure

```bash
mkdir -p ~/.config/agenthub
cp config.example.yaml ~/.config/agenthub/config.yaml
chmod 600 ~/.config/agenthub/config.yaml
$EDITOR ~/.config/agenthub/config.yaml
```

Passwords are referenced through environment variables, never stored in the config:

```bash
cat >> ~/.config/agenthub/env <<'EOF'
OPENCODE_SERVER_PASSWORD=change-me
EOF
chmod 600 ~/.config/agenthub/env
```

Minimal node configuration:

```yaml
nodes:
  - name: studio
    api_url: http://192.0.2.10:4096
    web_url: http://192.0.2.10:4097
    username: opencode
    password_env: OPENCODE_SERVER_PASSWORD

  - name: edge
    api_url: http://192.0.2.11:4096
    web_url: http://192.0.2.11:4097
    username: opencode
    password_env: OPENCODE_SERVER_PASSWORD
```

The hub binds to `8670`; each node gets a proxy port starting at `8671`. Override the base port with `hub.proxy_base_port` and the per-node port with `nodes[].proxy_port`.

### Run

```bash
AGENTHUB_CONF=~/.config/agenthub/config.yaml make run
```

Open `http://<hub-host>:8670`.

### Deploy as a user service

```bash
make deploy       # hub
make deploy-mcp   # optional read-only MCP server
make status
make logs
```

The deploy target syncs to `/opt/agenthub` by default, installs systemd user units, enables them, and restarts the service. Use `DEPLOY_DIR=...` to change the destination.

## Configuration

```yaml
hub:
  name: studio            # browser title: sah - studio
  host: 0.0.0.0
  port: 8670
  proxy_base_port: 8671

refresh_interval_s: 20
request_timeout_s: 6
state_db: ~/.local/share/agenthub/agenthub.db
```

Environment overrides:

- `AGENTHUB_CONF` — config file path.
- `AGENTHUB_STATE_DB` — SQLite state database.
- `AGENTHUB_PROXY=0` — disable per-node proxies (tests).

The Settings UI writes `config.yaml` live, keeps a `config.yaml.bak` backup, and hot-restarts the proxies. Comments in the file are not preserved by this write path.

## Architecture

```text
Browser :8670
  sidebar + native panels + embedded opencode iframe
        |
        v
FastAPI hub :8670
  ├─ /api/tree             aggregate /project, /api/session, health
  ├─ /api/sessions         project sessions + /session/status
  ├─ /api/project-status   /vcs/status + /permission + /question + status
  ├─ /api/host-status      /config + /provider + /agent + /skill + /mcp + /lsp
  ├─ /api/inbox            attention queue + permission/question replies
  ├─ /api/files/*          read-only list, preview, diff, search
  ├─ /api/skillcell/*      optional audit pipeline
  ├─ /api/favorites        SQLite
  ├─ /api/settings         hub name and host configuration
  └─ :8671..:8679          per-node proxy -> node:4097
                           Basic Auth injection, SSE/WebSocket passthrough

sah-mcp :8680
  read-only Streamable HTTP MCP, bearer auth, redaction, rate limit
```

Design notes:

- **No opencode modifications.** The hub uses its HTTP API and embeds its Web UI.
- **One proxy port per node.** This avoids rewriting absolute paths inside the SPA.
- **Web v2 compatibility layer.** The current opencode Web UI calls v2 project routes; the proxy maps them to v1 and wraps responses as `{data: ...}`.
- **Project bootstrap.** The proxy seeds the opencode Web project list and current project, fixing the Projects view and file-manager directory.
- **Iframe bridge.** A small injected script syncs iframe navigation back to the shell.
- **One iframe at a time.** This avoids duplicate SSE/WebSocket connections from many hidden frames.

## MCP setup

```bash
echo 'AGENTHUB_MCP_TOKEN=<long-random-token>' >> ~/.config/agenthub/env
chmod 600 ~/.config/agenthub/env
make run-mcp
```

Add to `config.yaml`:

```yaml
mcp:
  host: 127.0.0.1
  port: 8680
  path: /mcp
  # public_url: https://mcp.example.com
  auth:
    mode: bearer
    token_env: AGENTHUB_MCP_TOKEN
  allowed_hosts: []          # empty includes localhost and public_url
  allowed_origins: []
  expose_nodes: []           # empty = all
  expose_projects: []        # empty = all; restrict by path prefix
  max_sessions: 100
  max_projects: 200
  max_status_items: 100
  rate_limit_per_min: 60
```

Tools:

- `list_hosts`
- `get_host_status`
- `list_projects`
- `list_sessions`
- `get_project_status`
- `get_session_meta`

The server exposes `/mcp` and `/healthz` only. Tokens and payloads are not logged.

## Tests

```bash
make test    # unit + API + proxy/SSE/WS + skillcell + JS syntax
make smoke   # headless Chrome UI smoke test (requires a running hub)
```

The suite covers config parsing, Base64 deep links, project filtering, project and host status, favorites, HTTP/SSE/WebSocket proxying, the v2 compatibility shim, HTML bridge injection, file-path safety, inbox logic, and the skillcell pipeline. `make smoke` exercises the real UI: sidebar, resizing, panels, iframe, last-session resolution, new sessions, and bridge synchronization.

## Maturity and roadmap

**Done and in daily use:**

- multi-node aggregation, embedded opencode, deep links, and the Web v2 shim,
- project/session restore and draft-tab cleanup,
- favorites, host/project/Git/session panels,
- attention inbox with permission and question replies,
- read-only file pane with preview and diff,
- optional skillcell audit pipeline,
- read-only MCP with redaction and rate limiting,
- unit, API, proxy, and UI smoke tests.

**Still for tinkerers:**

- OAuth 2.1 and a public HTTPS tunnel for ChatGPT connectors,
- multi-user accounts and per-host authorization,
- a plugin/panel abstraction for agents other than opencode,
- sandboxing or approval policies beyond what opencode already provides,
- verification against every future opencode release.

This is a serious single-operator tool, not a finished multi-tenant platform. Treat upgrades and public exposure as engineering work.

## Project layout

```text
app/        FastAPI hub, opencode client, proxy, inbox, files
app/mcp/    read-only MCP server
app/static/ vanilla frontend
skillcell/  audit engine, evidence store, proposal pipeline
adapters/   integration layer for the hub
deploy/     systemd user units
tests/      unit, API, proxy, and UI tests
docs/       requirements, design notes, security
```

## Security

Read [`docs/security.md`](docs/security.md). The short version:

- Keep the hub and node ports on a trusted LAN or behind a VPN.
- Put HTTPS and authentication in front of `8670` and every proxy port.
- Run opencode nodes as low-privilege users with limited filesystem access.
- Protect `~/.config/agenthub/env` and `config.yaml`.
- Keep MCP on `127.0.0.1` until you have built the OAuth/tunnel layer.
- Re-test after every opencode upgrade.

## License

[MIT](LICENSE) © 2026 The softagenthub authors
