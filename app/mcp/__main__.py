from __future__ import annotations

import sys

import uvicorn

from .config import load_mcp_config
from .server import build_app


def main() -> None:
    try:
        config = load_mcp_config()
    except Exception as exc:
        print(f"sah-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    app, _service, _mcp = build_app(config)
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
