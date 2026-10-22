from __future__ import annotations

import asyncio
import contextlib
from html import escape
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .cache import TreeCache
from .config import HubConfig, NodeConfig, config_file_path, load_config, update_config
from .oc import OcClient
from .proxy import create_proxy_app
from .store import FavoriteStore

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def asset_version() -> str:
    files = [STATIC_DIR / "style.css", STATIC_DIR / "app.js", STATIC_DIR / "index.html"]
    return str(int(max(path.stat().st_mtime for path in files)))


class NoSignalServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def _start_proxies(app: FastAPI, config: HubConfig, oc: OcClient) -> list[tuple[str, uvicorn.Server, asyncio.Task]]:
    servers: list[tuple[str, uvicorn.Server, asyncio.Task]] = []
    if not config.proxy_enabled:
        return servers

    for node in config.nodes:
        proxy_app = create_proxy_app(node, oc.session)
        server = NoSignalServer(
            uvicorn.Config(
                proxy_app,
                host=config.host,
                port=node.proxy_port,
                log_config=None,
                log_level=None,
                access_log=False,
                lifespan="off",
            )
        )
        task = asyncio.create_task(server.serve(), name=f"agenthub-proxy-{node.name}")
        servers.append((node.name, server, task))

    await asyncio.sleep(0.2)
    for name, _server, task in servers:
        if task.done() and not task.cancelled():
            app.state.proxy_errors[name] = str(task.exception() or "proxy exited")
    return servers


async def _stop_proxies(app: FastAPI) -> None:
    servers = getattr(app.state, "proxy_servers", [])
    app.state.proxy_servers = []
    for _name, server, _task in servers:
        server.should_exit = True

    tasks = [task for _n, _s, task in servers]
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)
    except asyncio.TimeoutError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    if servers:
        await asyncio.sleep(0.2)


async def _apply_config(app: FastAPI, config: HubConfig) -> None:
    async with app.state.proxy_lock:
        await _stop_proxies(app)
        app.state.config = config
        app.state.oc.config = config
        app.state.proxy_errors = {}
        app.state.proxy_servers = await _start_proxies(app, config, app.state.oc)
        app.state.cache = TreeCache(config.refresh_interval_s, app.state.oc.tree)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    oc = OcClient(config)
    store = FavoriteStore(config.state_db)
    app.state.config = config
    app.state.oc = oc
    app.state.store = store
    app.state.proxy_errors = {}
    app.state.proxy_lock = asyncio.Lock()
    app.state.cache = TreeCache(config.refresh_interval_s, oc.tree)
    app.state.proxy_servers = await _start_proxies(app, config, oc)
    try:
        yield
    finally:
        for _name, server, _task in app.state.proxy_servers:
            server.should_exit = True
        for _name, _server, task in app.state.proxy_servers:
            task.cancel()
        await asyncio.gather(*(task for _n, _s, task in app.state.proxy_servers), return_exceptions=True)
        await oc.close()
        store.close()


app = FastAPI(title="agenthub", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.get("/")
async def index() -> HTMLResponse:
    version = asset_version()
    html = (STATIC_DIR / "index.html").read_text()
    name = escape(app.state.config.name)
    html = html.replace("<title>sah - homelab</title>", f"<title>sah - {name}</title>")
    html = html.replace(
        '<span class="user-name" id="hub-name"></span>',
        f'<span class="user-name" id="hub-name">{name}</span>',
    )
    html = html.replace("/static/style.css", f"/static/style.css?v={version}")
    html = html.replace("/static/app.js", f"/static/app.js?v={version}")
    return HTMLResponse(html)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
async def api_config() -> dict[str, Any]:
    config: HubConfig = app.state.config
    return {
        "name": config.name,
        "refresh_interval_s": config.refresh_interval_s,
        "nodes": [{"name": node.name, "proxy_port": node.proxy_port} for node in config.nodes],
    }


def _node_settings(node: NodeConfig) -> dict[str, Any]:
    return {
        "name": node.name,
        "api_url": node.api_url,
        "web_url": node.web_url,
        "username": node.username,
        "password_env": node.password_env,
        "proxy_port": node.proxy_port,
        "secret_set": bool(node.secret),
    }


def _settings_payload(config: HubConfig) -> dict[str, Any]:
    return {
        "name": config.name,
        "config_path": str(config_file_path()),
        "refresh_interval_s": config.refresh_interval_s,
        "proxy_base_port": config.proxy_base_port,
        "nodes": [_node_settings(node) for node in config.nodes],
    }


@app.get("/api/settings")
async def api_settings() -> dict[str, Any]:
    return _settings_payload(app.state.config)


@app.patch("/api/settings")
async def api_settings_update(body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name required")

    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("hub", {})["name"] = name

    try:
        config = update_config(config_file_path(), mutate)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"cannot save config: {exc}") from exc

    app.state.config = config
    app.state.oc.config = config
    return _settings_payload(config)


def _host_fields(body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("name") or "").strip()
    api_url = str(body.get("api_url") or "").strip().rstrip("/")
    web_url = str(body.get("web_url") or "").strip().rstrip("/")
    if not name or not api_url or not web_url:
        raise HTTPException(status_code=422, detail="name, api_url and web_url required")
    raw_port = body.get("proxy_port")
    try:
        port = int(raw_port) if str(raw_port or "").strip() else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="proxy_port must be an integer") from exc
    return {
        "name": name,
        "api_url": api_url,
        "web_url": web_url,
        "username": str(body.get("username") or "opencode"),
        "password_env": str(body.get("password_env") or "OPENCODE_SERVER_PASSWORD"),
        **({"proxy_port": port} if port is not None else {}),
    }


@app.post("/api/hosts")
async def api_host_add(body: dict[str, Any]) -> dict[str, Any]:
    fields = _host_fields(body)

    def mutate(data: dict[str, Any]) -> None:
        nodes = data.setdefault("nodes", [])
        if not isinstance(nodes, list):
            raise ValueError("config.nodes must be a list")
        nodes.append(fields)

    try:
        config = update_config(config_file_path(), mutate)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"cannot add host: {exc}") from exc

    await _apply_config(app, config)
    return _settings_payload(config)


@app.patch("/api/hosts/{name}")
async def api_host_update(name: str, body: dict[str, Any]) -> dict[str, Any]:
    fields = _host_fields({**body, "name": body.get("name") or name})

    def mutate(data: dict[str, Any]) -> None:
        nodes = data.get("nodes") or []
        if not isinstance(nodes, list):
            raise ValueError("config.nodes must be a list")
        for index, raw in enumerate(nodes):
            if isinstance(raw, dict) and str(raw.get("name")) == name:
                nodes[index] = fields
                return
        raise ValueError(f"unknown host: {name}")

    try:
        config = update_config(config_file_path(), mutate)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"cannot update host: {exc}") from exc

    await _apply_config(app, config)
    return _settings_payload(config)


@app.delete("/api/hosts/{name}")
async def api_host_remove(name: str) -> dict[str, Any]:
    def mutate(data: dict[str, Any]) -> None:
        nodes = data.get("nodes") or []
        if not isinstance(nodes, list):
            raise ValueError("config.nodes must be a list")
        remaining = [
            raw for raw in nodes if not (isinstance(raw, dict) and str(raw.get("name")) == name)
        ]
        if len(remaining) == len(nodes):
            raise ValueError(f"unknown host: {name}")
        if not remaining:
            raise ValueError("at least one host is required")
        data["nodes"] = remaining

    try:
        config = update_config(config_file_path(), mutate)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"cannot remove host: {exc}") from exc

    await _apply_config(app, config)
    return _settings_payload(config)


@app.get("/api/host-status")
async def api_host_status(
    node: str = Query(...),
    directory: str | None = Query(None),
) -> dict[str, Any]:
    config: HubConfig = app.state.config
    target = next((item for item in config.nodes if item.name == node), None)
    if target is None:
        raise HTTPException(status_code=404, detail="unknown node")

    try:
        return await app.state.oc.host_status(target, directory)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot read host status: {exc}") from exc


@app.get("/api/tree")
async def api_tree(refresh: bool = False) -> dict[str, Any]:
    tree = await app.state.cache.get(force=refresh)
    proxy_errors: dict[str, str] = getattr(app.state, "proxy_errors", {})
    for node in tree.get("nodes", []):
        error = proxy_errors.get(node["name"])
        if error:
            node["error"] = node.get("error") or error
    return tree


async def _resolve_project(node: str, directory: str):
    config: HubConfig = app.state.config
    tree = await app.state.cache.get()
    tree_node = next((item for item in tree.get("nodes", []) if item["name"] == node), None)
    if tree_node is None:
        raise HTTPException(status_code=404, detail="unknown node")
    project = next(
        (item for item in tree_node.get("projects", []) if item["path"] == directory),
        None,
    )
    if project is None:
        raise HTTPException(status_code=404, detail="unknown project")
    target = next((item for item in config.nodes if item.name == node), None)
    if target is None:
        raise HTTPException(status_code=404, detail="unknown node")
    return target, tree_node, project


@app.get("/api/favorites")
def api_favorites() -> dict[str, Any]:
    items = app.state.store.list()
    return {
        "items": items,
        "keys": [f"{item['node']}:{item['path']}" for item in items],
    }


@app.post("/api/favorites")
def api_favorite_add(body: dict[str, Any]) -> dict[str, bool]:
    node = str(body.get("node") or "").strip()
    path = str(body.get("path") or "").strip()
    config: HubConfig = app.state.config
    if not node or not path:
        raise HTTPException(status_code=422, detail="node and path required")
    if not any(item.name == node for item in config.nodes):
        raise HTTPException(status_code=404, detail="unknown node")
    app.state.store.add(node, path)
    return {"ok": True}


@app.delete("/api/favorites")
def api_favorite_remove(node: str = Query(...), path: str = Query(...)) -> dict[str, bool]:
    app.state.store.remove(node, path)
    return {"ok": True}


@app.get("/api/sessions")
async def api_sessions(
    node: str = Query(...),
    directory: str = Query(...),
    limit: int = Query(500, ge=1, le=500),
) -> dict[str, Any]:
    oc: OcClient = app.state.oc
    target, _tree_node, project = await _resolve_project(node, directory)

    try:
        sessions = await oc.project_sessions(target, project, limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot list sessions: {exc}") from exc

    return {"node": node, "directory": directory, "sessions": sessions}


@app.get("/api/project-status")
async def api_project_status(
    node: str = Query(...),
    directory: str = Query(...),
) -> dict[str, Any]:
    oc: OcClient = app.state.oc
    target, _tree_node, _project = await _resolve_project(node, directory)

    try:
        status = await oc.project_status(target, directory)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot read project status: {exc}") from exc

    return {"node": node, "directory": directory, **status}
