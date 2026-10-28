.PHONY: setup run run-mcp test smoke sync deploy deploy-mcp status status-mcp logs logs-mcp

PROJECT_DIR := $(shell pwd)
DEPLOY_DIR  ?= /opt/agenthub
SERVICE     := agenthub
MCP_SERVICE := agenthub-mcp
CONF        ?= $(HOME)/.config/agenthub/config.yaml
UNIT_DIR    := $(HOME)/.config/systemd/user
PYTHON      ?= python3

setup:
	@$(PYTHON) -c "import fastapi, uvicorn, aiohttp, yaml, websockets, httpx, mcp" 2>/dev/null || \
		$(PYTHON) -m pip install --user -r requirements.txt
	@echo "✓ dependencies ready"

run:
	@set -a; [ -f "$$HOME/.config/agenthub/env" ] && . "$$HOME/.config/agenthub/env"; set +a; \
	AGENTHUB_CONF=$${AGENTHUB_CONF:-$(CONF)} $(PYTHON) -m app

run-mcp:
	@set -a; [ -f "$$HOME/.config/agenthub/env" ] && . "$$HOME/.config/agenthub/env"; set +a; \
	AGENTHUB_CONF=$${AGENTHUB_CONF:-$(CONF)} $(PYTHON) -m app.mcp

test:
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) -m compileall -q app adapters skillcell
	@if [ -x "$$HOME/.local/bin/node" ]; then \
		"$$HOME/.local/bin/node" --check app/static/app.js; \
		"$$HOME/.local/bin/node" --check app/static/filepane.js; \
	elif command -v node >/dev/null 2>&1; then \
		node --check app/static/app.js; \
		node --check app/static/filepane.js; \
	else \
		echo "node not found, skip js syntax check"; \
	fi

smoke:
	$(PYTHON) tests/ui_smoke.py

sync:
	@echo "=== Syncing to $(DEPLOY_DIR) ==="
	mkdir -p $(DEPLOY_DIR)
	rsync -a --delete \
		--exclude=.git --exclude=.omo --exclude=__pycache__ --exclude='*.pyc' \
		--exclude=.venv --exclude=venv --exclude=node_modules \
		--exclude='*.db' --exclude='*.db-*' --exclude='*.log' \
		--exclude=config.yaml --exclude='*.secret.yaml' \
		$(PROJECT_DIR)/ $(DEPLOY_DIR)/

deploy: sync
	install -Dm644 deploy/agenthub.service $(UNIT_DIR)/$(SERVICE).service
	systemctl --user daemon-reload
	systemctl --user enable $(SERVICE)
	-systemctl --user restart $(SERVICE)
	@echo "✓ Deploy complete"

deploy-mcp: sync
	install -Dm644 deploy/agenthub-mcp.service $(UNIT_DIR)/$(MCP_SERVICE).service
	systemctl --user daemon-reload
	systemctl --user enable $(MCP_SERVICE)
	-systemctl --user restart $(MCP_SERVICE)
	@echo "✓ MCP deploy complete"

status:
	systemctl --user status $(SERVICE) --no-pager -l

status-mcp:
	systemctl --user status $(MCP_SERVICE) --no-pager -l

logs:
	journalctl --user -u $(SERVICE) -n 80 --no-pager -l

logs-mcp:
	journalctl --user -u $(MCP_SERVICE) -n 80 --no-pager -l
