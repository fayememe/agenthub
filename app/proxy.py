from __future__ import annotations

import asyncio
import base64
import contextlib
import gzip
import json
import time
import zlib
from collections.abc import Mapping
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import aiohttp
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .config import NodeConfig

HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
NO_BODY_STATUS = {204, 304}
BRIDGE_PATH = "__agenthub_bridge.js"
BOOTSTRAP_PATH = "__agenthub_bootstrap.js"
V2_ALIASES = {
    "api/project": ("project", "data"),
    "api/project/current": ("project/current", "data"),
    "api/worktree": ("experimental/worktree", "data"),
}


def _project_dir_from_path(path: str) -> str | None:
    if not path or path.startswith(("api/", "server/", "new-session", "assets/", "favicon", "site.")):
        return None
    first = path.split("/", 1)[0]
    try:
        raw = base64.urlsafe_b64decode(first + "=" * (-len(first) % 4))
        value = raw.decode()
    except Exception:
        return None
    return value if value.startswith("/") else None


def _bootstrap_js(current: str | None, paths: list[str], fetched: bool) -> str:
    return f"""
(() => {{
  try {{
    const key = "opencode.global.dat:server";
    const current = {json.dumps(current)};
    const paths = {json.dumps(paths)};
    const fetched = {json.dumps(fetched)};
    let state = {{}};
    try {{ state = JSON.parse(localStorage.getItem(key) || "{{}}") || {{}}; }} catch (error) {{}}
    if (!state || typeof state !== "object" || Array.isArray(state)) state = {{}};
    let projects = state.projects;
    if (!projects || typeof projects !== "object" || Array.isArray(projects)) projects = {{}};
    const existing = Array.isArray(projects.local) ? projects.local : [];
    if (fetched) {{
      const expanded = new Map(existing.map((item) => [item && item.worktree, !!(item && item.expanded)]));
      projects.local = paths.map((path) => ({{ worktree: path, expanded: current ? path === current : (expanded.get(path) || false) }}));
    }}
    state.list = Array.isArray(state.list) ? state.list : [];
    state.projects = projects;
    if (!state.lastProject || typeof state.lastProject !== "object" || Array.isArray(state.lastProject)) state.lastProject = {{}};
    if (current) state.lastProject.local = current;
    else if (!state.lastProject.local || (fetched && paths.length && !paths.includes(state.lastProject.local))) state.lastProject.local = paths[0] || state.lastProject.local;
    if (!state.recentlyClosed || typeof state.recentlyClosed !== "object" || Array.isArray(state.recentlyClosed)) state.recentlyClosed = {{}};
    localStorage.setItem(key, JSON.stringify(state));

    const tabsKey = "opencode.window.browser.dat:tabs";
    const recentTabsKey = "opencode.window.browser.dat:tabs.recent";
    let tabs = [];
    try {{ tabs = JSON.parse(localStorage.getItem(tabsKey) || "[]"); }} catch (error) {{}}
    if (Array.isArray(tabs)) {{
      let recent = {{}};
      try {{ recent = JSON.parse(localStorage.getItem(recentTabsKey) || "{{}}") || {{}}; }} catch (error) {{}}
      const drafts = tabs.filter((tab) => tab && tab.type === "draft");
      const activeDraft =
        typeof recent.key === "string" && recent.key.startsWith("draft:")
          ? recent.key.slice(6)
          : null;
      const keepDraft =
        drafts.find((tab) => tab.draftID === activeDraft) || drafts[drafts.length - 1] || null;
      const keep = tabs.filter(
        (tab) => !tab || tab.type !== "draft" || (keepDraft && tab.draftID === keepDraft.draftID)
      );
      if (keep.length !== tabs.length) localStorage.setItem(tabsKey, JSON.stringify(keep));
      if (keepDraft) {{
        if (
          !recent.key ||
          (typeof recent.key === "string" &&
            recent.key.startsWith("draft:") &&
            recent.key !== `draft:${{keepDraft.draftID}}`)
        ) {{
          recent.key = `draft:${{keepDraft.draftID}}`;
          localStorage.setItem(recentTabsKey, JSON.stringify(recent));
        }}
      }} else if (typeof recent.key === "string" && recent.key.startsWith("draft:")) {{
        delete recent.key;
        localStorage.setItem(recentTabsKey, JSON.stringify(recent));
      }}
    }}
  }} catch (error) {{}}
}})();
"""


def _map_v2_query(query: str) -> str:
    if not query:
        return query
    mapped = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key == "location[directory]":
            mapped.append(("directory", value))
        elif key == "location[workspace]":
            mapped.append(("workspace", value))
        else:
            mapped.append((key, value))
    return urlencode(mapped, doseq=True)


def _map_v2_path(path: str) -> tuple[str, str | None]:
    if path in V2_ALIASES:
        return V2_ALIASES[path]
    if path.startswith("api/project/") and path.endswith("/directories"):
        return path[4:], "data"
    return path, None
BRIDGE_JS = """
(() => {
  if (window.parent === window) return;
  const send = () => {
    try {
      window.parent.postMessage({
        source: "agenthub",
        type: "route",
        href: location.href,
        pathname: location.pathname,
        search: location.search,
        hash: location.hash,
      }, "*");
    } catch (error) {}
  };
  const wrap = (name) => {
    const original = history[name];
    if (typeof original !== "function") return;
    history[name] = function (...args) {
      const result = original.apply(this, args);
      setTimeout(send, 0);
      return result;
    };
  };
  wrap("pushState");
  wrap("replaceState");
  window.addEventListener("popstate", send);
  window.addEventListener("hashchange", send);
  window.addEventListener("message", (event) => {
    if (event.data && event.data.source === "agenthub" && event.data.type === "ping") send();
  });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", send);
  } else {
    send();
  }
  setInterval(send, 3000);
})();
"""


def _copy_headers(headers: Mapping[str, str], *, skip: set[str] | None = None) -> dict[str, str]:
    skip = skip or set()
    result: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in HOP_BY_HOP or lowered in skip:
            continue
        result[lowered] = value
    return result


def create_proxy_app(node: NodeConfig, client: aiohttp.ClientSession) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    base = node.web_url.rstrip("/")
    base_parts = urlsplit(base)
    upstream_host = base_parts.netloc
    ws_scheme = "wss" if base_parts.scheme == "https" else "ws"
    bootstrap_cache: dict[str, object] = {"at": 0.0, "paths": [], "fetched": False}
    project_path_cache: dict[str, tuple[float, str]] = {}

    async def preferred_project_path(item: dict) -> str:
        path = str(item.get("worktree") or "")
        sandboxes = [str(value) for value in item.get("sandboxes") or [] if value]
        project_id = str(item.get("id") or "")
        if not sandboxes or not project_id:
            return path

        now = time.monotonic()
        cached = project_path_cache.get(project_id)
        if cached and now - cached[0] < 60:
            return cached[1]

        preferred = ""
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with client.get(
                node.api_url + f"/project/{quote(project_id, safe='')}/directories",
                headers={"Authorization": node.auth_header},
                timeout=timeout,
            ) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    directories = [
                        str(entry.get("directory"))
                        for entry in data
                        if isinstance(entry, dict) and entry.get("directory")
                    ]
                    if directories:
                        preferred = directories[0]
        except Exception:
            pass

        if not preferred:
            preferred = sandboxes[0] if sandboxes else path
        project_path_cache[project_id] = (now, preferred)
        return preferred

    def target_url(path: str, query: str) -> str:
        url = f"{base}/{path}" if path else f"{base}/"
        if query:
            url += f"?{query}"
        return url

    @app.api_route("/", methods=HTTP_METHODS)
    @app.api_route("/{path:path}", methods=HTTP_METHODS)
    async def http_proxy(request: Request, path: str = ""):
        if path == BRIDGE_PATH:
            return Response(BRIDGE_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})
        if path == BOOTSTRAP_PATH:
            current = request.query_params.get("dir") or None
            now = time.monotonic()
            if now - float(bootstrap_cache["at"]) > 15:
                try:
                    timeout = aiohttp.ClientTimeout(total=5)
                    async with client.get(
                        node.api_url + "/project",
                        headers={"Authorization": node.auth_header},
                        timeout=timeout,
                    ) as response:
                        if response.status == 200:
                            data = await response.json(content_type=None)
                            if isinstance(data, list):
                                paths: list[str] = []
                                for item in data:
                                    if not isinstance(item, dict) or item.get("id") == "global":
                                        continue
                                    candidate = await preferred_project_path(item)
                                    if candidate and candidate != "/":
                                        paths.append(candidate)
                                bootstrap_cache["paths"] = paths
                                bootstrap_cache["fetched"] = True
                except Exception:
                    pass
                bootstrap_cache["at"] = now
            return Response(
                _bootstrap_js(current, list(bootstrap_cache["paths"]), bool(bootstrap_cache["fetched"])),
                media_type="application/javascript",
                headers={"Cache-Control": "no-store"},
            )

        upstream_path, wrap = _map_v2_path(path)
        query = _map_v2_query(request.url.query) if upstream_path != path else request.url.query
        target = target_url(upstream_path, query)
        headers = _copy_headers(request.headers, skip={"host", "authorization"})
        headers["host"] = upstream_host
        headers["authorization"] = node.auth_header

        wants_html = (
            request.headers.get("sec-fetch-dest") == "document"
            or "text/html" in request.headers.get("accept", "")
        )
        if wants_html or wrap:
            headers["accept-encoding"] = "identity"

        body = await request.body() if request.method not in {"GET", "HEAD"} else None
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=None)

        try:
            upstream = await client.request(
                request.method,
                target,
                headers=headers,
                data=body,
                allow_redirects=False,
                auto_decompress=False,
                timeout=timeout,
            )
        except aiohttp.ClientError as exc:
            return JSONResponse({"error": f"upstream error: {exc}"}, status_code=502)

        response_headers = _copy_headers(upstream.headers)
        response_headers.pop("content-length", None)

        if request.method == "HEAD" or upstream.status in NO_BODY_STATUS:
            upstream.release()
            return Response(status_code=upstream.status, headers=response_headers)

        content_type = upstream.headers.get("content-type", "").lower()
        if (
            path in {"project", "api/project", "project/current", "api/project/current"}
            and 200 <= upstream.status < 300
            and "json" in content_type
        ):
            payload = await upstream.json(content_type=None)
            upstream.release()
            response_headers.pop("content-length", None)
            response_headers.pop("content-encoding", None)
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if isinstance(item, dict) and item.get("sandboxes"):
                    item["worktree"] = await preferred_project_path(item)
            if wrap:
                return JSONResponse({"data": payload}, status_code=upstream.status, headers=response_headers)
            return JSONResponse(payload, status_code=upstream.status, headers=response_headers)

        if wrap and 200 <= upstream.status < 300 and "json" in content_type:
            payload = await upstream.json(content_type=None)
            upstream.release()
            response_headers.pop("content-length", None)
            response_headers.pop("content-encoding", None)
            return JSONResponse({"data": payload}, status_code=upstream.status, headers=response_headers)

        if content_type.startswith("text/html"):
            raw = await upstream.read()
            encoding = upstream.headers.get("content-encoding", "").lower()
            upstream.release()
            if "gzip" in encoding:
                raw = gzip.decompress(raw)
            elif "deflate" in encoding:
                raw = zlib.decompress(raw)
            elif encoding:
                return Response(raw, status_code=upstream.status, headers=response_headers)

            charset = upstream.charset or "utf-8"
            html = raw.decode(charset, errors="replace")
            current = _project_dir_from_path(path)
            if not current and path == "new-session":
                candidate = request.query_params.get("directory")
                if candidate and candidate.startswith("/"):
                    current = candidate
            bootstrap_src = "/__agenthub_bootstrap.js"
            if current:
                bootstrap_src += "?dir=" + quote(current, safe="")
            bootstrap_snippet = f'<script src="{bootstrap_src}"></script>'
            if '<script type="module"' in html:
                html = html.replace('<script type="module"', bootstrap_snippet + '<script type="module"', 1)
            elif "</head>" in html:
                html = html.replace("</head>", bootstrap_snippet + "</head>", 1)
            else:
                html = bootstrap_snippet + html
            bridge_snippet = '<script src="/__agenthub_bridge.js"></script>'
            if "</body>" in html:
                html = html.replace("</body>", bridge_snippet + "</body>", 1)
            else:
                html += bridge_snippet
            response_headers.pop("content-encoding", None)
            response_headers.pop("content-length", None)
            return Response(html, status_code=upstream.status, media_type="text/html", headers=response_headers)

        async def stream_body():
            try:
                async for chunk in upstream.content.iter_any():
                    yield chunk
            finally:
                upstream.release()

        return StreamingResponse(
            stream_body(),
            status_code=upstream.status,
            headers=response_headers,
        )

    @app.websocket("/")
    @app.websocket("/{path:path}")
    async def websocket_proxy(websocket: WebSocket, path: str = ""):
        target = f"{ws_scheme}://{upstream_host}/{path}"
        if websocket.url.query:
            target += f"?{websocket.url.query}"

        headers = {
            "authorization": node.auth_header,
            "host": upstream_host,
            "origin": f"{base_parts.scheme}://{upstream_host}",
        }
        protocols = websocket.scope.get("subprotocols") or ()

        try:
            upstream = await client.ws_connect(
                target,
                headers=headers,
                protocols=protocols,
                max_msg_size=0,
                autoping=True,
            )
        except Exception as exc:
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason=str(exc)[:120])
            return

        await websocket.accept(subprotocol=upstream.protocol)

        async def client_to_upstream():
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    if message.get("text") is not None:
                        await upstream.send_str(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send_bytes(message["bytes"])
            except Exception:
                pass

        async def upstream_to_client():
            try:
                async for message in upstream:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        await websocket.send_text(message.data)
                    elif message.type == aiohttp.WSMsgType.BINARY:
                        await websocket.send_bytes(message.data)
                    elif message.type in {
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    }:
                        break
            except Exception:
                pass

        tasks = [
            asyncio.create_task(client_to_upstream()),
            asyncio.create_task(upstream_to_client()),
        ]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        with contextlib.suppress(Exception):
            await upstream.close()
        with contextlib.suppress(Exception):
            await websocket.close()

    return app
