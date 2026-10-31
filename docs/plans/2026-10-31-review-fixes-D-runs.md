# Group D — runs, signing route, webhooks, deploy, studio form
Owns: `studio/routes_studio.py`, `studio/mcp_server.py`, `studio/routes_whatsapp.py`,
`studio/routes_meta.py`, `studio/routes_report.py`, `deploy/Caddyfile`,
`studio/ui/templates/studio.html`, `scripts/eval.py`, `studio/ui/static/brief.js`,
their tests. Read common rules.

1. **Limited accounts' landing check skips google.create and mock Meta**
   (routes_studio.py ~1027-1048). Apply `demo_link_ok` to google.create's `link`
   and any final URLs in its `google` spec whenever that adapter is live, and to
   Meta campaign.create whenever Meta is live (as now). Test both.
2. **Ownership on approvals** (routes_studio.py ~1004): signing an approval or reading
   its payload must check that the approval belongs to the session's account
   (operator/full accounts as today's rules say; verify what exists first).
3. **active_run leak** (routes_studio.py ~547-552, ~746-761): wrap claim→thread.start
   in try/except that resets `active_run` on any exception.
4. **MCP run stuck in running** (mcp_server.py ~389): clients()/run_context() must be
   inside the try that marks the run failed. Also: the stdio MCP should respect
   per-account isolation of API-drafted briefs if that rule exists for HTTP —
   verify, fix or document.
5. **WhatsApp webhook body without Content-Length** (routes_whatsapp.py ~129-131): read
   the stream with a running size cap (413 past MAX_WEBHOOK_BODY) before HMAC.
   Also: `SenderLimiter` never evicts idle senders (bound it); a `MUSE-123456`
   text from an already-linked owner must not be swallowed as verification.
6. **Meta leadgen webhook ignores STUDIO_READ_ONLY** (routes_meta.py ~191-220):
   acknowledge without ingesting (200, recorded) or 503 when read-only — choose
   what Meta retries correctly (503 → Meta retries later; prefer that).
7. **Report blocks the event loop** (routes_report.py ~122): build all inputs inside
   the threadpool callable.
8. **Caddy** (deploy/Caddyfile ~36): `microphone=(self)` (voice brief and Dictate),
   and exempt `/leads/import` from the 128 KB `@small` body cap (app allows ~2 MiB).
9. **studio.html field() attrs escaped** (~31): `{{ attrs|safe }}` only if every
   caller passes literal template strings (check; never user data). Add a
   rendering test asserting `min="0"` etc. appear unescaped.
10. **brief.js** button stays disabled after a failed fetch: re-enable in finally.
11. **scripts/eval.py** counts ERROR as flagged and omits two attack suites from the
    pass count: fix the counting, list suites explicitly.
Tests: one per item where testable.
