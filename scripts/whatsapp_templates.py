"""Submit the WhatsApp message templates for Meta's review (operator machine only).

Lists the WhatsApp Business Account's templates first, then creates (``POST
/{WABA_ID}/message_templates``, category UTILITY, languages ``pl`` and ``en_US``) only the
ones missing, so running it again changes nothing. It sends no message to anyone. Prints
each template's name, language and review status; never the token.

    .venv/bin/python scripts/whatsapp_templates.py [--env .env] [--base-url https://muse.udun.eu]
    .venv/bin/python scripts/whatsapp_templates.py --dry-run   # the bodies, no network

Needs ``META_WHATSAPP_TOKEN`` (``whatsapp_business_management``), ``META_WHATSAPP_WABA_ID``
and, for ``appsecret_proof``, ``META_APP_SECRET``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio import whatsapp  # noqa: E402
from studio.settings import read_env_file  # noqa: E402

DEFAULT_BASE = "https://muse.udun.eu"


def existing(transport: whatsapp.GraphTransport, waba: str) -> dict[tuple[str, str], str]:
    """(name, language) → status of every template of the account (all pages)."""
    out: dict[tuple[str, str], str] = {}
    params = {"fields": "name,language,status", "limit": "200"}
    path = f"{waba}/message_templates"
    for _ in range(20):
        page = transport.get(path, params)
        for t in page.get("data") or []:
            out[(str(t.get("name")), str(t.get("language")))] = str(t.get("status"))
        after = ((page.get("paging") or {}).get("cursors") or {}).get("after")
        if not after or not (page.get("paging") or {}).get("next"):
            break
        params = {**params, "after": str(after)}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default=str(ROOT / ".env"))
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    bodies = list(whatsapp.iter_templates(args.base_url))
    if args.dry_run:
        print(json.dumps(bodies, ensure_ascii=False, indent=2))
        return 0
    env = read_env_file(args.env)
    waba = env.get("META_WHATSAPP_WABA_ID", "")
    if not whatsapp.DIGITS.fullmatch(waba) or not env.get("META_WHATSAPP_TOKEN"):
        print("META_WHATSAPP_TOKEN and META_WHATSAPP_WABA_ID are required", file=sys.stderr)
        return 2
    cfg = whatsapp.WhatsAppConfig("graph", token=env["META_WHATSAPP_TOKEN"], waba_id=waba,
                                  app_secret=env.get("META_APP_SECRET", ""))
    transport = whatsapp.GraphTransport(cfg)
    have = existing(transport, waba)
    for body in bodies:
        key = (body["name"], body["language"])
        if key in have:
            print(f"{key[0]:24} {key[1]:6} exists   {have[key]}")
            continue
        try:
            out = transport.post(f"{waba}/message_templates", body)
        except whatsapp.WhatsAppError as exc:
            print(f"{key[0]:24} {key[1]:6} refused  {exc.code} {exc.detail}")
            continue
        print(f"{key[0]:24} {key[1]:6} created  {out.get('status', '?')} "
              f"({out.get('category', '?')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
