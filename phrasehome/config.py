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
    log_heard: bool = False  # log everything heard (handy for tuning; off for privacy)


@dataclass
class Command:
    name: str
    phrases: list[str]
    action: dict[str, Any]


@dataclass
class Config:
    settings: Settings
    backends: dict[str, dict[str, Any]]
    slots: dict[str, dict[str, str]]  # slot name -> {spoken form: value}
    commands: list[Command]
    base_dir: Path
    sources: dict[str, Any] = field(default_factory=dict)

    def resolve_path(self, p: str) -> Path:
        path = Path(p).expanduser()
        return path if path.is_absolute() else self.base_dir / path


def _normalize_slots(raw: dict[str, Any]) -> dict[str, dict[str, str]]:
    slots: dict[str, dict[str, str]] = {}
    for name, values in (raw or {}).items():
        if isinstance(values, list):
            slots[name] = {str(v): str(v) for v in values}
        elif isinstance(values, dict):
            slots[name] = {str(k): str(v) for k, v in values.items()}
        else:
            raise ConfigError(f"slot '{name}' must be a list or a mapping")
    return slots


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
        if not phrases:
            raise ConfigError(f"command #{i + 1} has no phrases")
        name = c.get("name") or phrases[0]
        action = dict(c.get("action") or {})
        action.setdefault("backend", settings.default_backend)
        if not action["backend"]:
            raise ConfigError(f"command '{name}' has no backend and no default_backend is set")
        if action["backend"] not in backends:
            raise ConfigError(f"command '{name}' uses unknown backend '{action['backend']}'")
        commands.append(Command(name=name, phrases=[str(p) for p in phrases], action=action))
    if not commands:
        raise ConfigError("no commands defined")

    sources = data.get("sources") or {"local_mic": {}}
    for name in sources:
        if name not in ("local_mic", "satellites"):
            raise ConfigError(f"unknown source '{name}' (use local_mic and/or satellites)")

    return Config(settings, backends, slots, commands, base_dir=path.parent, sources=sources)
