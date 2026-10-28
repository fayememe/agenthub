#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import websockets

BASE = os.environ.get("AGENTHUB_URL", "http://127.0.0.1:8670").rstrip("/")


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def chrome_path() -> str:
    for name in ("google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit("chrome/chromium not found")


def fetch_json(path: str):
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.loads(response.read().decode())


def pick_project():
    tree = fetch_json("/api/tree")
    fallback = None
    for node in tree.get("nodes", []):
        for project in node.get("projects", []):
            if fallback is None:
                fallback = (node, project)
            try:
                status = fetch_json(
                    f"/api/project-status?node={urllib.parse.quote(node['name'])}"
                    f"&directory={urllib.parse.quote(project['path'])}"
                )
            except Exception:
                continue
            changes = status.get("changes") or {}
            if changes.get("files"):
                return node, project
    if fallback is None:
        raise SystemExit("no projects found")
    return fallback


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0

    async def call(self, method: str, params=None):
        self.next_id += 1
        request_id = self.next_id
        await self.ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result", {})

    async def evaluate(self, expression: str, context_id: int | None = None):
        params = {"expression": expression, "returnByValue": True, "awaitPromise": True}
        if context_id is not None:
            params["contextId"] = context_id
        result = await self.call("Runtime.evaluate", params)
        return result.get("result", {}).get("value")


async def wait_for(cdp: CDP, expression: str, description: str, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await cdp.evaluate(expression)
        if last:
            return last
        await asyncio.sleep(0.2)
    raise AssertionError(f"timeout waiting for {description}: {last!r}")


async def run() -> dict:
    node, project = pick_project()
    url = f"{BASE}/#/{node['name']}/{project['b64']}"
    port = free_port()
    profile = tempfile.mkdtemp(prefix="agenthub-chrome-")
    process = subprocess.Popen(
        [
            chrome_path(),
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=1600,1000",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        targets = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1) as response:
                    targets = json.loads(response.read().decode())
                break
            except Exception:
                await asyncio.sleep(0.1)
        if not targets:
            raise RuntimeError("chrome devtools did not start")

        page = next(target for target in targets if target.get("type") == "page")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=20_000_000) as ws:
            cdp = CDP(ws)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            await cdp.call("Page.navigate", {"url": url})
            await wait_for(cdp, "document.querySelectorAll('.project-row').length > 0", "sidebar projects")
            await wait_for(cdp, "document.getElementById('status-git').textContent.trim().length > 0", "status strip")

            initial = await cdp.evaluate(
                """
                (() => ({
                  rows: document.querySelectorAll('.project-row').length,
                  tabHidden: document.getElementById('panel-tabs').hidden,
                  crumb: document.getElementById('crumbs').textContent,
                  frame: document.getElementById('oc-frame').src,
                  width: getComputedStyle(document.getElementById('sidebar')).width,
                  online: document.getElementById('node-summary').textContent,
                  status: document.getElementById('status-git').textContent,
                }))()
                """
            )
            assert initial["rows"] > 0, initial
            assert initial["tabHidden"] is False, initial
            assert f":{node['proxy_port']}/" in initial["frame"], initial

            resized = await cdp.evaluate(
                """
                (() => {
                  const r = document.getElementById('resizer');
                  const rect = r.getBoundingClientRect();
                  r.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true, clientX:rect.left+1, clientY:120}));
                  window.dispatchEvent(new PointerEvent('pointermove', {bubbles:true, clientX:rect.left+90, clientY:120}));
                  window.dispatchEvent(new PointerEvent('pointerup', {bubbles:true, clientX:rect.left+90, clientY:120}));
                  return getComputedStyle(document.getElementById('sidebar')).width;
                })()
                """
            )
            assert resized != initial["width"], (initial["width"], resized)

            await cdp.evaluate("document.querySelector('.panel-tab[data-panel=\"git\"]').click()")
            await wait_for(
                cdp,
                "!document.getElementById('panel-git').hidden && !document.getElementById('git-files').textContent.includes('loading')",
                "git panel",
            )
            git = await cdp.evaluate(
                """
                (() => ({
                  files: document.querySelectorAll('#git-files .git-file').length,
                  summary: document.getElementById('git-summary').textContent,
                  tabCount: document.getElementById('tab-git-count').textContent,
                  ocHidden: document.getElementById('panel-oc').hidden,
                }))()
                """
            )
            assert git["ocHidden"] is True, git
            assert git["files"] > 0 or "clean" in git["summary"].lower(), git
            assert git["tabCount"], git

            await cdp.evaluate("document.querySelector('.panel-tab[data-panel=\"sessions\"]').click()")
            await wait_for(cdp, "!document.getElementById('panel-sessions').hidden", "sessions panel")
            await wait_for(
                cdp,
                "!document.getElementById('sessions-list').textContent.includes('loading')",
                "sessions data",
            )
            sessions = await cdp.evaluate(
                """
                (() => ({
                  cards: document.querySelectorAll('#sessions-list .session-card').length,
                  text: document.getElementById('sessions-list').textContent.trim().slice(0, 80),
                  tabCount: document.getElementById('tab-session-count').textContent,
                  gitHidden: document.getElementById('panel-git').hidden,
                }))()
                """
            )
            assert sessions["gitHidden"] is True, sessions
            assert sessions["tabCount"], sessions

            await cdp.evaluate("document.querySelector('.panel-tab[data-panel=\"oc\"]').click()")
            oc = await cdp.evaluate(
                """
                (() => ({
                  visible: !document.getElementById('panel-oc').hidden,
                  frameVisible: !document.getElementById('oc-frame').hidden,
                }))()
                """
            )
            assert oc["visible"] and oc["frameVisible"], oc

            await cdp.evaluate("document.getElementById('new-session').click()")
            await asyncio.sleep(1.0)
            new_session_src = await cdp.evaluate("document.getElementById('oc-frame').src")
            assert "/session/" not in new_session_src, new_session_src
            assert "new-session" in new_session_src or new_session_src.rstrip("/").endswith(
                project["b64"]
            ), new_session_src

            await cdp.evaluate("document.querySelector('.project-row.active').click()")
            await asyncio.sleep(1.5)
            last_session_src = await cdp.evaluate("document.getElementById('oc-frame').src")
            assert "/session/" in last_session_src, last_session_src

            frame_tree = await cdp.call("Page.getFrameTree")
            frames = []

            def walk(frame):
                frames.append(frame["frame"])
                for child in frame.get("childFrames", []):
                    walk(child)

            walk(frame_tree["frameTree"])
            iframe_frame = next(
                (frame for frame in frames if f":{node['proxy_port']}/" in frame.get("url", "")),
                None,
            )
            assert iframe_frame, frames
            world = await cdp.call(
                "Page.createIsolatedWorld",
                {"frameId": iframe_frame["id"], "worldName": "agenthub-smoke"},
            )
            seeded = await cdp.evaluate(
                "localStorage.getItem('opencode.global.dat:server')",
                context_id=world["executionContextId"],
            )
            assert project["path"] in (seeded or ""), seeded
            await cdp.evaluate(
                "history.pushState({}, '', '/server/smoke/session/ses_smoke_probe')",
                context_id=world["executionContextId"],
            )
            await asyncio.sleep(4)
            bridge_hash = await cdp.evaluate("location.hash")
            assert "ses_smoke_probe" in bridge_hash, bridge_hash

            return {
                "project": f"{node['name']}/{project['name']}",
                "initial": initial,
                "resized": resized,
                "git": git,
                "sessions": sessions,
                "oc": oc,
                "new_session_src": new_session_src,
                "last_session_src": last_session_src,
                "bridge_hash": bridge_hash,
                "seeded": bool(seeded),
            }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)


def main() -> None:
    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("UI smoke: OK")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"UI smoke: FAIL: {exc}", file=sys.stderr)
        raise
