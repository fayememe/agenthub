from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse

from .config import McpConfig
from .security import AccessMiddleware
from .service import McpService

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def build_app(config: McpConfig) -> tuple[Any, McpService, FastMCP]:
    service = McpService(config)
    mcp = FastMCP(
        name="sah-mcp",
        instructions=(
            "Read-only access to agenthub's LAN opencode hosts. "
            "Use list_projects to discover project paths before querying sessions."
        ),
        host=config.host,
        port=config.port,
        streamable_http_path=config.path,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(config.allowed_hosts),
            allowed_origins=list(config.allowed_origins),
        ),
        lifespan=service.lifespan,
    )

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "sah-mcp"})

    mcp.add_tool(
        service.list_hosts,
        annotations=READ_ONLY,
        description="List configured opencode hosts with online state and version.",
    )
    mcp.add_tool(
        service.get_host_status,
        annotations=READ_ONLY,
        description=(
            "Read provider, agent, skill, MCP, LSP, formatter and session status "
            "for one host. Absolute home/state/config paths are removed."
        ),
    )
    mcp.add_tool(
        service.list_projects,
        annotations=READ_ONLY,
        description=(
            "List projects reported by opencode, including session counts. "
            "Omit node to query every exposed host."
        ),
    )
    mcp.add_tool(
        service.list_sessions,
        annotations=READ_ONLY,
        description=(
            "List session metadata for a project id/path/name, or for a project "
            "directory. Message bodies are not returned."
        ),
    )
    mcp.add_tool(
        service.get_project_status,
        annotations=READ_ONLY,
        description=(
            "Read git change counts, busy sessions and pending permission/question "
            "counts for a project directory. Set include_details=true only when "
            "permission patterns and question text are needed."
        ),
    )
    mcp.add_tool(
        service.get_session_meta,
        annotations=READ_ONLY,
        description=(
            "Read metadata for one session without message bodies or diffs."
        ),
    )

    app = mcp.streamable_http_app()
    app = AccessMiddleware(
        app,
        token=config.token if config.auth_mode == "bearer" else None,
        rate_limit_per_min=config.rate_limit_per_min,
    )
    return app, service, mcp
