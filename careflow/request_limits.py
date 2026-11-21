"""A size limit for what an ANONYMOUS caller may send. The public routes (`/public/...`) take no sign-in, so nothing stops a caller from posting a huge body; the framework would read all of it before any field is checked. This wraps the
application and answers 413 as soon as the size is known: from `Content-Length` before anything is read, or while the chunks of an unsized (chunked) body arrive. Signed-in routes are not touched here (their bodies are bounded by their own
field limits).
"""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

PUBLIC_PREFIX = "/public/"
PUBLIC_MAX_BODY_BYTES = 32 * 1024  # a complaint is at most 4000 characters (at most 4 bytes each, plus JSON escapes): comfortably under this


class PublicBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int = PUBLIC_MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith(PUBLIC_PREFIX):
            await self.app(scope, receive, send)
            return
        declared = next((v for k, v in scope.get("headers", []) if k == b"content-length"), None)
        if declared is not None:
            try:
                too_big = int(declared) > self.max_bytes
            except ValueError:
                too_big = True  # a Content-Length that is not a number is refused, never guessed
            if too_big:
                await self._refuse(send)
                return
        seen = 0
        overflowed = False
        answered = False

        async def counting_receive() -> Message:
            """Past the limit the caller is treated as gone: the framework then stops reading and whatever it answers is replaced by the 413 below. (Raising here would be swallowed by the framework's own body handling and turn into a 400.)"""
            nonlocal seen, overflowed
            if overflowed:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    overflowed = True
                    return {"type": "http.disconnect"}
            return message

        async def replacing_send(message: Message) -> None:
            nonlocal answered
            if not overflowed:
                await send(message)
            elif not answered:
                answered = True
                await self._refuse(send)

        await self.app(scope, counting_receive, replacing_send)
        if overflowed and not answered:
            await self._refuse(send)

    @staticmethod
    async def _refuse(send: Send) -> None:
        body = json.dumps({"detail": "request too large"}).encode()
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})
