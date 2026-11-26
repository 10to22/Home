"""family-test, ai-usage and the /ai page are reachable."""

import json
import types

import numpy as np

from phrasehome import cli, speechtest
from phrasehome.config import load_config
from tests.test_understanding import CFG


def _cfg(tmp_path):
    (tmp_path / "config.yaml").write_text(CFG)
    return tmp_path / "config.yaml"


def test_ai_usage_command(tmp_path, capsys):
    from phrasehome.ai_plus import Meter

    path = _cfg(tmp_path)
    cfg = load_config(path)
    Meter(types.SimpleNamespace(config=cfg)).record(
        "claude-haiku-5-5", types.SimpleNamespace(input_tokens=1000, output_tokens=200), 0.8)
    assert cli.main(["-c", str(path), "ai-usage"]) == 0
    out = capsys.readouterr().out
    assert "1 AI calls" in out and "claude-haiku-5-5" in out


def test_family_test_command(tmp_path, capsys, monkeypatch):
    path = _cfg(tmp_path)
    cfg = load_config(path)
    tone = (np.sin(np.arange(16000) / 5) * 3000).astype(np.int16).tobytes()
    speechtest.save_set(cfg, [{"expected": "lights on", "command": "lights_on"}], lambda i, n: tone, person="finn")
    monkeypatch.setattr(speechtest, "recognizers", lambda config: {"fake": lambda pcm: "lights on"})
    assert cli.main(["-c", str(path), "family-test"]) == 0
    assert "finn: 1/1 commands right" in capsys.readouterr().out


def test_ai_page_and_usage_api(tmp_path):
    import requests

    from phrasehome.dispatcher import Dispatcher
    from phrasehome.web import WebServer

    cfg = load_config(_cfg(tmp_path))
    cfg.web = {"port": 0, "host": "127.0.0.1"}
    home = Dispatcher(cfg)
    web = WebServer(home)
    web.start()
    try:
        assert "AI usage" in requests.get(f"http://127.0.0.1:{web.port}/ai", timeout=5).text
        s = requests.get(f"http://127.0.0.1:{web.port}/api/ai/usage", timeout=5).json()
        assert s["calls"] == 0 and "person" in s
    finally:
        web.stop()
        home.close()
