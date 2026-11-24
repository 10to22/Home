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


def _cmd_speakers(args) -> int:
    from .speakers import SpeakerHub

    cfg = load_config(args.config)
    hubs = [(n, b) for n, b in cfg.backends.items() if b["type"] == "speakers"]
    if not hubs:
        print("no `type: speakers` backend in config", file=sys.stderr)
        return 2
    hub = SpeakerHub(hubs[0][0], hubs[0][1], cfg)
    names = args.names or ["all"]
    if args.action == "status":
        for name, info in hub.status().items():
            if isinstance(info, dict):
                print(f"{name:<14} {info.get('mode', '?'):<10} volume {info.get('volume', '?')}"
                      f"{'  (synced)' if info.get('synced') else ''}")
            else:
                print(f"{name:<14} {info}")
    elif args.action in ("group", "bluetooth"):
        for n in names:
            print(f"{args.action}: {', '.join(hub.set_mode(n, args.action))}")
    elif args.action == "volume":
        if len(names) < 2:
            print("usage: phrasehome speakers volume <speaker|all> <0-100|up|down>", file=sys.stderr)
            return 2
        print(f"volume: {', '.join(hub.set_volume(names[0], names[1]))}")
    return 0


def _face_tools(cfg):
    from .door_scanner import DEFAULTS
    from .faces import FaceBank, OpenCVFaceEngine

    sc = {**DEFAULTS, **(cfg.door_scanners[0] if cfg.door_scanners else {})}
    bank = FaceBank(cfg.resolve_path(cfg.data_dir) / "faces")
    engine = lambda: OpenCVFaceEngine(cfg.resolve_path(sc["detector_model"]),  # noqa: E731
                                      cfg.resolve_path(sc["recognizer_model"]))
    return sc, bank, engine


def _cmd_faces(args) -> int:
    cfg = load_config(args.config)
    sc, bank, engine = _face_tools(cfg)
    a = args.action
    if a == "list":
        if not bank.people:
            print("face bank is empty. Add someone: phrasehome faces enroll \"Your Name\" --camera")
        for p in sorted(bank.people.values(), key=lambda p: (p.kind, p.id)):
            print(f"{p.id:<16} {p.name:<18} {'ALLOWED' if p.allowed else 'not allowed':<12} "
                  f"{p.kind:<9} seen {p.seen}x, last {p.last_seen or '-'}, {len(p.embeddings)} samples")
        return 0
    if a == "scans":
        from .events import EventLog

        for e in EventLog(cfg.resolve_path(cfg.data_dir) / "events.jsonl").query("scan", limit=args.n):
            if e["kind"] == "scan":
                print(f"{e['time']}  {e['door']:<8} {e['result']:<8} {e['name']:<18} "
                      f"score {e.get('score', 0):.2f}  {e.get('photo') or ''}")
        return 0
    if not args.ref:
        print(f"usage: phrasehome faces {a} <name or id>", file=sys.stderr)
        return 2
    ref = " ".join(args.ref)
    try:
        if a == "enroll":
            eng = engine()
            embs, photo = [], b""
            if args.image:
                import cv2

                for path in args.image:
                    img = cv2.imread(path)
                    faces = eng.faces(img) if img is not None else []
                    if not faces:
                        print(f"  no face found in {path}, skipped")
                        continue
                    f = max(faces, key=lambda f: f.size)
                    embs.append(eng.embed(img, f))
                    photo = photo or eng.crop_jpeg(img, f)
            if args.camera:
                from .door_scanner import DoorScanner

                print("Look at the door camera. Turn your head a little and change expression...")

                class _H:  # DoorScanner only needs config here
                    config = cfg

                got, ph = DoorScanner(_H, sc, engine=eng).capture_embeddings(args.samples)
                embs += got
                photo = photo or ph
            if not embs:
                print("no face samples collected; use --camera and/or --image", file=sys.stderr)
                return 1
            p = bank.enroll(ref, embs, allowed=not args.deny)
            if photo:
                bank.save_photo(p.id, photo)
            print(f"enrolled {p.name} ({p.id}) with {len(embs)} samples, "
                  f"{'allowed' if p.allowed else 'NOT allowed'}")
        elif a == "allow":
            p = bank.set_allowed(ref, True, name=args.name, hours=args.hours)
            print(f"{p.name} ({p.id}) is now allowed" + (f" until {p.allowed_until}" if p.allowed_until else ""))
        elif a == "deny":
            p = bank.set_allowed(ref, False)
            print(f"{p.name} ({p.id}) is now NOT allowed")
        elif a == "rename":
            if not args.name:
                print("usage: phrasehome faces rename <id> --name \"New Name\"", file=sys.stderr)
                return 2
            p = bank.rename(ref, args.name)
            print(f"{p.id} is now called {p.name}")
        elif a == "delete":
            p = bank.delete(ref)
            print(f"deleted {p.name} ({p.id}) from the face bank")
    except KeyError as e:
        print(e.args[0], file=sys.stderr)
        return 1
    return 0


def _cmd_voice(args) -> int:
    from .voice import Voice

    from .dispatcher import Dispatcher

    cfg = load_config(args.config)
    home = Dispatcher(cfg)
    v = Voice(cfg)
    text = " ".join(args.text)
    if args.action == "list":
        for key in sorted(v.phrases):
            clip = v._clip(key)
            entry = v.phrases[key]
            shown = " | ".join(entry) if isinstance(entry, list) else entry
            print(f"{key:<24} {'[recorded clip] ' if clip else ''}{shown}")
    elif args.action == "say":
        print(v.speak(text or "Hello. This is your house speaking."))
    elif args.action == "record":
        if not text:
            print("usage: phrasehome voice record <phrase_key> [--seconds 4]", file=sys.stderr)
            return 2
        print(f"Recording {args.seconds:.0f} seconds for '{text}' - speak now...")
        print(f"saved {v.record(text, args.seconds)}")
    return 0


def _cmd_ask(args) -> int:
    """Type a question to the AI assistant (as if you'd said "house, ...")."""
    from .assistant import Assistant
    from .dispatcher import Dispatcher

    cfg = load_config(args.config)
    if not cfg.assistant:
        print("add an `assistant:` section to the config first", file=sys.stderr)
        return 2
    home = Dispatcher(cfg, dry_run=args.dry_run)
    try:
        ai = Assistant(home)
        questions = [" ".join(args.question)] if args.question else None
        if questions is None:
            print(f"Talk to {ai.names[0]} (Ctrl+D to quit):")
            questions = (q.strip() for q in sys.stdin)
        for q in questions:
            if q:
                answer = ai.ask(q, room=args.room)
                print(f"{ai.names[0]}: {answer}")
                if args.speak:
                    home.voice.speak(answer)
    finally:
        home.close()
    return 0


def _cmd_agenda(args) -> int:
    """Everyone's reminders, alarms, timers and lists (what the main room sees)."""
    from .dispatcher import Dispatcher

    home = Dispatcher(load_config(args.config), dry_run=True)
    try:
        insts = home.instances
        for i in insts.all.values():
            if args.person and not i.matches_person(args.person):
                continue
            print(f"== {i.name}{' (main)' if i.main else ''}  rooms: {', '.join(i.rooms) or '-'}"
                  f"{'  [do not disturb]' if insts.dnd(i) else ''}")
            for it in insts.items(i):
                print(f"  {it['due'][:16].replace('T', ' ')}  {it['kind']:<8} {it['text'] or it['label']}"
                      f"{'  (' + it['repeat'] + ')' if it['repeat'] != 'none' else ''}  [{it['id']}]")
            for n in insts.list_names(i):
                if i.main or n not in insts.shared_lists:
                    print(f"  list {n}: {', '.join(insts.list_get(i, n)) or '(empty)'}")
            for m in insts.memories(i)[-5:]:
                print(f"  note: {m['text']}")
    finally:
        home.close()
    return 0


def _cmd_fingerprint(args) -> int:
    from .dispatcher import Dispatcher
    from .fingerprint import FingerprintDoor

    cfg = load_config(args.config)
    if not cfg.fingerprint:
        print("add a `fingerprint:` section to the config first", file=sys.stderr)
        return 2
    fp_cfg = next((f for f in cfg.fingerprint if str(f.get("door", "front")) == args.door), cfg.fingerprint[0])
    home = Dispatcher(cfg, dry_run=True)
    try:
        fp = FingerprintDoor(home, fp_cfg)
        if args.action == "list":
            for p in fp.bank.people.values():
                if p.fingerprints:
                    print(f"{p.name:<20} fingers {p.fingerprints}")
            return 0
        who = " ".join(args.person)
        if not who:
            print("usage: phrasehome fingerprint enroll <name>", file=sys.stderr)
            return 2
        fp.bank.find(who)  # must be in the face bank first
        print(f"Place {who}'s finger on the sensor, lift it, then place it again (LED guides you)...")
        fid = fp.enroll(who)
        print(f"saved as finger #{fid} for {who}")
    except KeyError as e:
        print(f"{e.args[0]} (enroll their face first: phrasehome faces enroll ...)", file=sys.stderr)
        return 1
    finally:
        home.close()
    return 0


def _cmd_backup(args) -> int:
    """Pack config + data (face bank, events, reminders) + recorded voices into one file."""
    import tarfile
    import time as _t
    from pathlib import Path

    cfg = load_config(args.config)
    out = Path(args.to or ".") / _t.strftime("phrasehome-backup-%Y%m%d-%H%M.tar.gz")
    with tarfile.open(out, "w:gz") as tar:
        tar.add(args.config, arcname="config.yaml")
        for extra in (cfg.data_dir, "voice_bank", ".env", "credentials.json"):
            p = cfg.resolve_path(extra)
            if p.exists():
                tar.add(p, arcname=Path(extra).name,
                        filter=lambda ti: None if "voice_cache" in ti.name else ti)
    out.chmod(0o600)
    print(f"saved {out}\nIt contains face data, voice recordings and secrets: keep it somewhere private.")
    return 0


def _cmd_auth_spotify(args) -> int:
    from .dispatcher import Dispatcher
    from .spotify import login

    home = Dispatcher(load_config(args.config), dry_run=True)
    try:
        login(home, open_browser=not args.no_browser)
    finally:
        home.close()
    return 0


def _cmd_package(args) -> int:
    from .dispatcher import Dispatcher
    from .packages import Packages

    home = Dispatcher(load_config(args.config), dry_run=True)
    try:
        p = Packages(home)
        if args.action == "add":
            it = p.add(args.number, args.name or "")
            print(f"tracking {it['name']} ({it['number']})")
        elif args.action == "remove":
            print(f"removed {p.remove(args.number)}")
        else:
            for it in p.items:
                print(f"{it['name']:<20} {it['number']:<24} {it['status']:<16} {it.get('eta', '')}  {it.get('description', '')}")
    finally:
        home.close()
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

    fc = sub.add_parser("faces", help="manage the door face bank",
                        description="phrasehome faces list | scans | enroll NAME --camera | "
                                    "allow ID [--name N] | deny ID | rename ID --name N | delete ID")
    fc.add_argument("action", choices=["list", "scans", "enroll", "allow", "deny", "rename", "delete"],
                    nargs="?", default="list")
    fc.add_argument("ref", nargs="*", help="name or id")
    fc.add_argument("--camera", action="store_true", help="enroll from the door camera")
    fc.add_argument("--image", nargs="+", help="enroll from photo files")
    fc.add_argument("--samples", type=int, default=8)
    fc.add_argument("--deny", action="store_true", help="enroll as NOT allowed (e.g. banned)")
    fc.add_argument("--name", help="new display name")
    fc.add_argument("--hours", type=float, help="guest pass: allowed for this many hours only")
    fc.add_argument("-n", type=int, default=20, help="how many scans to show")
    fc.set_defaults(fn=_cmd_faces)

    vo = sub.add_parser("voice", help="test and customize the voice bank",
                        description="phrasehome voice list | say <key or text> | record <key>")
    vo.add_argument("action", choices=["list", "say", "record"], nargs="?", default="list")
    vo.add_argument("text", nargs="*")
    vo.add_argument("--seconds", type=float, default=4.0)
    vo.set_defaults(fn=_cmd_voice)

    ag = sub.add_parser("agenda", help="show everyone's reminders, alarms, timers and lists")
    ag.add_argument("--person")
    ag.set_defaults(fn=_cmd_agenda)

    fpp = sub.add_parser("fingerprint", help="enroll fingers on the door's fingerprint sensor")
    fpp.add_argument("action", choices=["enroll", "list"], nargs="?", default="list")
    fpp.add_argument("person", nargs="*")
    fpp.add_argument("--door", default="front")
    fpp.set_defaults(fn=_cmd_fingerprint)

    bk = sub.add_parser("backup", help="back up config, face bank, reminders and recordings")
    bk.add_argument("--to", help="folder to save the backup in")
    bk.set_defaults(fn=_cmd_backup)

    sa = sub.add_parser("auth-spotify", help="link your Spotify account (one time)")
    sa.add_argument("--no-browser", action="store_true")
    sa.set_defaults(fn=_cmd_auth_spotify)

    pk = sub.add_parser("package", help="track packages")
    pk.add_argument("action", choices=["add", "list", "remove"], nargs="?", default="list")
    pk.add_argument("number", nargs="?", default="")
    pk.add_argument("--name")
    pk.set_defaults(fn=_cmd_package)

    ak = sub.add_parser("ask", help="type a question to the AI assistant")
    ak.add_argument("question", nargs="*")
    ak.add_argument("--room", help="pretend you're speaking from this room")
    ak.add_argument("--speak", action="store_true", help="also say the answer out loud")
    ak.add_argument("--dry-run", action="store_true", help="don't really control anything")
    ak.set_defaults(fn=_cmd_ask)

    sp = sub.add_parser("speakers", help="show or change ESP32 speakers",
                        description="phrasehome speakers status | group [names] | bluetooth [names]"
                                    " | volume <name|all> <0-100|up|down>")
    sp.add_argument("action", choices=["status", "group", "bluetooth", "volume"], nargs="?",
                    default="status")
    sp.add_argument("names", nargs="*")
    sp.set_defaults(fn=_cmd_speakers)

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
