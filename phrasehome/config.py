"""Load and validate the YAML config."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    pass


@dataclass
class Settings:
    model_path: str = "models/vosk-model-small-en-us-0.15"
    sample_rate: int = 16000
    device: Any = None  # sounddevice input device index or name; None = default mic
    # "utterance": the whole thing you say must closely match a phrase (fewest false triggers).
    # "contains": the phrase may appear inside a longer sentence.
    match_mode: str = "utterance"
    match_threshold: float = 85.0  # 0-100 fuzzy similarity needed to fire
    min_confidence: float = 0.6  # average Vosk word confidence needed to fire
    cooldown_seconds: float = 3.0  # same command will not refire within this window
    # Only let Vosk hear the words used in your phrases. More accurate, much lighter on a Pi.
    restrict_vocabulary: bool = True
    # If any of these appear in what was heard (and not in the phrase itself), don't fire.
    block_words: list[str] = field(
        default_factory=lambda: ["don't", "dont", "do not", "not", "never", "no"]
    )
    # When several mics hear the same command at once, wait this long and keep only the
    # clearest one, so "lights on" in the kitchen doesn't also fire in the next room.
    arbitration_seconds: float = 0.6
    default_backend: str | None = None
    language: str = "en"  # the main language; other rooms can use others (see `languages:`)
    log_heard: bool = False  # log everything heard (handy for tuning; off for privacy)
    # "vosk": fast, light (the Pi's default). "whisper": far more accurate (pip install faster-whisper):
    # sentences are cut out of the audio, cleaned up and written down by Whisper (see hearing.py).
    recognizer: str = "vosk"
    whisper_model: str = ""  # default: small.en on a Mac, base.en elsewhere
    noise_cleanup: bool = True  # remove rumble and steady background noise, even out the volume
    vad_silence: float = 0.6  # seconds of quiet that end a sentence (whisper)
    confirm_commands: list[str] = field(default_factory=list)  # also ask "did you say ...?" for these
    did_you_mean: bool = True  # near misses: let the AI pick the command you most likely meant
    # the word bank (wordbank.py): each phrase also matches its usual other wordings ("switch on the
    # lights", "lights on", "put the lights on"), English only
    phrase_variants: bool = True
    # "lock the front door and turn off the lights": run both (every part must be a command)
    multi_commands: bool = True


# Built-in backends, picked automatically from the action's keys:
#   {speak: retinal_scan_complete}      -> voice bank (rooms: [..] / everywhere: true)
#   {door: front, state: locked}        -> doors
#   {routine: lockdown}                 -> routines
#   {timer: set, minutes: 10}           -> timers        {alarm: stop} / {alarm: snooze}
#   {mode: away}                        -> home mode     {presence: away}
#   {ask: "brief me on today"}          -> AI assistant, answer spoken
#   {privacy: on, minutes: 60}          -> pause the mics {dnd: on} -> do not disturb
#   {if: {mode: away}, then: [...], else: [...]}
BUILTIN_BACKENDS = {"speak": "voice", "door": "doors", "routine": "routine", "timer": "timer",
                    "alarm": "alarm", "mode": "mode", "presence": "presence", "ask": "assistant",
                    "privacy": "privacy", "dnd": "dnd", "if": "logic", "report": "report",
                    "security": "security", "plug": "energy", "spotify": "spotify",
                    "pet": "pets", "voiceid": "voiceid", "freak": "freak", "hush": "voice"}
RESERVED = set(BUILTIN_BACKENDS.values())


@dataclass
class Command:
    name: str
    phrases: list[str]               # in the main language (settings.language)
    action: dict[str, Any]
    by_language: dict[str, list[str]] = field(default_factory=dict)  # {"es": [...], ...}

    def phrases_for(self, lang: str | None) -> list[str]:
        if lang and lang in self.by_language:
            return self.by_language[lang]
        return self.phrases


@dataclass
class Config:
    settings: Settings
    backends: dict[str, dict[str, Any]]
    slots: dict[str, dict[str, str]]  # slot name -> {spoken form: value}
    commands: list[Command]
    base_dir: Path
    sources: dict[str, Any] = field(default_factory=dict)
    routines: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    doors: dict[str, dict[str, Any]] = field(default_factory=dict)
    voice: dict[str, Any] = field(default_factory=dict)
    door_scanners: list[dict[str, Any]] = field(default_factory=list)
    assistant: dict[str, Any] = field(default_factory=dict)
    data_dir: str = "data"
    instances: list = field(default_factory=list)
    shared_lists: list[str] = field(default_factory=list)
    location: dict[str, float] = field(default_factory=dict)
    schedules: list[dict[str, Any]] = field(default_factory=list)
    automations: list[dict[str, Any]] = field(default_factory=list)
    vacation: dict[str, Any] = field(default_factory=dict)
    fingerprint: list[dict[str, Any]] = field(default_factory=list)
    web: dict[str, Any] = field(default_factory=dict)
    presence: dict[str, Any] = field(default_factory=dict)
    sensors: dict[str, Any] = field(default_factory=dict)
    cameras: dict[str, Any] = field(default_factory=dict)
    camera_settings: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)  # weather, alarm, energy, meals, spotify...
    source_name: str = ""

    def resolve_path(self, p: str) -> Path:
        path = Path(p).expanduser()
        return path if path.is_absolute() else self.base_dir / path


def _normalize_slots(raw: dict[str, Any]) -> dict[str, dict[str, str]]:
    from .clock import number_words

    slots: dict[str, dict[str, str]] = {}
    for name, values in (raw or {}).items():
        if isinstance(values, dict) and set(values) == {"numbers"}:
            # {numbers: [1, 120]} -> "one": "1", ... "one hundred twenty": "120"
            lo, hi = (int(x) for x in values["numbers"])
            slots[name] = {number_words(n): str(n) for n in range(lo, hi + 1)}
            if lo <= 1 <= hi:
                slots[name]["a"] = "1"  # "a minute"
        elif isinstance(values, list):
            slots[name] = {str(v): str(v) for v in values}
        elif isinstance(values, dict):
            slots[name] = {str(k): str(v) for k, v in values.items()}
        else:
            raise ConfigError(f"slot '{name}' must be a list or a mapping")
    return slots


# Commands for built-in features, so they work without editing config.yaml. Skipped when your
# config already has a command with the same name or the same action. `settings:
# builtin_commands: false` turns them all off.
HUSH_PHRASES = ["shut up", "shut it", "shut up house", "be quiet", "quiet", "stop talking", "shush", "hush",
                "enough", "silence", "zip it", "pipe down", "stop it", "cut it out", "okay stop", "nobody asked",
                "shut your mouth", "give it a rest"]
BUILTIN_COMMANDS = [
    ("freak_on", ["freak mode", "freak mode on", "we're getting freaky"], {"backend": "freak", "freak": "on"}),
    ("freak_off", ["freak mode off", "end freak mode", "freak mode over"], {"backend": "freak", "freak": "off"}),
    ("hush", HUSH_PHRASES, {"backend": "voice", "hush": True}),  # also heard while the house is talking
    # only if the config has a fire_mode routine. Also puts "fire" in the recognizer's vocabulary,
    # so it isn't heard as "five".
    ("fire", ["fire", "there's a fire", "fire fire", "fire fire fire", "house fire", "something's on fire",
              "help fire"], {"backend": "routine", "routine": "fire_mode"}),
]
NEEDS_ROUTINE = {"fire": "fire_mode"}


def _is_on(v: Any) -> bool:
    return str(v).lower() in ("on", "true", "start", "yes")


def _identity(action: dict) -> tuple | None:
    """What a command does, to spot a config command that already covers a built-in one."""
    if action.get("hush"):
        return ("hush",)
    if action.get("routine"):
        return ("routine", str(action["routine"]))
    if action.get("backend") == "freak" or "freak" in action:
        return ("freak", _is_on(action.get("freak")))
    return None


def missing_builtin_commands(commands: list["Command"], routines=()) -> list["Command"]:
    have_names = {c.name for c in commands}
    have = {_identity(c.action) for c in commands} - {None}
    return [Command(name=name, phrases=list(phrases), action=dict(action))
            for name, phrases, action in BUILTIN_COMMANDS
            if name not in have_names and _identity(action) not in have
            and (name not in NEEDS_ROUTINE or NEEDS_ROUTINE[name] in routines)]


def normalize_action(action: dict[str, Any], default_backend: str | None,
                     backends: dict[str, Any], where: str) -> dict[str, Any]:
    """Fill in `backend` and check it exists. Raises ConfigError with `where` for context."""
    action = dict(action or {})
    if "wait" in action and len(action) == 1:
        return action  # routine pause step
    if "if" in action:  # conditional step: normalize both branches
        for branch in ("then", "else"):
            raw = action.get(branch) or []
            raw = [raw] if isinstance(raw, dict) else raw
            action[branch] = [normalize_action(a, default_backend, backends, f"{where} {branch}")
                              for a in raw]
    if not action.get("backend"):
        builtin = next((b for k, b in BUILTIN_BACKENDS.items() if k in action), None)
        action["backend"] = builtin or default_backend
    if not action["backend"]:
        raise ConfigError(f"{where} has no backend and no default_backend is set")
    if action["backend"] not in backends and action["backend"] not in RESERVED:
        raise ConfigError(f"{where} uses unknown backend '{action['backend']}'")
    return action


EXTRA_SECTIONS = ("weather", "alarm", "energy", "packages", "meals", "spotify", "commute", "rooms",
                  "tags", "beds", "pets", "voice_id", "languages", "diary", "watcher", "floorplan",
                  "follow_lights", "smoke", "wake", "freak", "history")


def load_config(path: str | Path) -> Config:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}

    known = set(Settings.__dataclass_fields__)
    raw_settings = data.get("settings") or {}
    unknown = set(raw_settings) - known
    if unknown:
        raise ConfigError(f"unknown settings: {', '.join(sorted(unknown))}")
    settings = Settings(**raw_settings)
    if settings.match_mode not in ("utterance", "contains"):
        raise ConfigError("settings.match_mode must be 'utterance' or 'contains'")

    backends = data.get("backends") or {}
    for name, b in backends.items():
        if not isinstance(b, dict) or "type" not in b:
            raise ConfigError(f"backend '{name}' needs a 'type'")
    if settings.default_backend is None and len(backends) == 1:
        settings.default_backend = next(iter(backends))

    slots = _normalize_slots(data.get("slots") or {})

    commands: list[Command] = []
    for i, c in enumerate(data.get("commands") or []):
        phrases = c.get("phrases") or ([c["phrase"]] if "phrase" in c else [])
        by_lang: dict[str, list[str]] = {}
        if isinstance(phrases, dict):  # phrases: {en: [...], es: [...]}
            by_lang = {str(k): [str(p) for p in v] for k, v in phrases.items()}
            phrases = by_lang.get(settings.language) or next(iter(by_lang.values()), [])
        if not phrases:
            raise ConfigError(f"command #{i + 1} has no phrases")
        name = c.get("name") or phrases[0]
        action = normalize_action(c.get("action") or {}, settings.default_backend, backends,
                                  f"command '{name}'")
        commands.append(Command(name=name, phrases=[str(p) for p in phrases], action=action,
                                by_language=by_lang))
    if not commands:
        raise ConfigError("no commands defined")
    if data.get("settings", {}).get("builtin_commands", True) is not False:
        commands += missing_builtin_commands(commands, set(data.get("routines") or {}))

    sources = data.get("sources") or {"local_mic": {}}
    for name in sources:
        if name not in ("local_mic", "satellites"):
            raise ConfigError(f"unknown source '{name}' (use local_mic and/or satellites)")

    def steps(raw, where):
        if isinstance(raw, dict):
            raw = [raw]
        return [normalize_action(st, settings.default_backend, backends, f"{where} step {i + 1}")
                for i, st in enumerate(raw or [])]

    def rules(key, where):
        out = []
        for i, r in enumerate(data.get(key) or []):
            r = dict(r)
            name = r.get("name") or f"{where} {i + 1}"
            r["name"] = name
            if "do" not in r:
                raise ConfigError(f"{where} '{name}' has no `do:` steps")
            r["do"] = steps(r["do"], f"{where} '{name}'")
            out.append(r)
        return out

    for name in backends:
        if name in RESERVED:
            raise ConfigError(f"backend name '{name}' is reserved; pick another")

    routines = {str(n): steps(v, f"routine '{n}'") for n, v in (data.get("routines") or {}).items()}
    for c in commands:
        if c.action["backend"] == "routine" and c.action["routine"] not in routines:
            raise ConfigError(f"command '{c.name}' uses unknown routine '{c.action['routine']}'")

    doors = {}
    for n, d in (data.get("doors") or {}).items():
        d = dict(d or {})
        for k in ("lock", "unlock"):
            if k in d:
                d[k] = steps(d[k], f"door '{n}' {k}")
        doors[str(n).lower()] = d

    raw_scanners = data.get("door_scanner") or []
    scanners = []
    for i, sc in enumerate(raw_scanners if isinstance(raw_scanners, list) else [raw_scanners]):
        sc = dict(sc)
        for k in ("on_allowed", "on_denied", "on_unknown", "on_doorbell", "on_auto_unlock"):
            if k in sc:
                sc[k] = steps(sc[k], f"door_scanner {i + 1} {k}")
        if str(sc.get("door", "front")).lower() not in doors:
            raise ConfigError(f"door_scanner {i + 1}: door '{sc.get('door', 'front')}' isn't in `doors:`")
        scanners.append(sc)

    for section, keys in (("wake", ("escalate", "on_up")), ("freak", ("on_start", "on_end"))):
        if isinstance(data.get(section), dict):  # steps inside these sections work like routines
            data[section] = dict(data[section])
            for k in keys:
                if k in data[section]:
                    data[section][k] = steps(data[section][k], f"{section} {k}")

    from .instances import parse_instances

    instances = parse_instances(data.get("instances"))
    if "person" not in slots:
        slots["person"] = {i.name.lower(): i.id for i in instances if not i.main}
    room_owner: dict[str, str] = {}
    for inst in instances:
        for r in inst.rooms:
            if r in room_owner:
                raise ConfigError(f"room '{r}' is in two instances ({room_owner[r]}, {inst.id})")
            room_owner[r] = inst.id

    schedules = rules("schedules", "schedule")
    for sc in schedules:
        if "at" not in sc:
            raise ConfigError(f"schedule '{sc['name']}' needs `at:` (e.g. \"07:00\" or sunset)")
    automations = rules("automations", "automation")
    for a in automations:
        if not isinstance(a.get("when"), dict) or "event" not in a["when"]:
            raise ConfigError(f"automation '{a['name']}' needs `when: {{event: ...}}`")

    fingerprint = []
    for i, fp in enumerate(data.get("fingerprint") or []):
        fp = dict(fp)
        for k in ("on_accepted", "on_rejected", "on_lockout"):
            if k in fp:
                fp[k] = steps(fp[k], f"fingerprint {i + 1} {k}")
        fingerprint.append(fp)

    vacation = dict(data.get("vacation") or {})
    if "lights" in vacation:
        vacation["lights"] = steps(vacation["lights"], "vacation lights")

    return Config(
        settings, backends, slots, commands, base_dir=path.parent, sources=sources,
        routines=routines, doors=doors, voice=dict(data.get("voice") or {}),
        door_scanners=scanners, assistant=dict(data.get("assistant") or {}),
        data_dir=str(data.get("data_dir", "data")),
        instances=instances, shared_lists=list(data.get("shared_lists") or []),
        location=dict(data.get("location") or {}), schedules=schedules, automations=automations,
        vacation=vacation, fingerprint=fingerprint, web=dict(data.get("web") or {}),
        presence=dict(data.get("presence") or {}), sensors=dict(data.get("sensors") or {}),
        cameras={str(k).lower(): v for k, v in (data.get("cameras") or {}).items()},
        camera_settings=dict(data.get("camera_settings") or {}),
        extra={k: data[k] for k in EXTRA_SECTIONS if k in data},
        source_name=path.name,
    )
