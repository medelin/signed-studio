"""``studio account create|list|disable|enable|reset-password``: the operator's tools.

There is no public sign-up; accounts exist only when the operator creates them here.
Passwords are read from the terminal (twice, not echoed) or from standard input with
``--password-stdin`` (one line), never from the command line, where ``ps`` and shell
history would see them. The database is the server's (``STUDIO_DATA_DIR``, default
``var``, from ``--env-file``); changes reach a running server on its next request (a disabled
account's sessions stop working at once).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from studio import db
from studio.accounts import AccountError, Accounts
from studio.settings import Settings


class UsageError(Exception):
    pass


def add_parser(sub: Any) -> None:
    p = sub.add_parser("account", help="create and manage user accounts (operator)")
    p.add_argument("--env-file", type=Path, default=Path(".env"))
    acts = p.add_subparsers(dest="action", required=True)
    create = acts.add_parser("create", help="create an account (asks for the password)")
    create.add_argument("username")
    create.add_argument("--display-name", default="")
    create.add_argument("--email", default="")
    create.add_argument("--password-stdin", action="store_true")
    acts.add_parser("list", help="list accounts")
    for name, text in (("disable", "disable an account and end its sessions"),
                       ("enable", "enable a disabled account")):
        acts.add_parser(name, help=text).add_argument("username")
    reset = acts.add_parser("reset-password", help="set a new password, end every session")
    reset.add_argument("username")
    reset.add_argument("--password-stdin", action="store_true")
    reset.add_argument("--clear-2fa", action="store_true",
                       help="also turn two-step sign-in off (lost phone and recovery codes)")


def _password(from_stdin: bool, stdin: TextIO) -> str:
    if from_stdin:
        return stdin.readline().rstrip("\r\n")
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat it: "):
        raise UsageError("the passwords differ")
    return first


def _ts(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M") if value else "-"


def run(args: argparse.Namespace, stdin: TextIO | None = None) -> int:
    stdin = stdin or sys.stdin
    settings = Settings.load(env_file=args.env_file)
    path = settings.data_dir / "studio.db"
    if not path.exists():
        raise UsageError(f"no database at {path}; start the server once or check "
                         "STUDIO_DATA_DIR")
    conn = db.connect(path)
    try:
        db.init(conn)
        store = Accounts(conn)
        try:
            if args.action == "create":
                account = store.create(args.username, _password(args.password_stdin, stdin),
                                       display_name=args.display_name, email=args.email)
                print(f"created {account.username} ({account.id})")
            elif args.action == "list":
                print(f"{'user':<20} {'status':<9} {'2fa':<4} {'demo':<5} {'created':<17} id")
                for a in store.listing():
                    print(f"{a.username:<20} {a.status:<9} {'on' if a.totp_enabled else '-':<4} "
                          f"{'yes' if a.is_demo else '-':<5} {_ts(a.created_ts):<17} {a.id}")
            elif args.action in ("disable", "enable"):
                account = store.set_status(
                    args.username, "disabled" if args.action == "disable" else "active")
                print(f"{account.username}: {account.status}")
            elif args.action == "reset-password":
                account = store.reset_password(
                    args.username, _password(args.password_stdin, stdin),
                    clear_2fa=args.clear_2fa)
                extra = ", two-step sign-in off" if args.clear_2fa else ""
                print(f"{account.username}: password reset, sessions ended{extra}")
        except AccountError as exc:
            raise UsageError(str(exc)) from exc
    finally:
        conn.close()
    return 0
