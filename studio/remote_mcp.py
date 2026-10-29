"""``/mcp``: Muse Studio Lite as a remote MCP server (Streamable HTTP, official ``mcp`` SDK)
for the owner's own assistant: Claude Desktop, Claude Code, claude.ai or ChatGPT custom
connectors, any MCP client that can send a header.

- **Auth**: the same personal access tokens as ``/api/v1`` (``Authorization: Bearer``),
  checked on every HTTP request before the SDK sees it (``routes_api.BearerGate``), so a
  revoked or expired token is cut off on its next message. Cookies never authorize. No
  OAuth authorization server: the SDK only offers the resource-server half cheaply, and
  a token made on ``/account`` keeps the human in charge of who connects.
- **Stateless, JSON responses**: every POST gets a fresh transport and server session
  (``stateless=True``) and one JSON answer, no long-lived SSE stream: nothing to buffer
  behind Caddy, nothing that outlives a revoked token.
- **Tools** are the operations of ``studio.assistants`` (``Op.mcp``), each call through
  ``Assistants.call`` (scope check, schema, rate limit, receipt with transport ``mcp``).
  Every description says the assistant cannot sign or launch; the server's
  ``instructions`` say it once more. The stdio server (``studio mcp``) shares the server
  construction (``mcp_server.make_server``).
"""

from __future__ import annotations

from typing import Any

import anyio

from studio import assistants as ops
from studio import mcp_server


class RemoteMcp:
    """The ASGI app behind ``/mcp`` (wrapped by the bearer gate)."""

    def __init__(self, svc: ops.Assistants, principal: ops.Principal | None = None) -> None:
        self.svc = svc
        self.principal = principal  # in-process use (tests): no HTTP request to read it from
        tools = [(o.name, o.description, o.schema) for o in ops.OPS.values() if o.mcp]
        self.server = mcp_server.make_server(tools, self._call, instructions=ops.INSTRUCTIONS)

    def _call(self, ctx: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request = getattr(ctx, "request", None)
        principal = request.scope["state"]["principal"] if request is not None \
            else self.principal
        if principal is None:
            return {"error": "invalid_token"}
        op = ops.OPS.get(name)
        if op is not None and not op.mcp:  # HTTP-only operations are not tools here
            name = "mcp-unavailable:" + name
        return self.svc.call(principal, name, arguments)[1]

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        from mcp.server.streamable_http import StreamableHTTPServerTransport

        transport = StreamableHTTPServerTransport(mcp_session_id=None,
                                                  is_json_response_enabled=True,
                                                  event_store=None)

        async def run_server(*, task_status: Any = anyio.TASK_STATUS_IGNORED) -> None:
            async with transport.connect() as (read, write):
                task_status.started()
                await self.server.run(read, write, self.server.create_initialization_options(),
                                      stateless=True)

        async with anyio.create_task_group() as tg:
            await tg.start(run_server)
            try:
                await transport.handle_request(scope, receive, send)
            finally:
                with anyio.CancelScope(shield=True):
                    await transport.terminate()
