# AGENTS.md

This repository contains the source for **agenthub** (an opencode hub). Requirements and plans:

- `docs/pr.md` (user requirements)
- `.omo/plans/agenthub-requirements.md` (requirements and work plan)

## Conventions

- Working tree: your repository checkout
- Deploy directory: `/opt/agenthub` (runtime, no `.git`; override with `DEPLOY_DIR=...`)
- CI/CD: `make deploy`
- Ports: `8670–8679` reserved
- Never commit secrets or credentials (`.gitignore`)
- Confirm before destructive operations

## Development

- `make test`: unit + API + proxy/SSE/WS + JS syntax checks
- `make smoke`: headless Chrome UI smoke test (requires a running hub)
- `make deploy`: sync to the deploy directory, install units, restart `agenthub`
- Private config: `~/.config/agenthub/config.yaml`; passwords: `~/.config/agenthub/env`

## Session notes (`mymd/`)

`mymd/` contains working notes that keep the project resumable if a session
disappears at any time. It is gitignored and must never be committed.

- Write notes incrementally while chatting, not as a log and not only at the end.
- Organize by chat phase and outcome; when a decision changes, record the final
  result instead of replaying the back-and-forth.
- Each note should let a fresh session answer: what is the goal, what is done,
  what is the current state, what decisions are final, and what comes next.
- Keep one note per workstream (for example `mymd/agenthub.md`); update or
  replace stale content instead of appending history.
- Never put secrets, tokens, or private credentials in `mymd/`.
