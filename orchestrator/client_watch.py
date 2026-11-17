"""Cancel a chat request's work when its client goes away (sprint M9 step 4).

A pure ASGI middleware on the four chat routes. It reads the request body,
hands it to the app, and then keeps listening on the connection. When the
client disconnects before the response is complete, it marks the request's
`run_state.Run` (`client_gone`) and cancels the app's task, so the pipeline
stops at its current await: the model lock is released, the trace closes
with `kronk.ended = client_gone`, and a tool call already sent is neither
retried nor reported as failed (nobody is listening).

Before M9 only the streaming responses noticed a disconnect (Starlette's
StreamingResponse listens for it); a buffered request, which is every
production voice request, ran to its end with nobody waiting.

docs/features/model-locks.md.
"""
import asyncio
import logging

import run_state

logger = logging.getLogger(__name__)

CHAT_PATHS = frozenset({"/message", "/api/chat", "/voice/api/chat", "/v1/chat/completions"})


class ClientWatch:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") not in CHAT_PATHS:
            await self.app(scope, receive, send)
            return

        body = []
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return                      # gone before the body arrived
            body.append(msg)
            if not msg.get("more_body"):
                break

        gone = asyncio.Event()
        complete = False

        async def replay():
            if body:
                return body.pop(0)
            await gone.wait()
            return {"type": "http.disconnect"}

        async def tracked_send(msg):
            nonlocal complete
            if msg["type"] == "http.response.body" and not msg.get("more_body"):
                complete = True
            await send(msg)

        run = run_state.Run()
        token = run_state.current.set(run)
        try:
            app_task = asyncio.ensure_future(self.app(scope, replay, tracked_send))
        finally:
            run_state.current.reset(token)

        async def watch():
            while True:
                msg = await receive()
                if msg["type"] != "http.disconnect":
                    continue
                if not complete and not app_task.done():
                    run.client_gone = True
                    app_task.cancel()
                gone.set()
                return

        watcher = asyncio.ensure_future(watch())
        try:
            await asyncio.shield(app_task)
        except asyncio.CancelledError:
            if not app_task.done():
                # This handler was cancelled (server shutdown): pass it on.
                app_task.cancel()
                raise
            if not run.client_gone:
                raise
        finally:
            watcher.cancel()
