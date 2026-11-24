from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .matcher import Matcher, expand


def _cmd_run(args) -> int:
    from .listener import listen

    listen(load_config(args.config), dry_run=args.dry_run)
    return 0


def _cmd_test(args) -> int:
    """Type phrases instead of speaking them, to check matching."""
    cfg = load_config(args.config)
    s = cfg.settings
    matcher = Matcher(expand(cfg.commands, cfg.slots), s.match_mode, s.match_threshold, s.block_words)
    lines = [" ".join(args.text)] if args.text else (l.rstrip("\n") for l in sys.stdin)
    if not args.text and sys.stdin.isatty():
        print("Type a sentence and press Enter (Ctrl+D to quit):")
    for line in lines:
        m = matcher.match(line)
        if m:
            print(f"MATCH  {m.command.name:<20} score={m.score:.0f} slots={m.slots}  action={m.command.action}")
        else:
            print("no match")
    return 0


def _cmd_check(args) -> int:
    cfg = load_config(args.config)
    cands = expand(cfg.commands, cfg.slots)
    print(f"OK: {len(cfg.commands)} commands, {len(cands)} phrases, backends: {', '.join(cfg.backends)}")
    if args.verbose:
        for c in cands:
            print(f"  {c.text!r:45} -> {c.command.name} {c.slots or ''}")
    return 0


def _cmd_devices(args) -> int:
    import sounddevice as sd

    print(sd.query_devices())
    return 0


def _cmd_auth_google(args) -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow

    from .actions.google_assistant import SCOPE

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, scopes=[SCOPE])
    creds = flow.run_local_server(port=args.port, open_browser=not args.no_browser)
    out = Path(args.output)
    out.write_text(json.dumps({
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }, indent=2))
    out.chmod(0o600)
    print(f"Saved Google credentials to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="phrasehome", description=__doc__)
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="listen on the microphone and run commands")
    r.add_argument("--dry-run", action="store_true", help="show matches but don't execute")
    r.set_defaults(fn=_cmd_run)

    t = sub.add_parser("test", help="test matching with typed text")
    t.add_argument("text", nargs="*")
    t.set_defaults(fn=_cmd_test)

    sub.add_parser("check", help="validate config").set_defaults(fn=_cmd_check)
    sub.add_parser("devices", help="list microphones").set_defaults(fn=_cmd_devices)

    a = sub.add_parser("auth-google", help="authorize Google Assistant (one time)")
    a.add_argument("--client-secrets", default="client_secret.json")
    a.add_argument("--output", default="credentials.json")
    a.add_argument("--port", type=int, default=8080)
    a.add_argument("--no-browser", action="store_true", help="print the URL instead of opening it")
    a.set_defaults(fn=_cmd_auth_google)

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
    )
    try:
        return args.fn(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
