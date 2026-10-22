<p align="center"><img src="assets/logo.svg" width="112" height="112" alt="softagenthub logo"></p>

# softagenthub — opencode multi-node shell

A single-user LAN shell for opencode: nodes and projects on the left, the selected project's opencode Web UI in a panel on the right, plus native Git and Sessions panels.

![softagenthub screenshot](docs/snap.png)

> The public repository is named `softagenthub`; the internal directory, systemd units, config, and database still use `agenthub` and are unaffected.

## ⚠️ Scope and disclaimer

This project targets **LAN / single-user / trusted networks** and is not a public-internet product:

- **No multi-user support, login, or RBAC.** The Settings write API has no authentication, so anyone who can reach `8670` can change host configuration.
- **Do not expose it directly to the public internet** (same for `8670–8680`). For remote access, add your own HTTPS reverse proxy, authentication, access control, and firewall; public deployments must add OAuth/authorization first.
- agenthub proxies and embeds the opencode Web UI, so the browser gains indirect access to project files, sessions, and terminal/tool execution on each node. Evaluate node permissions and network boundaries yourself.
- **Add and harden security features (authentication, authorization, TLS, audit, rate limiting, isolation) for your own environment.** See [`docs/security.md`](docs/security.md).
- The software is provided "as is" without any express or implied warranty; you are responsible for any loss caused by deployment, configuration, or use.

Use it only on a LAN with trusted users.

- Deploy directory: `/opt/agenthub` (override with `DEPLOY_DIR=...`)
- Hub: `http://<host>:8670`
- Node proxies: `8671–8679` (one port per node)
- MCP (read-only): `127.0.0.1:8680`
- CI/CD: `make deploy`

## Features

- Node → project → session tree with collapsing, filtering, and drag-to-resize (persisted width).
- Right-side tabs: `opencode` (iframe), `Host` (node opencode status), `Project` (project status), `Git` (changed files), `Sessions` (session list).
- `Host`: version, models, providers, MCP connections, skills, agents/workmode, LSP, formatter, paths, busy/permission/question state.
- `Project`: path, sessions, busy state, permissions/questions, working-tree details.
- Settings: change the display name and add/edit/delete hosts; writes update `config.yaml` live (with an automatic `config.yaml.bak` backup) and hot-restart the proxies.
- Project lists come from each node's opencode `/project`; the hub does not scan the filesystem. If a project directory was renamed, the latest directory registered in `/project/{id}/directories` is used for display (opencode's `worktree` may still be stale); the proxy rewrites `/project` responses and the bootstrap project list so the opencode Web UI also shows the new path.
- Node online/offline/version/error state; project git change stats; session busy state.
- Session index from opencode web `/api/session`: sessions are grouped by `projectID`, `global` sessions go into an `Other` project per node, and up to 500 sessions are shown per project. The sidebar shows per-project and total session counts.
- Status bar: git changes, busy sessions, pending permissions/questions, refresh time.
- Favorites: persisted in SQLite across browsers and reloads.
- Deep links: `/#/<node>/<base64url(project path)>[/<sessionID>]`.
- Shortcuts: `/` search, `r` refresh, `o` / `h` / `p` / `g` / `s` switch panels, `Esc` clear search.

### Project click behavior

- Clicking a project opens its **last opened session** (recorded by agenthub), otherwise the **latest session**.
- If the project has no sessions, open a **new session draft** scoped to that project directory; the previously recorded draftId is reused so every click does not create another New session tab.
- "New session" at the top always creates a draft; bootstrap cleans up extra draft tabs and keeps at most one New session.
- When the session changes inside the iframe, the injected bridge syncs the route back to agenthub, so the next project click or reload returns to the same session.
- opencode has no explicit "create project" API; projects come from directories/sessions. The `/:dir` route is a "new session draft" rather than a project page, so agenthub deep-links to the last session instead.

## Architecture

```text
Browser :8670  sidebar + panel(tabs)
   │
FastAPI hub :8670
   ├─ /api/tree            aggregate /project, /api/session index, health
   ├─ /api/sessions        project sessions (projectID/directory) + /session/status
   ├─ /api/project-status  /vcs/status + /permission + /question + /session/status
   ├─ /api/host-status     /config + /provider + /agent + /skill + /mcp + /lsp …
   ├─ /api/settings        hub.name / config path
   ├─ /api/hosts           add / edit / remove (writes config.yaml)
   ├─ /api/favorites       SQLite
   └─ :8671..              per-node proxy → node:4097
                           injects Basic Auth, passes through SSE/WebSocket
```

- **No opencode modifications**: uses its HTTP API and Web UI.
- **Proxy** handles Basic Auth/CORS so the iframe does not show a password prompt.
- **opencode web compatibility layer**: the current opencode 1.18.x Web UI calls `/api/project`, `/api/project/current`, and `/api/worktree`, but the server only exposes v1 routes such as `/project`; the proxy maps v2 requests to v1 and adds the `{data: ...}` wrapper.
- **Project bootstrap**: the proxy injects a bootstrap script that writes the node's `/project` list into the opencode Web `opencode.global.dat:server` (`projects.local`) and sets `lastProject` for the iframe directory. This makes Projects, the current project, and the file manager show the correct directory.
- **Draft cleanup**: every time the opencode Web UI opens `/<b64>` it creates a `New session` draft tab; bootstrap removes duplicate draft tabs down to one and reuses the `draftId`.
- **Bridge**: injects a small script that syncs the iframe's internal route back to the shell.
- One proxy port per node avoids rewriting absolute paths in the SPA.
- Only one opencode iframe is kept at a time to avoid multiple SSE/WS connections.

## MCP (read-only)

`sah-mcp` is a separate read-only MCP server and does not reuse the hub's write APIs. It listens on `127.0.0.1:8680` by default; the only business endpoint is `/mcp`, plus `/healthz`.

```bash
# Put the token in ~/.config/agenthub/env
echo 'AGENTHUB_MCP_TOKEN=<long-random-token>' >> ~/.config/agenthub/env
chmod 600 ~/.config/agenthub/env

make run-mcp          # run in the foreground
make deploy-mcp       # install/enable agenthub-mcp.service
make logs-mcp
```

Add to `config.yaml`:

```yaml
mcp:
  host: 127.0.0.1
  port: 8680
  path: /mcp
  public_url: https://sah-mcp.example.com   # set when using a tunnel
  auth:
    mode: bearer
    token_env: AGENTHUB_MCP_TOKEN
  allowed_hosts: []          # empty includes localhost and public_url
  allowed_origins: []
  expose_nodes: []           # empty = all; can restrict nodes
  expose_projects: []        # empty = all; can restrict path prefixes
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
- `get_project_status` (omits permission/question details when `include_details=false`)
- `get_session_meta`

Security boundaries:

- GET-only; tool arguments cannot contain arbitrary URLs or file paths; project directories must match an exposed project.
- Removes absolute `home/state/config` paths, skill locations, LSP roots, and share URLs before returning; never returns message bodies or diffs.
- The bearer token travels in the `Authorization` header; tokens and payloads are not logged.
- **Do not expose 8670 directly to the public internet.** ChatGPT connectors require HTTPS + OAuth 2.1; the current version is a bearer endpoint and needs OAuth/authorization pages plus a reverse tunnel before public ChatGPT use.
- LAN or tunnel use only; see the security discussion in `docs/pr.md`.

## Configuration

Copy `config.example.yaml` to a private location (do not commit it):

```bash
mkdir -p ~/.config/agenthub
cp config.example.yaml ~/.config/agenthub/config.yaml
$EDITOR ~/.config/agenthub/config.yaml
```

Passwords are referenced through environment variables in `~/.config/agenthub/env` (`chmod 600`):

```bash
OPENCODE_SERVER_PASSWORD=...
```

Node fields:

```yaml
nodes:
  - name: node1
    api_url: http://127.0.0.1:4096
    web_url: http://127.0.0.1:4097
    username: opencode
    password_env: OPENCODE_SERVER_PASSWORD
    # proxy_port: 8680   # optional; defaults to proxy_base_port + index
```

Optional settings:

```yaml
hub:
  name: my homelab   # shown in the browser title: sah - my homelab
state_db: ~/.local/share/agenthub/agenthub.db
refresh_interval_s: 20
request_timeout_s: 6
```

Environment overrides: `AGENTHUB_CONF`, `AGENTHUB_STATE_DB`, `AGENTHUB_PROXY=0` (for tests).

## Running

```bash
make setup
AGENTHUB_CONF=~/.config/agenthub/config.yaml make run
```

Or deploy as a systemd user service:

```bash
make deploy
make status
make logs
```

`make deploy` syncs the source, installs `deploy/agenthub.service` to `~/.config/systemd/user/`, enables it, and restarts `agenthub`.

## Tests

```bash
make test    # unit + API + proxy/SSE/WS tests; JS syntax check
make smoke   # requires a running hub + Chrome: real UI smoke test
```

`make test` covers config parsing, Base64 deep links, project filtering, project status aggregation, the Favorites API, HTTP/SSE/WebSocket proxying, and HTML bridge injection. `make smoke` opens headless Chrome and checks the sidebar, drag-resize, Git/Sessions panels, iframe, last-session resolution, new sessions, and bridge route synchronization.

## Known limitations

- The proxy carries node credentials for the browser, so access to the hub is equivalent to access to each node's opencode.
- Saving Settings rewrites `config.yaml` (comments are lost) and keeps `config.yaml.bak`; host changes hot-restart all node proxies.
- `8671+` must be reachable from the browser machine; if you add an HTTPS reverse proxy, provide HTTPS for the node ports as well.
- The opencode Web UI is a fast-moving internal UI; re-verify deep links, embedding, the `/api/project` mapping, and bootstrap after upgrades.
- iframe navigation is synced back to the parent address bar/state through the injected bridge, which depends on the opencode Web DOM/routing; re-verify after upgrades.
- Other agents (Claude Code, Hermes, etc.) are not integrated yet; terminal or adapter panels can be added later.

## License

[MIT](LICENSE) © 2026 The softagenthub authors
