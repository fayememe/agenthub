from __future__ import annotations

import asyncio
import socket
import unittest

import aiohttp
import uvicorn
from aiohttp import web

from app.config import NodeConfig
from app.proxy import create_proxy_app

AUTH = "Basic b3BlbmNvZGU6eA=="


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def http_handler(request):
            if request.headers.get("Authorization") != AUTH:
                return web.Response(status=401)
            return web.Response(text="upstream-ok", headers={"X-Test": "yes"})

        async def sse_handler(request):
            if request.headers.get("Authorization") != AUTH:
                return web.Response(status=401)
            response = web.StreamResponse(
                headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
            )
            await response.prepare(request)
            await response.write(b"data: a\n\n")
            await response.write(b"data: b\n\n")
            await response.write_eof()
            return response

        async def html_handler(request):
            if request.headers.get("Authorization") != AUTH:
                return web.Response(status=401)
            return web.Response(
                text='<html><head><script type="module" src="/app.js"></script></head><body>hello</body></html>',
                content_type="text/html",
            )

        async def project_handler(request):
            if request.headers.get("Authorization") != AUTH:
                return web.Response(status=401)
            return web.json_response([{"id": "p1", "worktree": "/work/demo", "vcs": "git"}])

        async def current_handler(request):
            if request.headers.get("Authorization") != AUTH:
                return web.Response(status=401)
            if request.query.get("directory") != "/work/demo":
                return web.json_response({"error": "missing directory"}, status=400)
            return web.json_response({"id": "p1", "worktree": "/work/demo", "vcs": "git"})

        async def directories_handler(request):
            if request.headers.get("Authorization") != AUTH:
                return web.Response(status=401)
            return web.json_response([{"directory": "/work/demo"}])

        async def worktree_handler(request):
            if request.headers.get("Authorization") != AUTH:
                return web.Response(status=401)
            return web.json_response(["/work/demo", "/work/demo-wt"])

        async def ws_handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    await ws.send_str("echo:" + message.data)
            return ws

        upstream = web.Application()
        upstream.router.add_get("/", http_handler)
        upstream.router.add_get("/event", sse_handler)
        upstream.router.add_get("/html", html_handler)
        upstream.router.add_get("/project", project_handler)
        upstream.router.add_get("/project/current", current_handler)
        upstream.router.add_get("/project/p1/directories", directories_handler)
        upstream.router.add_get("/experimental/worktree", worktree_handler)
        upstream.router.add_get("/ws", ws_handler)

        self.upstream_runner = web.AppRunner(upstream)
        await self.upstream_runner.setup()
        self.upstream_port = free_port()
        self.upstream_site = web.TCPSite(self.upstream_runner, "127.0.0.1", self.upstream_port)
        await self.upstream_site.start()

        self.client = aiohttp.ClientSession()
        self.proxy_port = free_port()
        node = NodeConfig(
            name="test",
            api_url=f"http://127.0.0.1:{self.upstream_port}",
            web_url=f"http://127.0.0.1:{self.upstream_port}",
            username="opencode",
            password="x",
            proxy_port=self.proxy_port,
        )
        self.server = uvicorn.Server(
            uvicorn.Config(
                create_proxy_app(node, self.client),
                host="127.0.0.1",
                port=self.proxy_port,
                log_config=None,
                log_level=None,
                access_log=False,
                lifespan="off",
            )
        )
        self.task = asyncio.create_task(self.server.serve())
        for _ in range(200):
            if self.server.started:
                break
            await asyncio.sleep(0.01)
        if not self.server.started:
            raise RuntimeError("proxy server did not start")

    async def asyncTearDown(self):
        self.server.should_exit = True
        await asyncio.wait_for(self.task, timeout=5)
        await self.client.close()
        await self.upstream_runner.cleanup()

    async def test_http_and_auth_injection(self):
        async with self.client.get(f"http://127.0.0.1:{self.proxy_port}/") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.text(), "upstream-ok")
            self.assertEqual(response.headers.get("X-Test"), "yes")

    async def test_sse_stream(self):
        async with self.client.get(f"http://127.0.0.1:{self.proxy_port}/event") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get("Content-Type"), "text/event-stream")
            body = await response.read()
        self.assertIn(b"data: a", body)
        self.assertIn(b"data: b", body)

    async def test_bridge_script_and_html_injection(self):
        async with self.client.get(f"http://127.0.0.1:{self.proxy_port}/__agenthub_bridge.js") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("postMessage", await response.text())

        async with self.client.get(
            f"http://127.0.0.1:{self.proxy_port}/html",
            headers={"Accept": "text/html"},
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.headers.get("Content-Type", ""))
            body = await response.text()
        self.assertIn("hello", body)
        self.assertIn('src="/__agenthub_bridge.js"', body)
        self.assertIn('src="/__agenthub_bootstrap.js', body)
        self.assertLess(body.index("__agenthub_bootstrap.js"), body.index('<script type="module"'))

        async with self.client.get(
            f"http://127.0.0.1:{self.proxy_port}/__agenthub_bootstrap.js?dir=/work/demo"
        ) as response:
            self.assertEqual(response.status, 200)
            bootstrap = await response.text()
        self.assertIn("opencode.global.dat:server", bootstrap)
        self.assertIn("/work/demo", bootstrap)

    async def test_v2_project_compatibility(self):
        async with self.client.get(f"http://127.0.0.1:{self.proxy_port}/api/project") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["data"][0]["worktree"], "/work/demo")

        async with self.client.get(
            f"http://127.0.0.1:{self.proxy_port}/api/project/current",
            params={"location[directory]": "/work/demo"},
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["data"]["id"], "p1")

        async with self.client.get(
            f"http://127.0.0.1:{self.proxy_port}/api/project/p1/directories"
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["data"][0]["directory"], "/work/demo")

        async with self.client.get(
            f"http://127.0.0.1:{self.proxy_port}/api/worktree",
            params={"location[directory]": "/work/demo"},
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["data"], ["/work/demo", "/work/demo-wt"])

    async def test_websocket(self):
        ws = await self.client.ws_connect(f"http://127.0.0.1:{self.proxy_port}/ws")
        await ws.send_str("hello")
        message = await asyncio.wait_for(ws.receive(), timeout=2)
        self.assertEqual(message.type, aiohttp.WSMsgType.TEXT)
        self.assertEqual(message.data, "echo:hello")
        await ws.close()


if __name__ == "__main__":
    unittest.main()
