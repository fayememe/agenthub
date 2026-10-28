from __future__ import annotations

import asyncio
import contextlib
import json
import time
from html import escape
from pathlib import Path
from typing import Any

import aiohttp
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from adapters.skillcell_ah import SkillcellRuntime, load_skillcell_settings
from skillcell.models import ProposalStatus

from .activity import ActivityStore
from .cache import TreeCache
from .config import HubConfig, NodeConfig, config_file_path, load_config, update_config
from .files import FilePathError, FileService, FileTooLarge, relative_path
from .oc import OcClient
from .proxy import create_proxy_app
from .store import FavoriteStore

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def asset_version() -> str:
    files = [
        STATIC_DIR / "style.css",
        STATIC_DIR / "filepane.css",
        STATIC_DIR / "app.js",
        STATIC_DIR / "filepane.js",
        STATIC_DIR / "index.html",
    ]
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


async def _stop_skillcell(app: FastAPI) -> None:
    runtime = getattr(app.state, "skillcell", None)
    app.state.skillcell = None
    if runtime is not None:
        await runtime.stop()


async def _start_skillcell(
    app: FastAPI,
    config: HubConfig,
    session: aiohttp.ClientSession,
) -> None:
    app.state.skillcell = None
    app.state.skillcell_error = None
    settings = load_skillcell_settings(config_file_path())
    if not settings.enabled:
        return
    runtime: SkillcellRuntime | None = None
    try:
        runtime = SkillcellRuntime(config, settings, session)
        await runtime.start()
        app.state.skillcell = runtime
    except Exception as exc:
        if runtime is not None:
            await runtime.stop()
        app.state.skillcell_error = str(exc)[:500] or exc.__class__.__name__


async def _apply_config(app: FastAPI, config: HubConfig) -> None:
    async with app.state.proxy_lock:
        await _stop_proxies(app)
        app.state.config = config
        app.state.oc.config = config
        app.state.proxy_errors = {}
        app.state.proxy_servers = await _start_proxies(app, config, app.state.oc)
        app.state.cache = TreeCache(config.refresh_interval_s, app.state.oc.tree)
        activity = getattr(app.state, "activity", None)
        if activity is not None:
            await activity.reload(config)
        await _stop_skillcell(app)
        await _start_skillcell(app, config, app.state.oc.session)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    oc = OcClient(config)
    activity = ActivityStore(config, oc)
    oc.activity = activity
    store = FavoriteStore(config.state_db)
    app.state.config = config
    app.state.oc = oc
    app.state.activity = activity
    app.state.store = store
    app.state.files = FileService()
    app.state.proxy_errors = {}
    app.state.proxy_lock = asyncio.Lock()
    app.state.cache = TreeCache(config.refresh_interval_s, oc.tree)
    app.state.proxy_servers = await _start_proxies(app, config, oc)
    await activity.start()
    await _start_skillcell(app, config, oc.session)
    try:
        yield
    finally:
        await _stop_skillcell(app)
        await activity.stop()
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
    if (
        request.url.path == "/"
        or request.url.path == "/skillcell"
        or request.url.path.startswith("/static/")
    ):
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
    html = html.replace("/static/filepane.css", f"/static/filepane.css?v={version}")
    html = html.replace("/static/app.js", f"/static/app.js?v={version}")
    html = html.replace("/static/filepane.js", f"/static/filepane.js?v={version}")
    return HTMLResponse(html)


@app.get("/skillcell")
async def skillcell_page() -> HTMLResponse:
    return HTMLResponse(
        (STATIC_DIR / "skillcell.html").read_text(),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


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


def _node_config(node_name: str) -> NodeConfig | None:
    return next((node for node in app.state.config.nodes if node.name == node_name), None)


def _project_for_session(tree_node: dict[str, Any], session: dict[str, Any]) -> dict[str, Any] | None:
    projects = tree_node.get("projects") or []
    project_id = session.get("projectID")
    directory = str(session.get("directory") or "")
    project = next((item for item in projects if item.get("id") == project_id), None)
    if project:
        return project
    if directory:
        containing = [
            item
            for item in projects
            if item.get("kind") != "other"
            and (
                directory == item.get("path")
                or directory.startswith(str(item.get("path", "")).rstrip("/") + "/")
            )
        ]
        if containing:
            return max(containing, key=lambda item: len(str(item.get("path") or "")))
    return next((item for item in projects if item.get("kind") == "other"), None)


@app.get("/api/inbox")
async def api_inbox() -> dict[str, Any]:
    activity: ActivityStore = app.state.activity
    oc: OcClient = app.state.oc
    tree = await app.state.cache.get()
    tree_nodes = {node["name"]: node for node in tree.get("nodes", [])}

    by_node: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for (node_name, session_id), state in list(activity.sessions.items()):
        if state.get("permissions") or state.get("questions") or state.get("done") or state.get("error"):
            by_node.setdefault(node_name, []).append((session_id, state))

    items: list[dict[str, Any]] = []
    counts = {"permission": 0, "question": 0, "error": 0, "done": 0, "total": 0}

    for node_name, entries in by_node.items():
        tree_node = tree_nodes.get(node_name)
        node_cfg = _node_config(node_name)
        if not tree_node or not node_cfg:
            continue
        try:
            index = await oc.session_index(node_cfg)
        except Exception:
            index = []
        session_map = {str(session.get("id")): session for session in index}

        for session_id, state in entries:
            info = session_map.get(session_id) or {
                "id": session_id,
                "title": session_id,
                "directory": None,
                "projectID": None,
            }
            project = _project_for_session(tree_node, info)
            base = {
                "node": node_name,
                "sessionID": session_id,
                "sessionTitle": info.get("title") or session_id,
                "projectPath": project.get("path") if project else None,
                "projectName": project.get("name") if project else None,
                "projectB64": project.get("b64") if project else None,
                "updated": state.get("updated") or info.get("updated") or 0,
            }

            for request_id in list(state.get("permissions") or []):
                detail = activity.permission_items.get((node_name, request_id)) or {}
                items.append(
                    {
                        **base,
                        "id": f"permission:{node_name}:{request_id}",
                        "kind": "permission",
                        "requestID": request_id,
                        "permission": detail.get("permission"),
                        "patterns": detail.get("patterns") or [],
                        "always": detail.get("always") or [],
                        "metadata": detail.get("metadata") or {},
                    }
                )
                counts["permission"] += 1
                counts["total"] += 1

            for request_id in list(state.get("questions") or []):
                detail = activity.question_items.get((node_name, request_id)) or {}
                items.append(
                    {
                        **base,
                        "id": f"question:{node_name}:{request_id}",
                        "kind": "question",
                        "requestID": request_id,
                        "questions": detail.get("questions") or [],
                    }
                )
                counts["question"] += 1
                counts["total"] += 1

            if state.get("done"):
                items.append({**base, "id": f"done:{node_name}:{session_id}", "kind": "done"})
                counts["done"] += 1
                counts["total"] += 1

            if state.get("error"):
                items.append(
                    {
                        **base,
                        "id": f"error:{node_name}:{session_id}",
                        "kind": "error",
                        "error": state.get("error_message"),
                    }
                )
                counts["error"] += 1
                counts["total"] += 1

    order = {"permission": 0, "question": 1, "error": 2, "done": 3}
    items.sort(key=lambda item: (order.get(item["kind"], 9), -(item.get("updated") or 0)))
    return {"items": items, "counts": counts, "generated_at": int(time.time() * 1000)}


async def _post_first(node: NodeConfig, bases: list[str], paths: list[str], body: Any) -> tuple[bool, str | None]:
    oc: OcClient = app.state.oc
    seen: set[str] = set()
    for base in bases:
        if not base or base in seen:
            continue
        seen.add(base)
        for path in paths:
            try:
                await oc._post_url(node, base, path, body)
                return True, None
            except Exception as exc:
                last_error = str(exc)
    return False, last_error if "last_error" in locals() else "no endpoint"


@app.post("/api/inbox/permission")
async def api_inbox_permission(body: dict[str, Any]) -> dict[str, Any]:
    node_name = str(body.get("node") or "")
    request_id = str(body.get("requestID") or "")
    reply = str(body.get("reply") or "")
    node = _node_config(node_name)
    activity: ActivityStore = app.state.activity
    item = activity.permission_items.get((node_name, request_id))
    if not node or not item:
        raise HTTPException(status_code=404, detail="permission request not found")
    if reply not in {"once", "always", "reject"}:
        raise HTTPException(status_code=422, detail="invalid reply")

    paths = [
        f"/api/session/{item['sessionID']}/permission/{request_id}/reply",
        f"/permission/{request_id}/reply",
    ]
    ok, error = await _post_first(node, [item.get("source"), node.web_url, node.api_url], paths, {"reply": reply})
    if not ok:
        raise HTTPException(status_code=502, detail=f"permission reply failed: {error}")
    activity.resolve_permission(node_name, request_id)
    return {"ok": True}


@app.post("/api/inbox/question")
async def api_inbox_question(body: dict[str, Any]) -> dict[str, Any]:
    node_name = str(body.get("node") or "")
    request_id = str(body.get("requestID") or "")
    reject = bool(body.get("reject"))
    answers = body.get("answers")
    node = _node_config(node_name)
    activity: ActivityStore = app.state.activity
    item = activity.question_items.get((node_name, request_id))
    if not node or not item:
        raise HTTPException(status_code=404, detail="question request not found")

    if reject:
        paths = [
            f"/api/session/{item['sessionID']}/question/{request_id}/reject",
            f"/question/{request_id}/reject",
        ]
        ok, error = await _post_first(node, [item.get("source"), node.web_url, node.api_url], paths, None)
    else:
        if not isinstance(answers, list):
            raise HTTPException(status_code=422, detail="answers must be a list")
        paths = [
            f"/api/session/{item['sessionID']}/question/{request_id}/reply",
            f"/question/{request_id}/reply",
        ]
        ok, error = await _post_first(node, [item.get("source"), node.web_url, node.api_url], paths, {"answers": answers})
    if not ok:
        raise HTTPException(status_code=502, detail=f"question reply failed: {error}")
    activity.resolve_question(node_name, request_id)
    return {"ok": True}


@app.post("/api/inbox/prompt")
async def api_inbox_prompt(body: dict[str, Any]) -> dict[str, Any]:
    node_name = str(body.get("node") or "")
    session_id = str(body.get("sessionID") or "")
    text = str(body.get("text") or "").strip()
    node = _node_config(node_name)
    if not node or not session_id or not text:
        raise HTTPException(status_code=422, detail="node, sessionID and text required")

    activity: ActivityStore = app.state.activity
    activity.mark_read(node_name, {session_id})

    info: dict[str, Any] = {}
    try:
        sessions = await app.state.oc.session_index(node)
        info = next((item for item in sessions if item.get("id") == session_id), {})
    except Exception:
        info = {}

    payload: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
    model = info.get("model") or {}
    if model.get("providerID") and model.get("id"):
        payload["model"] = {"providerID": model["providerID"], "modelID": model["id"]}
    if info.get("agent"):
        payload["agent"] = info["agent"]

    async def send() -> None:
        oc: OcClient = app.state.oc
        timeout = aiohttp.ClientTimeout(total=1800)
        for base in [node.web_url, node.api_url]:
            try:
                await oc._post_url(node, base, f"/session/{session_id}/message", payload, timeout=timeout)
                return
            except Exception:
                continue

    asyncio.create_task(send())
    return {"ok": True, "accepted": True}


@app.post("/api/inbox/read")
async def api_inbox_read(body: dict[str, Any]) -> dict[str, Any]:
    node_name = str(body.get("node") or "")
    session_id = str(body.get("sessionID") or "")
    if not node_name or not session_id:
        raise HTTPException(status_code=422, detail="node and sessionID required")
    app.state.activity.mark_read(node_name, {session_id})
    return {"ok": True}


@app.get("/api/inbox/content")
async def api_inbox_content(
    node: str = Query(...),
    sessionID: str = Query(...),
) -> dict[str, Any]:
    node_cfg = _node_config(node)
    if not node_cfg:
        raise HTTPException(status_code=404, detail="unknown node")
    oc: OcClient = app.state.oc
    data: Any = None
    bases: list[str] = []
    for base in (node_cfg.web_url, node_cfg.api_url):
        if base and base not in bases:
            bases.append(base)
    for base in bases:
        try:
            data = await oc._get_url(node_cfg, base, f"/session/{sessionID}/message", {"limit": 20})
            break
        except Exception:
            continue

    messages = data.get("data") if isinstance(data, dict) else data
    content = ""
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            info = message.get("info") if isinstance(message.get("info"), dict) else {}
            if info.get("role") != "assistant":
                continue
            parts = message.get("parts") if isinstance(message.get("parts"), list) else []
            texts = [
                str(part.get("text"))
                for part in parts
                if isinstance(part, dict)
                and part.get("type") == "text"
                and not part.get("ignored")
                and part.get("text")
            ]
            if texts:
                content = "\n\n".join(texts)
                break

    return {"content": content[:20000]}


@app.get("/api/events")
async def api_events() -> StreamingResponse:
    queue = app.state.activity.subscribe()

    async def generate():
        try:
            yield 'data: {"type":"connected"}\n\n'
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            app.state.activity.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/activity/read")
async def api_activity_read(body: dict[str, Any]) -> dict[str, Any]:
    node = str(body.get("node") or "")
    directory = str(body.get("directory") or "")
    session_id = str(body.get("sessionID") or "")
    activity: ActivityStore = app.state.activity
    ids: set[str] = set()

    if session_id:
        ids.add(session_id)
    elif directory:
        try:
            target, _tree_node, project = await _resolve_project(node, directory)
            sessions = await app.state.oc.project_sessions(target, project, 5000)
            ids.update(str(session["id"]) for session in sessions if session.get("id"))
        except HTTPException:
            pass

    if ids:
        activity.mark_read(node, ids)
    return {"ok": True, "marked": len(ids)}


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

    app.state.activity.decorate(node, sessions)
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


def _skillcell_runtime() -> SkillcellRuntime:
    runtime = getattr(app.state, "skillcell", None)
    if runtime is None:
        detail = getattr(app.state, "skillcell_error", None) or "skillcell disabled"
        raise HTTPException(status_code=404, detail=detail)
    return runtime


@app.get("/api/skillcell/status")
async def api_skillcell_status() -> dict[str, Any]:
    runtime = getattr(app.state, "skillcell", None)
    if runtime is None:
        return {
            "enabled": False,
            "error": getattr(app.state, "skillcell_error", None),
        }
    try:
        return await runtime.status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot read skillcell status: {exc}") from exc


@app.get("/api/skillcell/proposals")
async def api_skillcell_proposals(
    status: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    runtime = _skillcell_runtime()
    proposal_status = None
    if status:
        try:
            proposal_status = ProposalStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"unknown proposal status: {status}") from exc
    items = runtime.list_proposals(status=proposal_status, limit=limit)
    return {"items": items, "total": len(items)}


@app.get("/api/skillcell/proposals/{proposal_id}")
async def api_skillcell_proposal(proposal_id: str) -> dict[str, Any]:
    runtime = _skillcell_runtime()
    detail = runtime.proposal_detail(proposal_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="unknown proposal")
    return detail


@app.post("/api/skillcell/proposals/{proposal_id}/review")
async def api_skillcell_review(
    proposal_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    runtime = _skillcell_runtime()
    decision = str(body.get("decision") or "").strip()
    rationale = str(body.get("rationale") or "").strip()
    try:
        return runtime.review(proposal_id, decision, rationale=rationale)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown proposal") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/skillcell/jobs")
async def api_skillcell_jobs(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    runtime = _skillcell_runtime()
    items = runtime.list_jobs(limit=limit)
    return {"items": items, "total": len(items)}


@app.get("/api/skillcell/evidence/{evidence_id}")
async def api_skillcell_evidence(evidence_id: str) -> dict[str, Any]:
    runtime = _skillcell_runtime()
    item = runtime.get_evidence(evidence_id)
    if item is None:
        raise HTTPException(status_code=404, detail="unknown evidence")
    return item


@app.post("/api/skillcell/scan")
async def api_skillcell_scan(body: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime = _skillcell_runtime()
    source_id = str((body or {}).get("sourceID") or "").strip() or None
    focus = str((body or {}).get("focus") or "").strip() or None
    try:
        jobs = await runtime.request_scan(source_id, focus=focus)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown source") from exc
    return {
        "jobs": [
            {
                "id": job.id,
                "sourceID": job.source_id,
                "status": job.status.value,
            }
            for job in jobs
        ]
    }


@app.post("/api/skillcell/refresh")
async def api_skillcell_refresh() -> dict[str, Any]:
    runtime = _skillcell_runtime()
    await runtime.refresh_sources()
    return await runtime.status()


@app.get("/api/skillcell/chat/history")
async def api_skillcell_chat_history(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    runtime = _skillcell_runtime()
    try:
        items = await runtime.chat_history(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot read chat history: {exc}") from exc
    return {"items": items}


@app.post("/api/skillcell/chat")
async def api_skillcell_chat(body: dict[str, Any]) -> dict[str, Any]:
    runtime = _skillcell_runtime()
    message = str(body.get("message") or "").strip()
    proposal_id = str(body.get("proposalID") or "").strip() or None
    if not message:
        raise HTTPException(status_code=422, detail="message is required")
    try:
        return await runtime.chat(message, proposal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown proposal") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"chat failed: {exc}") from exc


@app.post("/api/skillcell/chat/reset")
async def api_skillcell_chat_reset() -> dict[str, Any]:
    runtime = _skillcell_runtime()
    try:
        return await runtime.reset_chat()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot reset chat: {exc}") from exc


async def _files_project(node: str, directory: str):
    target, _tree_node, project = await _resolve_project(node, directory)
    if project.get("kind") == "other" or project.get("path") == "__other__":
        raise HTTPException(status_code=400, detail="files are not available for the Other project")
    return target, project


@app.get("/api/files/list")
async def api_files_list(
    node: str = Query(...),
    directory: str = Query(...),
    path: str = Query("."),
) -> dict[str, Any]:
    target, project = await _files_project(node, directory)
    try:
        rel = relative_path(path)
        provider = app.state.files.provider(app.state.oc, target)
        entries = await provider.list(str(project["path"]), rel)
    except FilePathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot list files: {exc}") from exc
    return {
        "node": node,
        "directory": project["path"],
        "path": rel,
        "entries": entries,
    }


@app.get("/api/files/content")
async def api_files_content(
    node: str = Query(...),
    directory: str = Query(...),
    path: str = Query(...),
) -> dict[str, Any]:
    target, project = await _files_project(node, directory)
    try:
        rel = relative_path(path, allow_root=False)
        provider = app.state.files.provider(app.state.oc, target)
        content = await provider.read(str(project["path"]), rel)
    except FilePathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot read file: {exc}") from exc
    return {"node": node, "path": rel, **content}


@app.get("/api/files/status")
async def api_files_status(
    node: str = Query(...),
    directory: str = Query(...),
) -> dict[str, Any]:
    target, project = await _files_project(node, directory)
    try:
        provider = app.state.files.provider(app.state.oc, target)
        files = await provider.status(str(project["path"]))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot read file status: {exc}") from exc
    return {"node": node, "directory": project["path"], "files": files}


@app.get("/api/files/diff")
async def api_files_diff(
    node: str = Query(...),
    directory: str = Query(...),
    path: str = Query(...),
) -> dict[str, Any]:
    target, project = await _files_project(node, directory)
    try:
        rel = relative_path(path, allow_root=False)
        provider = app.state.files.provider(app.state.oc, target)
        diff = await provider.diff(str(project["path"]), rel)
    except FilePathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot read diff: {exc}") from exc
    if diff is None:
        raise HTTPException(status_code=404, detail="no working-tree diff for this file")
    return {"node": node, **diff}


@app.get("/api/files/find")
async def api_files_find(
    node: str = Query(...),
    directory: str = Query(...),
    query: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    target, project = await _files_project(node, directory)
    try:
        provider = app.state.files.provider(app.state.oc, target)
        paths = await provider.find(str(project["path"]), query, limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"cannot search files: {exc}") from exc
    return {"node": node, "directory": project["path"], "paths": paths}
