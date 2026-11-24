"""Energy, appliances, cheap power, packages, countdowns, meal planner, Spotify."""

import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from phrasehome import ai, clock
from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.energy import Energy, Plug
from phrasehome.meals import Meals, week_of
from phrasehome.packages import Packages
from phrasehome.spotify import Spotify
from tests.test_door import Recorder

CFG = """
data_dir: data
settings: { default_backend: dev }
sources: { satellites: {} }
backends:
  dev: { type: record }
voice: { engine: none }
instances:
  household: { main: true, rooms: [living room, kitchen] }
  sam: { name: Sam, rooms: [bedroom], diet: { dislikes: [mushrooms] } }
  mia: { name: Mia, rooms: [kids room], diet: { allergies: [peanuts] } }
energy:
  currency: "$"
  price: 0.20
  time_of_use: [ { window: "23:00-07:00", price: 0.10, cheap: true } ]
  plugs:
    washer: { host: x, room: laundry, appliance: { running_above: 10, done_below: 3, done_after: 60, start_after: 20 } }
    sam pc: { host: x, owner: sam }
    tv: { host: x, room: living room }
    lamp: { host: x, room: kids room }
meals: { slots: [dinner], staples: [salt, pepper] }
packages: { api_key: test }
spotify: { client_id: a, client_secret: b, playlists: { dinner: "spotify:playlist:abc" } }
commands:
  - { name: dinner, phrases: ["what's for dinner"], action: { report: dinner } }
"""


@pytest.fixture
def home(tmp_path):
    (tmp_path / "c.yaml").write_text(CFG)
    Recorder.calls = []
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    h.said = []
    h.voice.speak = lambda key, room=None, rooms=None, everywhere=False, urgent=False, **v: \
        h.said.append((key, room)) or key
    yield h
    h.close()


def feed(e, plug, power, total, t):
    e.update(e.plugs[plug], now=t, reading=(power, total))


# ---------------------------------------------------------------- energy
def test_energy_attribution_and_report(home):
    e = Energy(home)

    class Rooms:
        def people_in(self, room):
            return ["sam", "mia"] if room == "living room" else []
    home.rooms = Rooms()
    for plug in e.plugs:
        feed(e, plug, 0, 0, 0)
    feed(e, "sam pc", 200, 2000, 10)       # 2 kWh, Sam's own plug
    feed(e, "tv", 100, 1000, 10)           # 1 kWh, shared room: Sam and Mia are there
    feed(e, "lamp", 10, 500, 10)           # 0.5 kWh, Mia's room
    feed(e, "washer", 0, 3000, 10)         # 3 kWh, nobody in particular
    t = e.totals(1)
    assert round(t["kwh"], 2) == 6.5
    assert t["people"] == {"household": 3.0, "sam": 2.5, "mia": 1.0}
    assert list(t["devices"])[0] == "washer"
    text = e.report(1)
    assert "6.5 kilowatt hours" in text and "Sam used the most, 2.5" in text and "Mia 1.0" in text


def test_appliance_done(home):
    e = Energy(home)
    w = e.plugs["washer"]
    w._fake = True
    for t, p in [(0, 0), (10, 500), (35, 450), (100, 2), (130, 400), (150, 1), (200, 1), (215, 1)]:
        w.power = p
        e._appliance(w, t)
    kinds = [x["kind"] for x in home.events.query("appliance")]
    assert kinds == ["appliance.started", "appliance.done"]
    assert home.events.query("appliance.done")[0]["appliance"] == "washer"


def test_cheap_power(home):
    e = Energy(home)
    noon = clock.now().replace(hour=12, minute=0, second=0, microsecond=0)
    assert e.price_at(noon) == (0.20, False)
    assert e.price_at(noon.replace(hour=23, minute=30)) == (0.10, True)
    assert e.next_cheap(noon).hour == 23
    msg = e.run_when_cheap("washer", hours=2)
    items = home.instances.items(home.instances.main, ("action",))
    if "now" in msg:  # running the test at night
        assert len(items) == 1
    else:
        assert "11:00 PM" in msg and len(items) == 2
        assert items[0]["action"] == {"backend": "energy", "plug": "washer", "state": "on"}


def test_shelly_and_tasmota_plugs():
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            seen.append(self.path)
            q = parse_qs(u.query)
            if u.path == "/rpc/Switch.GetStatus":
                body = {"output": True, "apower": 42.5, "aenergy": {"total": 1234.0}}
            elif u.path == "/cm" and q["cmnd"] == ["Status 10"]:
                body = {"StatusSNS": {"ENERGY": {"Total": 1.5, "Power": 7}}}
            elif u.path == "/cm":
                body = {"POWER": "OFF"}
            else:
                body = {}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host = f"127.0.0.1:{srv.server_port}"
    s = Plug("a", {"host": host, "kind": "shelly"})
    s.read()
    assert (s.power, s.total_wh, s.on) == (42.5, 1234.0, True)
    s.switch(False)
    t = Plug("b", {"host": host, "kind": "tasmota"})
    t.read()
    assert (t.power, t.total_wh, t.on) == (7.0, 1500.0, False)
    srv.shutdown()
    assert "/rpc/Switch.Set?id=0&on=false" in seen


# ---------------------------------------------------------------- packages and countdowns
def test_package_tracking(home):
    p = Packages(home)
    p._post = lambda path, body: {}
    it = p.add("1Z 999 AA1 0123 4567 84", "new shoes")
    assert it["number"] == "1Z999AA10123456784"
    info = lambda st, desc: [{"number": it["number"], "track_info": {
        "latest_status": {"status": st}, "latest_event": {"description": desc},
        "time_metrics": {"estimated_delivery_date": {"from": "2026-11-26T00:00:00"}}}}]
    p.refresh(info("InTransit", "Departed facility"))
    p.refresh(info("OutForDelivery", "On the truck"))
    p.refresh(info("OutForDelivery", "On the truck"))
    assert [e["status"] for e in home.events.query("package.status")] == ["InTransit", "OutForDelivery"]
    assert home.events.query("package.out_for_delivery")
    assert "new shoes is out for delivery, expected 2026-11-26" in p.report()
    p.refresh(info("Delivered", "Left at front door"))
    assert home.events.query("package.carrier_delivered")
    assert p.remove("shoes") == 1


def test_countdowns(home):
    insts = home.instances
    sam = insts.all["sam"]
    soon = (clock.now() + timedelta(days=12)).strftime("%Y-%m-%d")
    insts.countdown_add(insts.main, "the beach trip", soon)
    insts.countdown_add(sam, "Sam's birthday", (clock.now() + timedelta(days=3)).strftime("%Y-%m-%d"))
    insts.countdown_add(sam, "old thing", "2020-01-01")
    names = [c["name"] for c in insts.countdowns(sam)]
    assert names == ["Sam's birthday", "the beach trip"]           # own + household, past dropped
    assert [c["name"] for c in insts.countdowns(insts.all["mia"])] == ["the beach trip"]
    text = home.run_action({"backend": "report", "report": "countdowns"}, {"here": "bedroom"})
    assert text.startswith("3 days until Sam's birthday. 12 days until the beach trip.")


# ---------------------------------------------------------------- meals
def test_meal_plan_ingredients_pantry_and_shopping_sync(home):
    m = Meals(home)
    insts = home.instances
    insts.list_add(insts.main, "pantry", ["rice", "Garlic"])
    insts.list_add(insts.main, "shopping", ["dish soap"])
    m.set_meal("mon", "dinner", {"name": "Chicken stir fry", "ingredients":
                                 ["1 lb chicken thighs", "1 1/2 cups rice", "2 cloves garlic", "salt", "1 bell pepper"]})
    m.set_meal("wed", "dinner", {"name": "Chicken tacos", "ingredients":
                                 ["1/2 lb chicken thigh", "8 tortillas", "1 bell pepper"]})
    ing = {i["key"]: i for i in m.ingredients()}
    assert ing["chicken thigh"]["qty"] == "1 1/2 lb" and not ing["chicken thigh"]["have"]
    assert ing["bell pepper"]["qty"] == "2" and ing["rice"]["have"] and ing["salt"]["have"]
    shop = insts.list_get(insts.main, "shopping")
    assert "dish soap" in shop and "chicken thighs (1 1/2 lb)" in shop and "tortillas (8)" in shop
    assert not any(s.startswith("rice") or s.startswith("garlic") for s in shop)
    m.set_meal("wed", "dinner", None)                 # plan changed: tortillas no longer needed
    shop = insts.list_get(insts.main, "shopping")
    assert "tortillas (8)" not in shop and "dish soap" in shop and "chicken thighs (1 lb)" in shop
    m.bought("chicken thighs (1 lb)")
    assert "chicken thighs" in insts.list_get(insts.main, "pantry")
    assert home.run_action(home.config.commands[0].action) in ("Tonight is Chicken stir fry.",
                                                               "Nothing's planned for tonight yet.")


def test_ai_meal_planning_respects_diets(home, monkeypatch):
    m = Meals(home)
    home.instances.diet_update(home.instances.all["sam"], "allergies", ["shellfish"])
    prompts = []

    def fake(h, prompt, schema=None, **kw):
        prompts.append(prompt)
        if "ideas" in schema["properties"]:
            return {"ideas": [{"name": "Fried rice", "uses": ["rice"], "missing": ["eggs"], "minutes": 20}]}
        return {"meals": [{"day": d, "slot": "dinner", "name": f"Meal {d}", "servings": 4,
                           "ingredients": [{"qty": "1", "item": "onion"}]} for d in ("mon", "tue", "xyz")]}
    monkeypatch.setattr(ai, "ask", fake)
    planned = m.plan_week("no fish", days=["mon", "tue"])
    assert [p["day"] for p in planned] == ["mon", "tue"]
    assert '"Mia": {"allergies": ["peanuts"]}' in prompts[0] and "shellfish" in prompts[0] and "mushrooms" in prompts[0]
    assert "onion (2)" in home.instances.list_get(home.instances.main, "shopping")
    assert m.suggest()[0]["name"] == "Fried rice"


# ---------------------------------------------------------------- spotify
def test_spotify_play_logic(home):
    sp = Spotify(home)
    calls = []

    def api(method, path, **kw):
        calls.append((method, path, kw.get("json"), kw.get("params")))
        if path == "/me/player/devices":
            return {"devices": [{"name": "House", "id": "dev1"}]}
        if path == "/me/playlists":
            return {"items": [{"name": "Road Trip", "uri": "spotify:playlist:rt"}]}
        if path == "/search":
            return {"artists": {"items": [{"name": "Miles Davis", "uri": "spotify:artist:md"}]}}
        if path == "/me/player/currently-playing":
            return {"item": {"id": "t1", "name": "So What", "artists": [{"name": "Miles Davis"}]}}
    sp.api = api
    assert sp.play("road trip") == "Playing your playlist Road Trip."
    assert calls[-1] == ("PUT", "/me/player/play", {"context_uri": "spotify:playlist:rt"}, {"device_id": "dev1"})
    assert sp.play("miles davis") == "Playing Miles Davis."
    assert sp.play(playlist="dinner").startswith("Playing")
    assert calls[-1][2] == {"context_uri": "spotify:playlist:abc"}
    assert sp.simple("what") == "This is So What by Miles Davis."
    assert sp.simple("like").startswith("Saved So What")
    assert calls[-1] == ("PUT", "/me/tracks", None, {"ids": "t1"})
