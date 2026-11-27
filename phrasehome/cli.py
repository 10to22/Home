from __future__ import annotations

import argparse
import json
import logging
import sys
import time
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


def _faces_test(cfg, sc, bank, eng, args) -> int:
    """Live: who does the camera think it sees, and how sure is it?"""
    import time as _t

    from .door_scanner import Camera, DoorScanner

    spec = 0 if args.webcam else sc["camera"]
    cams = cfg.cameras or {}
    if isinstance(spec, str) and spec.lower() in cams:
        spec = cams[spec.lower()].get("url", 0)
    try:
        cam = Camera(spec)
    except Exception as e:
        print(f"can't open the camera: {e}", file=sys.stderr)
        return 1

    class _H:
        config = cfg

    scanner = DoorScanner(_H, sc, engine=eng, camera=cam)
    thr = float(sc["match_threshold"])
    print(f"Look at the camera for {args.seconds:.0f} s. A match needs {thr:.2f} or more "
          f"({thr - float(sc['unsure_margin']):.2f}+ = 'not sure yet', keeps looking). Control+C to stop.")
    end, scores = _t.monotonic() + args.seconds, []
    try:
        while _t.monotonic() < end:
            img = cam.read()
            if img is None:
                continue
            faces = [f for f in eng.faces(img) if f.size >= sc["min_face_px"]]
            if not faces:
                small = eng.faces(img)
                print("  no face" + (f" big enough (yours is {max(f.size for f in small):.0f} px, needs "
                                     f"{sc['min_face_px']}: come closer)" if small else ""), flush=True)
            else:
                f = max(faces, key=lambda f: f.size)
                label, person, _, near, score = scanner.identify(eng.embed(img, f))
                verdict = {"unsure": "not sure yet", "unknown": "STRANGER"}.get(
                    label, f"MATCH {person.name}" + ("  (auto-unlock)" if getattr(person, "auto_unlock", False) else ""))
                if person is not None and person.kind == "visitor":
                    verdict = f"matches {person.id} (a saved stranger) - run: phrasehome faces delete visitors"
                print(f"  closest: {near.name if near else '-':<12} {score:.2f}  -> {verdict}", flush=True)
                if near:
                    scores.append(score)
            _t.sleep(0.5)
    except KeyboardInterrupt:
        pass
    if scores:
        avg = sum(scores) / len(scores)
        print(f"average {avg:.2f}, best {max(scores):.2f}. " + (
            "Looks good." if avg >= thr else
            "Too low: enroll more samples here, facing the camera in this light, without hats or sunglasses."))
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
                  f"{p.kind:<9} seen {p.seen}x, last {p.last_seen or '-'}, {len(p.embeddings)} samples"
                  + ("  AUTO-UNLOCK" if p.auto_unlock else ""))
        return 0
    if a == "scans":
        from .events import EventLog

        for e in EventLog(cfg.resolve_path(cfg.data_dir) / "events.jsonl").query("scan", limit=args.n):
            if e["kind"] == "scan":
                print(f"{e['time']}  {e['door']:<8} {e['result']:<8} {e['name']:<18} "
                      f"score {e.get('score', 0):.2f}  {e.get('photo') or ''}")
        return 0
    if a == "test":
        return _faces_test(cfg, sc, bank, engine(), args)
    if a == "delete" and " ".join(args.ref).lower() in ("visitors", "all visitors"):
        gone = [bank.delete(p.id) for p in list(bank.people.values()) if p.kind == "visitor"]
        print(f"deleted {len(gone)} visitor entries" + (f": {', '.join(p.id for p in gone)}" if gone else ""))
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
            if args.webcam:
                sc = {**sc, "camera": 0}
            if args.camera or args.webcam:
                from .door_scanner import DoorScanner

                from .faces import MAX_EMBEDDINGS

                print(f"Look at the camera. Taking {args.samples} samples: follow the prompts, "
                      "slowly. Stay about an arm's length away.")
                if args.samples > MAX_EMBEDDINGS:
                    print(f"  (only the newest {MAX_EMBEDDINGS} samples per person are kept)")
                tips = ["look straight at it", "turn a little to your left", "turn a little to your right",
                        "tilt your chin up", "tilt your chin down", "smile", "neutral face",
                        "step a bit closer", "step a bit back", "look straight again"]

                def progress(n, total):
                    tip = tips[(n * len(tips)) // max(total, 1)] if n < total else "done"
                    print(f"  sample {n}/{total}" + (f" - now {tip}" if n < total else ""), flush=True)

                class _H:  # DoorScanner only needs config here
                    config = cfg

                got, ph = DoorScanner(_H, sc, engine=eng).capture_embeddings(args.samples, progress=progress)
                if len(got) < args.samples:
                    print(f"  got {len(got)} of {args.samples} (timed out: is your face well lit and in view?)")
                embs += got
                photo = photo or ph
            if not embs:
                print("no face samples collected; use --camera, --webcam and/or --image "
                      "(on a Mac, allow Terminal to use the camera: System Settings > Privacy & Security > Camera)",
                      file=sys.stderr)
                return 1
            p = bank.enroll(ref, embs, allowed=not args.deny)
            if photo:
                bank.save_photo(p.id, photo)
            if args.auto_unlock and not args.deny:
                p = bank.set_auto_unlock(p.id, True)
            for v in bank.visitors_like(p, float(sc["match_threshold"])):
                bank.delete(v.id)  # the door saved them as a stranger before: that was them
                print(f"  removed {v.id}: it was {p.name}'s face, saved before they were enrolled")
            print(f"enrolled {p.name} ({p.id}) with {len(p.embeddings)} samples in total, "
                  f"{'allowed' if p.allowed else 'NOT allowed'}"
                  + (", their face unlocks the door by itself" if p.auto_unlock else ""))
        elif a == "auto-unlock":
            p = bank.set_auto_unlock(ref, not args.off)
            print(f"{p.name} ({p.id}): " + ("their face now unlocks a locked door by itself, in any mode"
                                           if p.auto_unlock else "needs the fingerprint again"))
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
    text = " ".join(args.text)
    if args.action == "match":  # doesn't need the house running
        return _voice_match(cfg, text, args)
    if args.action == "check":
        return _voice_check(cfg)
    if args.action == "tune":
        return _voice_tune(cfg, args)
    home = Dispatcher(cfg)  # noqa: F841  (speech is routed through it)
    v = Voice(cfg)
    if args.action == "serve":
        return _voice_serve(v, args)
    if args.action == "emotions":
        return _voice_emotions(v, text, args)
    if args.action == "list":
        for key in sorted(v.phrases):
            clip = v._clip(key)
            entry = v.phrases[key]
            shown = " | ".join(entry) if isinstance(entry, list) else entry
            print(f"{key:<24} {'[recorded clip] ' if clip else ''}{shown}")
    elif args.action == "say":
        if args.emotion:
            v.style.enabled = True  # hear a mood even while moods are off in config.yaml
        print(v.speak(text or "Hello. This is your house speaking.", emotion=args.emotion))
    elif args.action == "demo":
        from pathlib import Path

        save = Path(args.save).expanduser() if args.save else None
        out = v.demo(save, every_variant=not args.short)
        if out:
            print(f"\nsaved {out}  (open it: open \"{out}\")")
    elif args.action == "record":
        if not text:
            print("usage: phrasehome voice record <phrase_key> [--seconds 4]", file=sys.stderr)
            return 2
        print(f"Recording {args.seconds:.0f} seconds for '{text}' - speak now...")
        print(f"saved {v.record(text, args.seconds)}")
    return 0


def _voice_emotions(v, text: str, args) -> int:
    """Say one line in every mood, one after another (or save them all to one file)."""
    from pathlib import Path

    from .audio_out import convert
    from .voice_style import EMOTIONS

    v.style.enabled = True
    text = text or "Righto, the kitchen lights are off. Try not to trip over the dog on the way out."
    moods = [args.emotion] if args.emotion else list(EMOTIONS)
    if not args.save:
        for mood in moods:
            print(f"[{mood}] {EMOTIONS.get(mood, {}).get('about', '')}")
            v.speak(text, emotion=mood)
        return 0
    import wave

    rate, pcm = 22050, bytearray()
    for mood in moods:
        print(f"[{mood}] {EMOTIONS.get(mood, {}).get('about', '')}")
        wav = v.synthesize(text, v.base, emotion=mood)
        if wav:
            pcm += convert(wav, rate, 1) + bytes(2 * int(rate * 0.8))
    out = Path(args.save).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(pcm))
    print(f"saved {out}")
    return 0


def _voice_tune(cfg, args) -> int:
    """Show every mood's knobs, or change one mood's (saved in data/voice_tuning.json)."""
    from .voice_style import EMOTIONS, KNOBS, VoiceModeler

    m = VoiceModeler(cfg)
    mood = " ".join(args.text).strip() or args.emotion
    if args.reset:
        m.reset(mood or None)
        print(f"reset {mood or 'every mood'} to the built-in settings")
    knobs = {k: getattr(args, k, None) for k in KNOBS}
    if mood and any(x is not None for x in knobs.values()):
        try:
            m.tune(mood, **knobs)
        except KeyError as e:
            print(e.args[0], file=sys.stderr)
            return 2
    on = "ON" if m.enabled else "off (turn them on with voice: {emotion: true} in config.yaml)"
    accent = m.accent or "the voice's own"
    print(f"moods are {on}; accent: {accent}")
    print()
    print(f"{'mood':<10}" + "".join(f"{k:>13}" for k in KNOBS))
    for name in ([mood] if mood else EMOTIONS):
        k, tuned = m.knobs(name), m.tuning.get(name, {})
        print(f"{name:<10}" + "".join(f"{('*' if x in tuned else '') + format(k[x], 'g'):>13}" for x in KNOBS))
    print()
    print("* = your tuning.  Change one: phrasehome voice tune cheeky --pitch 2 --rate 1.1")
    print('Hear it:          phrasehome voice say --emotion cheeky "Lights off, mate."')
    return 0


def _voice_serve(v, args) -> int:
    """Lend this computer's voice to the house (see voice_server.py)."""
    import time

    from .voice_server import VoiceServer

    try:
        srv = VoiceServer(v, host=args.host, port=args.port, token=args.token)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    srv.start()
    engine = v.available_engine(v.base.get("engine", "piper"), v.base)
    print(f"Voice server: http://<this computer>:{srv.port}/v1/audio/speech  (engine {engine}, accent "
          f"{v.style.accent or 'default'}, moods {'on' if v.style.enabled else 'off'})")
    if not srv.token:
        print("No token set: anyone on your network can use it. Set PHRASEHOME_VOICE_TOKEN (here and on the Pi).")
    print('On the Pi:  voice: {engine: http, http: {url: "http://<this computer>:%d/v1/audio/speech"}}' % srv.port)
    print("Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
    return 0


def _voice_check(cfg) -> int:
    """What voice will the house actually use here, and does it work?"""
    import shutil

    from . import voice as vmod
    from .dispatcher import Dispatcher

    raw = cfg.voice
    print("Your config's voice section:")
    for k in ("engine", "accent", "emotion", "say_voice", "espeak_voice", "piper_model", "style", "effect",
              "speed"):
        if k in raw:
            note = "  <- has curly quotes from TextEdit (fixed automatically)" \
                if isinstance(raw[k], str) and any(q in raw[k] for q in "\u201c\u201d\u2018\u2019") else ""
            print(f"  {k}: {raw[k]!r}{note}")
    home = Dispatcher(cfg)
    v = home.voice
    engine = v.available_engine(v.base["engine"], v.base)
    print(f"\nComputer: {sys.platform}")
    print(f"Engine used here: {engine}" + (f" (config says {v.base['engine']})" if engine != v.base["engine"] else ""))
    problems = 0
    if engine == "say":
        wanted = v.base.get("say_voice")
        got = vmod.mac_voice(wanted)
        print(f"Mac voice: {got or 'the default voice'}" + (f" (you asked for {wanted!r})" if wanted and got != wanted else ""))
        if wanted and not got:
            problems += 1
            print("  PROBLEM: that voice isn't installed. Installed voices:", ", ".join(vmod.mac_voices()))
    elif engine == "piper":
        model = cfg.resolve_path(v.base["piper_model"])
        print(f"Piper model: {model} ({'found' if model.exists() else 'MISSING'})")
        problems += 0 if model.exists() else 1
    elif engine == "espeak":
        print(f"eSpeak voice: {v.base['espeak_voice']} ({'installed' if shutil.which('espeak-ng') else 'espeak-ng NOT installed'})")
        problems += 0 if shutil.which("espeak-ng") else 1
    elif engine in ("melo", "chatterbox", "http"):
        import importlib.util

        need = {"melo": "melo", "chatterbox": "chatterbox"}.get(engine)
        if need and importlib.util.find_spec(need) is None:
            problems += 1
            print(f"  PROBLEM: engine {engine} isn't installed here (see the Voice section of the README)")
        if engine == "http":
            print(f"Voice server: {(v.base.get('http') or {}).get('url') or 'MISSING: voice.http.url'}")
        if engine == "chatterbox":
            from .tts_neural import chatterbox_sample

            smp = chatterbox_sample(cfg, v.base.get("chatterbox") or {})
            print(f"Voice sample: {smp or 'none (Chatterbox speaks in its own voice)'}")
    st = v.style
    print(f"Accent: {st.accent or 'the voice itself'}   Moods: {'on' if st.enabled else 'off'}")
    style = raw.get("style", vmod.DEFAULT_STYLE)
    print(f"Style: {style}" + ("" if style in vmod.STYLES or style == "plain" else "  PROBLEM: unknown style, using the plain lines"))
    if v.base.get("effect"):
        ok = bool(shutil.which("ffmpeg"))
        print(f"Effect: {v.base['effect']} ({'ok' if ok else 'needs ffmpeg: brew install ffmpeg'})")
    text, _ = v.resolve("door_auto_locked", door="front")
    print(f"\nTest line: {text}")
    try:
        wav = v.synthesize(text, v.base)
        print(f"Made the audio: {wav}")
    except Exception as e:
        print(f"PROBLEM making the audio: {e}")
        return 1
    try:
        where = v.router.play(wav, None, float(v.base.get("volume", 100)) / 100)
        print(f"Played it ({where}). Did you hear it? If not, check the Mac's volume and output device.")
    except Exception as e:
        print(f"PROBLEM playing it: {e}")
        return 1
    return 1 if problems else 0


def _voice_match(cfg, path: str, args) -> int:
    from pathlib import Path

    from .voice_match import match

    if not path or not Path(path).expanduser().exists():
        print("usage: phrasehome voice match <recording.mp3|.wav|...>", file=sys.stderr)
        return 2
    print("Comparing every voice on this computer with that recording (takes a minute or two)...")
    try:
        ref, best = match(cfg, Path(path).expanduser(), top=args.top, effects=not args.no_effects,
                          progress=lambda m: print(m, flush=True), novelty=args.novelty)
    except Exception as e:
        print(f"couldn't compare: {e}", file=sys.stderr)
        return 1
    print(f"\nThe recording: {ref.describe()}\n")
    print(f"{'#':>2}  {'closeness':>9}  voice")
    for i, (dist, label, effect, settings, p) in enumerate(best, 1):
        close = max(0, 100 - 20 * dist)
        print(f"{i:>2}  {close:>8.0f}%  {label}" + (f" + effect {effect}" if effect else ""))
        print(f"{'':>15}{p.describe()}")
    if best:
        _, label, effect, settings, _ = best[0]
        print("\nTo use the closest one, put this under `voice:` in config.yaml:")
        for k, v in settings.items():
            print(f"  {k}: {v}")
        print("\nHear it:  phrasehome voice say \"Hello. This is your house speaking.\"")
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
                if not args.quiet:
                    home.voice.speak(answer, room=args.room)  # in the house's voice
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


def _cmd_ir(args) -> int:
    """Learn a button from a remote pointed at a device node's IR receiver, or list codes."""
    import json as _json
    import time as _time

    import requests

    from .actions.esp32 import Esp32Backend

    cfg = load_config(args.config)
    be_name = next((n for n, b in cfg.backends.items() if b["type"] == "esp32"), None)
    if not be_name:
        print("no esp32 backend in the config", file=sys.stderr)
        return 2
    be = Esp32Backend(be_name, cfg.backends[be_name], cfg)
    if args.action == "list":
        for n, c in be.ir_codes().items():
            print(f"{n:<20} {c['protocol']:<10} {c['code']}  ({c.get('bits', '?')} bits)")
        return 0
    if not (args.name and args.node):
        print("usage: phrasehome ir learn <name> --node <device node>", file=sys.stderr)
        return 2
    (_, host), = be._hosts(args.node)
    before = requests.get(f"http://{host}/api/ir/learn", timeout=5).json().get("seq", 0)
    print(f"Point the remote at the {args.node} receiver and press the button for '{args.name}'...")
    end = _time.monotonic() + 20
    while _time.monotonic() < end:
        got = requests.get(f"http://{host}/api/ir/learn", timeout=5).json()
        if got.get("seq", 0) != before:
            path = cfg.resolve_path(cfg.data_dir) / "ir_codes.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            codes = _json.loads(path.read_text()) if path.exists() else {}
            codes[args.name] = {k: got[k] for k in ("protocol", "code", "bits")}
            path.write_text(_json.dumps(codes, indent=1))
            print(f"learned {args.name}: {got['protocol']} {got['code']}. Use it: {{ node: {args.node}, ir: {args.name} }}")
            return 0
        _time.sleep(0.4)
    print("nothing received; try again closer to the receiver", file=sys.stderr)
    return 1


def _cmd_voice_id(args) -> int:
    from .dispatcher import Dispatcher
    from .voiceid import VoiceID

    home = Dispatcher(load_config(args.config), dry_run=True)
    try:
        v = VoiceID(home)
        if args.action == "list":
            for p, vs in v.prints.items():
                print(f"{p:<16} {len(vs)} samples")
            return 0
        if args.action == "record":  # say a few sentences into this computer's microphone
            import tempfile
            import wave

            import sounddevice as sd

            lines = ["The kitchen lights are on and the coffee is ready.",
                     "Remind me to take the bins out on Tuesday night.",
                     "What's the weather going to be like tomorrow morning?",
                     "Turn the living room lamp down to thirty percent.",
                     "I'm heading out now, lock up behind me please.",
                     "Set a timer for twelve minutes for the pasta."]
            vecs = []
            for i, line in enumerate(lines, 1):
                print(f"[{i}/{len(lines)}] Say: \"{line}\"  (recording in 1 s, for 4 s)")
                time.sleep(1)
                data = sd.rec(4 * 16000, samplerate=16000, channels=1, dtype="int16")
                sd.wait()
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    with wave.open(f.name, "wb") as w:
                        w.setnchannels(1)
                        w.setsampwidth(2)
                        w.setframerate(16000)
                        w.writeframes(data.tobytes())
                    try:
                        vecs.append(v.vector_from_wav(f.name))
                    except ValueError as e:
                        print(f"  skipped: {e}")
            print(f"{args.person}: {v.enroll(args.person, vecs)} samples")
            return 0
        vecs = [v.vector_from_wav(f) for f in args.files]
        print(f"{args.person}: {v.enroll(args.person, vecs)} samples")
    finally:
        home.close()
    return 0


def _cmd_simulate(args) -> int:
    """A virtual house: your config with simulated ESP32s, on this computer."""
    from .simulator import run

    run(load_config(args.config), mic=args.mic, base_port=args.port, real_backends=args.real_backends,
        webcam=args.webcam, room=args.room, camera=args.camera)
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


def load_env(*folders) -> list[str]:
    """Read KEY=value lines from .env (in the config's folder and the current folder) into the
    environment, like the Pi's service file does. Values already set win. Returns the keys set."""
    import os
    from pathlib import Path

    done, seen = [], set()
    for folder in folders:
        f = Path(folder) / ".env"
        try:
            real = f.resolve()
            if real in seen or not f.is_file():
                continue
            seen.add(real)
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            val = val.split(" #", 1)[0].strip().strip("\u201c\u201d\u2018\u2019\"'").strip()
            if key and val and key not in os.environ:
                os.environ[key] = val
                done.append(key)
    return done


def _ai_check_local(a: dict) -> int:
    from .local_ai import LocalAIError, LocalClient

    c = LocalClient(a.get("local") or {})
    print(f"AI: local (Ollama at {c.url})\nModel: {c.model}" + (f"   camera model: {c.vision_model}" if c.vision_model else ""))
    try:
        have = c.installed_models()
    except LocalAIError as e:
        print(f"PROBLEM: {e}\nInstall Ollama from ollama.com (or: brew install ollama), open it, then run this again.")
        return 1
    print("Downloaded models:", ", ".join(have) or "none")
    missing = [m for m in (c.model, c.vision_model) if m and m not in have and f"{m}:latest" not in have]
    for m in missing:
        print(f"PROBLEM: {m} isn't downloaded. Run:  ollama pull {m}")
    if c.model in missing:
        return 1
    print("Asking it something (the first answer can take a while as the model loads)...")
    try:
        resp = c.messages.create(messages=[{"role": "user", "content": "Reply with just the word: linked"}],
                                 max_tokens=50)
    except LocalAIError as e:
        print(f"PROBLEM: {e}")
        return 1
    text = " ".join(b.text for b in resp.content if b.type == "text").strip()
    print(f"{c.model} says: {text!r}\nThe local AI is linked: free and private. 'house, ...' questions, meal planning, "
          "the diary and the watcher use it" + (", and the cameras use " + c.vision_model if c.vision_model else
                                                ". Add a vision_model for camera questions") + ".")
    print("It can't search the web, and it picks the right house tool less reliably than Claude.")
    return 1 if missing else 0


MODELS = {
    "speech": ("vosk-model-small-en-us-0.15", "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
               "speech recognition (40 MB)"),
    "voice-id": ("vosk-model-spk-0.4", "https://alphacephei.com/vosk/models/vosk-model-spk-0.4.zip",
                 "telling voices apart (13 MB)"),
    "big": ("vosk-model-en-us-0.22-lgraph", "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip",
            "more accurate speech recognition (128 MB)"),
}


def _download_zip(url: str, models: "Path", name: str) -> None:
    import shutil
    import tempfile
    import zipfile
    from pathlib import Path

    import requests

    with tempfile.TemporaryDirectory() as tmp:
        z = Path(tmp) / "m.zip"
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total, got = int(r.headers.get("content-length") or 0), 0
            with z.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        print(f"\r  {got * 100 // total}%", end="", flush=True)
        print()
        out = Path(tmp) / "x"
        with zipfile.ZipFile(z) as zf:
            for m in zf.namelist():  # nothing may land outside the folder
                if not (out / m).resolve().is_relative_to(out.resolve()):
                    raise SystemExit(f"refusing a strange file in the download: {m}")
            zf.extractall(out)
        src = out / name if (out / name).is_dir() else next(p for p in out.iterdir() if p.is_dir())
        shutil.move(str(src), str(models / name))


def _cmd_download_models(args) -> int:
    """Download the offline models a laptop needs (the Pi's install.sh does this for you)."""
    from pathlib import Path

    cfg = load_config(args.config)
    models = cfg.resolve_path("models")
    Path(models).mkdir(parents=True, exist_ok=True)
    want = ["speech"] + (["voice-id"] if args.voice_id else []) + (["big"] if args.big else [])
    for key in want:
        name, url, what = MODELS[key]
        if (Path(models) / name).is_dir():
            print(f"{name}: already there")
            continue
        print(f"Downloading {name}, for {what}...")
        try:
            _download_zip(url, Path(models), name)
        except Exception as e:
            print(f"couldn't download {name}: {e}\nDownload {url} yourself and unzip it into {models}/", file=sys.stderr)
            return 1
        print(f"{name}: done")
    if args.big:
        print(f"To use the bigger model, set this under settings: in config.yaml:\n"
              f"  model_path: models/{MODELS['big'][0]}")
    if args.whisper:
        from .hearing import Whisper

        name = cfg.settings.whisper_model or ""
        print("Getting Whisper ready (downloads the model once)...")
        try:
            w = Whisper(name)
        except Exception as e:
            print(f"couldn't get Whisper: {e}", file=sys.stderr)
            return 1
        print(f"Whisper {w.name}: ready. Turn it on under settings: in config.yaml:\n  recognizer: whisper")
    print("Ready. Try:  .venv/bin/phrasehome simulate --mic")
    return 0


def _cmd_speech_test(args) -> int:
    from . import speechtest

    cfg = load_config(args.config)
    if getattr(args, "files", ""):
        speechtest.import_files(cfg, args.files)
    elif args.record or args.tts:
        items = speechtest.choose_phrases(cfg, args.n)
        if args.record:
            print(f"Say each phrase normally, from where you'd usually stand. {len(items)} phrases.")
            out = speechtest.record(cfg, items, args.seconds)
        else:
            out = speechtest.synthesize(cfg, items)
        print(f"\nSaved {len(items)} clips in {out}. Scoring them now...")
    speechtest.score(cfg)
    print("\nChange something (whisper, a bigger model, noise_cleanup, teach words on the History page), "
          "then run `phrasehome speech-test` again to compare.")
    return 0


def _cmd_family_test(args) -> int:
    from . import family_test, speechtest

    cfg = load_config(args.config)
    if args.record:
        if not args.person:
            print("usage: phrasehome family-test --record --person <name> [-n 50]", file=sys.stderr)
            return 2
        items = speechtest.choose_phrases(cfg, args.n)
        print(f"{args.person}: say each phrase normally. {len(items)} phrases.")
        speechtest.record(cfg, items, args.seconds, person=args.person)
    family_test.score_everyone(cfg)
    return 0


def _cmd_ai_usage(args) -> int:
    import types

    from .ai_plus import Meter

    cfg = load_config(args.config)
    s = Meter(types.SimpleNamespace(config=cfg)).summary(args.days)
    print(f"Last {s['days']} days: {s['calls']} AI calls, ${s['cost']:.2f}"
          f" (today: {s['today'].get('calls', 0)} calls, ${s['today'].get('cost', 0.0):.2f})")
    for group in ("person", "room", "model"):
        rows = sorted(s[group].items(), key=lambda kv: -kv[1]["cost"])
        if rows:
            print(f"\nBy {group}:")
            for name, b in rows:
                print(f"  {name:<20} {b['calls']:>5} calls  ${b['cost']:<8.4f} {b['avg_seconds']} s each")
    return 0


def _cmd_ai_check(args) -> int:
    """Is the AI linked? Sends one tiny question and explains any problem."""
    import os

    cfg = load_config(args.config)
    a = cfg.assistant
    from .local_ai import is_local

    if is_local(a):
        return _ai_check_local(a)
    model = a.get("model", "claude-opus-5-5")
    print(f"AI section in config: {'yes' if a else 'NO: add an assistant: section'}")
    print(f"Model: {model}   effort: {a.get('effort', 'low')}   persona: {a.get('persona', 'mean')}")
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        print(f"API key: found ({key[:7]}...{key[-4:]})")
    else:
        print("API key: not in .env or the environment (trying a saved `ant auth login` profile instead)")
    try:
        import anthropic
    except ImportError:
        print("PROBLEM: the AI package isn't installed. Run:  .venv/bin/pip install -e \".[ai]\"")
        return 1
    try:
        client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model=model, max_tokens=200, output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            messages=[{"role": "user", "content": "Reply with just the word: linked"}])
    except anthropic.AuthenticationError:
        print("PROBLEM: the key was refused. Copy it again from console.anthropic.com > API keys into .env")
        return 1
    except anthropic.PermissionDeniedError as e:
        print(f"PROBLEM: this key isn't allowed to use {model}: {e.message}")
        return 1
    except anthropic.NotFoundError:
        print(f"PROBLEM: model {model!r} wasn't found. Use model: claude-opus-5-5 under assistant:")
        return 1
    except anthropic.RateLimitError:
        print("PROBLEM: rate limited. Wait a minute and try again.")
        return 1
    except anthropic.BadRequestError as e:
        msg = e.message or ""
        if "credit" in msg.lower() or "billing" in msg.lower():
            print("PROBLEM: no credit on the account. Add some at console.anthropic.com > Billing.")
        else:
            print(f"PROBLEM: the request was rejected: {msg}")
        return 1
    except anthropic.APIStatusError as e:
        print(f"PROBLEM: the API answered {e.status_code}: {e.message}. Try again in a bit.")
        return 1
    except anthropic.APIConnectionError:
        print("PROBLEM: couldn't reach the API. Check the internet connection.")
        return 1
    except Exception as e:  # no credentials at all, etc.
        print(f"PROBLEM: {e}\nPut ANTHROPIC_API_KEY=your-key in the .env file in this folder.")
        return 1
    if resp.stop_reason == "refusal":
        print("The AI answered with a refusal, but the link works.")
        return 0
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    print(f"Claude says: {text!r}\nThe AI is linked. Everything AI will work: 'house, ...' questions, the morning "
          "briefing, meal planning, the diary, the watcher, and the cameras' descriptions.")
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
                                    "allow ID [--name N] | deny ID | rename ID --name N | delete ID | "
                                    "auto-unlock ID [--off] | test [--webcam] | delete visitors")
    fc.add_argument("action", choices=["list", "scans", "enroll", "allow", "deny", "rename", "delete",
                                       "auto-unlock", "test"],
                    nargs="?", default="list")
    fc.add_argument("ref", nargs="*", help="name or id")
    fc.add_argument("--camera", action="store_true", help="enroll from the door camera")
    fc.add_argument("--webcam", action="store_true", help="enroll from this computer's webcam")
    fc.add_argument("--image", nargs="+", help="enroll from photo files")
    fc.add_argument("--samples", type=int, default=20,
                    help="how many face samples to take (default 20; run enroll again to add more)")
    fc.add_argument("--deny", action="store_true", help="enroll as NOT allowed (e.g. banned)")
    fc.add_argument("--auto-unlock", action="store_true",
                    help="enroll: this face unlocks the door by itself (no fingerprint)")
    fc.add_argument("--off", action="store_true", help="auto-unlock: turn it off for this person")
    fc.add_argument("--seconds", type=float, default=20, help="test: how long to watch")
    fc.add_argument("--name", help="new display name")
    fc.add_argument("--hours", type=float, help="guest pass: allowed for this many hours only")
    fc.add_argument("-n", type=int, default=20, help="how many scans to show")
    fc.set_defaults(fn=_cmd_faces)

    vo = sub.add_parser("voice", help="test and customize the voice bank",
                        description="phrasehome voice list | say <key or text> [--emotion cheeky] | record <key> | "
                                    "match <recording> | demo [--save demo.mp3] [--short] | check | "
                                    "emotions [text] [--save moods.wav] | tune [mood] [--pitch 2 ...] [--reset] | "
                                    "serve [--port 8810]")
    vo.add_argument("action", choices=["list", "say", "record", "match", "demo", "check", "emotions", "tune",
                                       "serve"], nargs="?", default="list")
    vo.add_argument("text", nargs="*")
    vo.add_argument("--seconds", type=float, default=4.0)
    vo.add_argument("--top", type=int, default=8, help="match: how many voices to show")
    vo.add_argument("--no-effects", action="store_true", help="match: don't try the voice effects")
    vo.add_argument("--save", help="demo: write everything to one audio file (.wav or .mp3) instead of playing it")
    vo.add_argument("--short", action="store_true", help="demo: one version of each line, not every variant")
    vo.add_argument("--novelty", action="store_true", help="match: include the Mac's joke voices (Bahh, Bubbles...)")
    vo.add_argument("--emotion", help="say/emotions/tune: a mood (neutral, calm, clear, cheeky, annoyed, cheerful, "
                                      "urgent, soft)")
    for knob, what in (("rate", "speed, x normal (0.6-1.6)"), ("pitch", "semitones (-6..6)"),
                       ("pause", "gaps, x normal (0.3-3)"), ("lively", "pitch movement (0.2-2)"),
                       ("volume", "dB louder/quieter (-12..12)"), ("exaggeration", "Chatterbox acting (0.25-2)")):
        vo.add_argument(f"--{knob}", type=float, help=f"tune: {what}")
    vo.add_argument("--reset", action="store_true", help="tune: back to the built-in settings")
    vo.add_argument("--port", type=int, default=8810, help="serve: port")
    vo.add_argument("--host", default="0.0.0.0", help="serve: address to listen on")
    vo.add_argument("--token", help="serve: required token (default: PHRASEHOME_VOICE_TOKEN)")
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

    ir = sub.add_parser("ir", help="learn IR remote buttons for the IR blaster")
    ir.add_argument("action", choices=["learn", "list"], nargs="?", default="list")
    ir.add_argument("name", nargs="?")
    ir.add_argument("--node")
    ir.set_defaults(fn=_cmd_ir)

    vi = sub.add_parser("voice-id", help="teach the house who's who by voice (from recordings)")
    vi.add_argument("action", choices=["enroll", "list", "record"], nargs="?", default="list")
    vi.add_argument("person", nargs="?")
    vi.add_argument("files", nargs="*", help="WAV recordings (3+ seconds each)")
    vi.set_defaults(fn=_cmd_voice_id)

    sm = sub.add_parser("simulate", help="run your config as a virtual house with simulated devices")
    sm.add_argument("--mic", action="store_true", help="also listen on this computer's microphone")
    sm.add_argument("--webcam", action="store_true", help="use this computer's webcam as the door camera")
    sm.add_argument("--room", help="the room the laptop's microphone starts in (move it on the dashboard)")
    sm.add_argument("--camera", help="the camera the webcam starts as, e.g. \"front door\" (move it on the dashboard)")
    sm.add_argument("--port", type=int, default=9100, help="first port for simulated nodes")
    sm.add_argument("--real-backends", action="store_true",
                    help="really use Google, phone alerts, speakers... (off by default)")
    sm.set_defaults(fn=_cmd_simulate)

    ak = sub.add_parser("ask", help="type a question to the AI assistant")
    ak.add_argument("question", nargs="*")
    ak.add_argument("--room", help="pretend you're speaking from this room")
    ak.add_argument("--quiet", action="store_true", help="only print the answer, don't say it out loud")
    ak.add_argument("--speak", action="store_true", help=argparse.SUPPRESS)  # speaking is the default now
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

    dm = sub.add_parser("download-models", help="download the offline speech model (for a laptop)")
    dm.add_argument("--voice-id", action="store_true", help="also the model for recognizing who's talking")
    dm.add_argument("--big", action="store_true", help="also a bigger, more accurate speech model (128 MB)")
    dm.add_argument("--whisper", action="store_true", help="also Whisper, the most accurate (needs .[whisper])")
    dm.set_defaults(fn=_cmd_download_models)

    ft = sub.add_parser("family-test", help="how well the house hears each person (record per person, then score)")
    ft.add_argument("--record", action="store_true", help="record this person saying a set of commands first")
    ft.add_argument("--person", help="whose voice (with --record)")
    ft.add_argument("-n", type=int, default=50, help="how many phrases")
    ft.add_argument("--seconds", type=float, default=3.5)
    ft.set_defaults(fn=_cmd_family_test)

    au = sub.add_parser("ai-usage", help="what the AI has cost and how fast it's been, per person, room and model")
    au.add_argument("--days", type=int, default=31)
    au.set_defaults(fn=_cmd_ai_usage)

    sc = sub.add_parser("scenario", help="play a whole evening against your config in seconds (dry run)")
    sc.add_argument("action", choices=["list", "run"], nargs="?", default="list")
    sc.add_argument("name", nargs="?", default="", help="a scenario name, or all")
    sc.set_defaults(fn=lambda a: __import__("phrasehome.scenarios", fromlist=["main"]).main(a))

    cp = sub.add_parser("companion", help="the laptop companion: runs in the background and helps when needed")
    cp.add_argument("--person", default="", help="whose laptop this is (their instance name)")
    cp.add_argument("--hub", default="http://localhost:8080", help="the house's dashboard address")
    cp.add_argument("--token", default="", help="the dashboard token, if it has one (or PHRASEHOME_TOKEN)")
    cp.add_argument("--every", type=float, default=5.0, help="seconds between checks")
    cp.add_argument("--auto-help", action="store_true", help="explain an error that's stuck on screen, unasked")
    cp.add_argument("--speak-here", action="store_true", help="also say answers on this laptop")
    cp.add_argument("--install", action="store_true", help="start it at every login")
    cp.add_argument("--uninstall", action="store_true", help="stop starting it at login")
    cp.set_defaults(fn=lambda a: __import__("phrasehome.companion", fromlist=["main"]).main(a))

    st = sub.add_parser("speech-test", help="measure how well the house hears you (record, then score)")
    st.add_argument("--record", action="store_true", help="say a set of phrases into the microphone first")
    st.add_argument("--tts", action="store_true", help="have the house's voice say them instead (quick)")
    st.add_argument("-n", type=int, default=15, help="how many phrases")
    st.add_argument("--seconds", type=float, default=3.5, help="recording length per phrase")
    st.add_argument("--files", default="", help="a folder of your own recordings (mp3, m4a, wav): file name = what was said")
    st.set_defaults(fn=_cmd_speech_test)

    th = sub.add_parser("train-hearing", help="teach it your words: many voices say your phrases, it learns what it mishears")
    th.add_argument("--phrases", type=int, default=300, help="how many phrases (every command gets at least one)")
    th.add_argument("--per-phrase", type=int, default=2, help="how many voices say each phrase")
    th.add_argument("--voices", nargs="*", help="folders of Piper voices (.onnx), as well as this computer's own")
    th.add_argument("--speakers", type=int, default=6, help="voices to use from each multi-speaker Piper model")
    th.add_argument("--noise", default="", help="background audio to mix in (mp3/wav: music, a party, the TV)")
    th.add_argument("--background", nargs="*", help="long real recordings that should run nothing (music, TV, talk)")
    th.add_argument("--dry", action="store_true", help="measure only; learn nothing")
    th.add_argument("--keep", action="store_true", help="keep the mp3 clips (data/train-hearing/clips)")
    th.add_argument("--rescore", action="store_true", help="measure the kept clips again (after changing something)")
    th.set_defaults(fn=lambda a: __import__("phrasehome.hearing_train", fromlist=["main"]).main(a))

    ac = sub.add_parser("ai-check", help="check that the AI (Claude) is linked: key, model, a test question")
    ac.set_defaults(fn=_cmd_ai_check)

    args = p.parse_args(argv)
    from pathlib import Path as _P

    load_env(_P(args.config).expanduser().resolve().parent, _P.cwd())  # secrets in .env, like on the Pi
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
    )
    try:
        return args.fn(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
