"""The laptop voice version: model download, not hearing itself, silent-mic warning."""

import io
import sys
import types
import zipfile

import pytest

from phrasehome import audio_out, cli


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class _Resp:
    def __init__(self, data):
        self.data, self.headers = data, {"content-length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        yield self.data


def test_model_download_unzips_into_models(tmp_path, monkeypatch):
    import requests

    data = _zip({"vosk-model-small-en-us-0.15/am/final.mdl": b"x", "vosk-model-small-en-us-0.15/conf/model.conf": b"y"})
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Resp(data))
    (tmp_path / "config.yaml").write_text("""
backends: { dev: { type: log } }
settings: { default_backend: dev }
commands:
  - { phrases: [hello], action: { backend: dev } }
""")
    assert cli.main(["-c", str(tmp_path / "config.yaml"), "download-models"]) == 0
    assert (tmp_path / "models/vosk-model-small-en-us-0.15/am/final.mdl").read_bytes() == b"x"
    assert cli.main(["-c", str(tmp_path / "config.yaml"), "download-models"]) == 0  # already there: fine


def test_model_download_refuses_files_outside_the_folder(tmp_path, monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kw: _Resp(_zip({"../../evil.sh": b"rm -rf"})))
    with pytest.raises(SystemExit, match="strange file"):
        cli._download_zip("http://x", tmp_path, "m")
    assert not (tmp_path.parent / "evil.sh").exists()


def test_hub_speaker_marks_itself_busy(home_audio):
    router, seen = home_audio
    router._play_local(audio_out.Path("/dev/null"), 1.0)
    assert seen == [True] and audio_out.local_busy()  # still quiet for a moment after


@pytest.fixture
def home_audio(monkeypatch):
    monkeypatch.setattr(audio_out, "_quiet_until", 0.0)
    seen = []
    monkeypatch.setattr(audio_out.subprocess, "run", lambda *a, **k: seen.append(audio_out.local_busy()))
    r = audio_out.AudioRouter.__new__(audio_out.AudioRouter)
    r.play_local, r.player = True, ["afplay", "{file}"]
    return r, seen


def test_laptop_mic_ignores_the_house_talking(monkeypatch):
    from phrasehome import listener

    captured = {}

    class Stream:
        def __init__(self, **kw):
            captured["callback"] = kw["callback"]

        def start(self):
            pass

    monkeypatch.setattr(audio_out, "_busy", 0)  # the house isn't talking
    monkeypatch.setattr(audio_out, "_quiet_until", 0.0)
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawInputStream=Stream))
    cfg = types.SimpleNamespace(settings=types.SimpleNamespace(sample_rate=16000, device=None))
    got = []
    listener._open_local_mic(cfg, {"room": "Living Room"}, got.append)
    loud = (b"\x10\x27" * 4000)  # some sound
    captured["callback"](loud, 4000, None, None)
    assert got and got[-1][:2] == ("local", "living room")
    monkeypatch.setattr(audio_out, "local_busy", lambda: True)  # now it is
    n = len(got)
    listener._open_local_mic(cfg, {"room": "living room"}, got.append)
    captured["callback"](loud, 4000, None, None)
    assert len(got) == n  # dropped while the house was talking
