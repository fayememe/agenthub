from __future__ import annotations

import json
import unittest

from aiohttp import web

from skillcell.opencode import (
    OpenCodeAuthError,
    OpenCodeClient,
    OpenCodeError,
    parse_sse_line,
)


EXPECTED_AUTH = "Basic b3BlbmNvZGU6c2VjcmV0"


class OpenCodeClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.seen: list[dict] = []
        app = web.Application()
        app.router.add_get("/global/health", self.health)
        app.router.add_get("/project", self.projects)
        app.router.add_get("/project/p1/directories", self.directories)
        app.router.add_get("/api/session", self.sessions)
        app.router.add_get("/session/ses_1", self.session)
        app.router.add_get("/session/ses_1/message", self.messages)
        app.router.add_get("/session/ses_1/message/msg_1", self.message)
        app.router.add_get("/skill", self.skills)
        app.router.add_post("/session", self.create_session)
        app.router.add_post("/session/ses_1/prompt_async", self.prompt_async)
        app.router.add_get("/global/event", self.events)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self) -> None:
        await self.runner.cleanup()

    def authorized(self, request: web.Request) -> None:
        if request.headers.get("Authorization") != EXPECTED_AUTH:
            raise web.HTTPUnauthorized()

    async def health(self, request: web.Request) -> web.Response:
        self.authorized(request)
        return web.json_response({"healthy": True, "version": "test"})

    async def projects(self, request: web.Request) -> web.Response:
        self.authorized(request)
        return web.json_response(
            [
                {"id": "p1", "worktree": "/work/demo"},
                {"id": "global", "worktree": "/"},
            ]
        )

    async def directories(self, request: web.Request) -> web.Response:
        self.authorized(request)
        return web.json_response([{"directory": "/work/demo"}])

    async def sessions(self, request: web.Request) -> web.Response:
        self.authorized(request)
        self.seen.append(dict(request.query))
        return web.json_response(
            {
                "data": [
                    {
                        "id": "ses_1",
                        "projectID": "p1",
                        "title": "Demo",
                        "time": {"updated": 123},
                        "location": {"directory": "/work/demo"},
                    }
                ],
                "cursor": {"previous": "prev", "next": "next-cursor"},
            }
        )

    async def session(self, request: web.Request) -> web.Response:
        self.authorized(request)
        return web.json_response({"data": {"id": "ses_1", "title": "Demo"}})

    async def messages(self, request: web.Request) -> web.Response:
        self.authorized(request)
        return web.json_response(
            [
                {
                    "info": {"id": "msg_1", "role": "user", "time": {"created": 1000}},
                    "parts": [
                        {"id": "prt_1", "type": "text", "text": "actually check the remote"}
                    ],
                }
            ]
        )

    async def message(self, request: web.Request) -> web.Response:
        self.authorized(request)
        return web.json_response(
            {
                "data": {
                    "info": {"id": "msg_1", "role": "user", "time": {"created": 1000}},
                    "parts": [
                        {"id": "prt_1", "type": "text", "text": "actually check the remote"}
                    ],
                }
            }
        )

    async def skills(self, request: web.Request) -> web.Response:
        self.authorized(request)
        return web.json_response([{"name": "demo", "description": "Demo skill"}])

    async def create_session(self, request: web.Request) -> web.Response:
        self.authorized(request)
        body = await request.json()
        return web.json_response({"id": "ses_new", "title": body.get("title")})

    async def prompt_async(self, request: web.Request) -> web.Response:
        self.authorized(request)
        body = await request.json()
        if body.get("parts", [{}])[0].get("text") != "hello":
            raise web.HTTPBadRequest()
        return web.Response(status=204)

    async def events(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b'data: {"type":"one"}\n\n')
        await response.write(b'data: {"payload":{"type":"two"}}\n\n')
        await response.write_eof()
        return response

    async def test_read_methods(self):
        async with OpenCodeClient(self.base, password="secret") as client:
            self.assertTrue((await client.health())["healthy"])
            projects = await client.projects()
            self.assertEqual([item["id"] for item in projects], ["p1", "global"])
            self.assertEqual(await client.project_directories("p1"), ["/work/demo"])

            page = await client.sessions(
                directory="/work/demo",
                limit=1,
                order="desc",
                cursor="cursor-a",
            )
            self.assertEqual(len(page.items), 1)
            self.assertEqual(page.previous_cursor, "prev")
            self.assertEqual(page.next_cursor, "next-cursor")
            self.assertEqual(self.seen[-1]["order"], "desc")
            self.assertEqual(self.seen[-1]["cursor"], "cursor-a")

            session = await client.session("ses_1")
            self.assertEqual(session["title"], "Demo")
            messages = await client.messages("ses_1", limit=10)
            self.assertEqual(messages[0]["parts"][0]["text"], "actually check the remote")
            message = await client.message("ses_1", "msg_1")
            self.assertEqual(message["info"]["id"], "msg_1")
            self.assertEqual((await client.skills())[0]["name"], "demo")

            created = await client.create_session(directory="/work/demo", title="Audit")
            self.assertEqual(created["id"], "ses_new")
            self.assertEqual(created["title"], "Audit")
            await client.prompt_async("ses_1", "hello")

    async def test_unauthorized(self):
        client = OpenCodeClient(self.base, password="wrong")
        with self.assertRaises(OpenCodeAuthError):
            await client.health()
        await client.close()

    async def test_not_found(self):
        async with OpenCodeClient(self.base, password="secret") as client:
            with self.assertRaises(OpenCodeError):
                await client.project_directories("missing")

    async def test_stream_events(self):
        async with OpenCodeClient(self.base, password="secret") as client:
            events = [event async for event in client.stream_events()]
        self.assertEqual([event["type"] for event in events], ["one", "two"])


class SseParserTests(unittest.TestCase):
    def test_parses_event_and_payload(self):
        self.assertEqual(parse_sse_line('data: {"type":"one"}'), {"type": "one"})
        self.assertEqual(
            parse_sse_line('data: {"payload":{"type":"two"}}'), {"type": "two"}
        )
        self.assertIsNone(parse_sse_line(": ping"))
        self.assertIsNone(parse_sse_line("data: [DONE]"))
        self.assertIsNone(parse_sse_line("data: not-json"))


if __name__ == "__main__":
    unittest.main()
