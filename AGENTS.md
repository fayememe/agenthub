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
