"""The second batch: house points, jobs, the ledger, living together, uni life, the kitchen, safety
and the personal helpers."""

import time
from datetime import timedelta

from phrasehome import clock
from tests.test_house_life import home, keys  # noqa: F401  (home is a fixture)


def q(home, said, room="kitchen", person=None):
    return home.life.quick(said, room=room, person=person)


def last(home):
    return home.said[-1] if home.said else ("", "")


# ---------------------------------------------------------------- house points
def test_good_deeds_get_a_point_and_missed_jobs_lose_one(home):
    L = home.life
    assert q(home, "i did the dishes", room="finn's room")
    assert L.points.totals()["finn"] == 1 and keys(home)[-1] == "points_good"
    assert q(home, "parker cleaned the bathroom", room="finn's room")  # credit someone else
    assert L.points.totals()["parker"] == 1
    assert q(home, "that's a lie", room="alex's room")  # someone else disputes the last claim
    assert L.points.totals()["parker"] == 0
    for _ in range(4):
        q(home, "i vacuumed the lounge", room="jo's room")
    assert L.points.totals()["jo"] == 3  # at most three self-claimed points a day
    assert keys(home)[-1] == "points_capped"


def test_a_job_done_is_just_done_and_a_missed_one_costs(home):
    L = home.life
    assert q(home, "finn your job is to clean the bathroom by tonight", room="kitchen")
    job = L.points.jobs(home.instances.all["finn"])[0]
    assert job["task"] == "clean the bathroom"
    assert q(home, "i cleaned the bathroom", room="finn's room")
    assert L.points.totals()["finn"] == 0 and not L.points.jobs(home.instances.all["finn"])
    q(home, "alex your job is to mop the kitchen by tonight")
    j = L.points.jobs(home.instances.all["alex"])[0]
    L.points.tick(clock.now().replace(hour=23, minute=59) + timedelta(hours=3))
    assert L.points.totals()["alex"] == -1 and any(s[0] == "alex" and "Minus one" in s[2] for s in home.sent)
    assert j["task"] == "mop the kitchen"


def test_leaderboard(home):
    q(home, "i did the dishes", room="finn's room")
    assert q(home, "house points")
    assert keys(home)[-1] == "points_board" and "Finn 1" in last(home)[1]
    assert q(home, "how many points do i have", room="finn's room")
    assert "first" in last(home)[1]


def test_bins_rota_helper_and_holder(home):
    L = home.life
    L.bins.cfg = {"night": clock.now().strftime("%a").lower(), "time": "00:00", "until": "23:59",
                  "rota": ["finn", "parker", "alex", "jo"]}
    holder = L.bins.turn()
    L.bins.tick(clock.now())
    assert keys(home)[-1] == "bins_reminder" and holder.name in last(home)[1]
    helper = next(i for i in L.points.housemates() if i.id != holder.id)
    assert q(home, "i took the bins out", room=helper.rooms[0])
    assert L.points.totals()[helper.id] == 1 and L.points.totals()[holder.id] == -1


# ---------------------------------------------------------------- living together
def test_bathroom_queue(home):
    assert q(home, "i'm next for the shower", room="finn's room")
    assert q(home, "put me in the shower queue", room="jo's room")
    assert "second" in last(home)[1]
    home.sent.clear()
    assert q(home, "bathroom's free")
    assert [s[0] for s in home.sent] == ["finn"]


def test_borrow_log(home):
    assert q(home, "parker borrowed my charger", room="finn's room")
    assert q(home, "who has my charger", room="finn's room") and "Parker" in last(home)[1]
    assert q(home, "parker gave back my charger", room="finn's room")
    assert not q(home, "who has my charger", room="finn's room")  # back: nothing to say


def test_house_rules_and_the_referee(home):
    assert q(home, "new house rule: shoes off inside")
    assert q(home, "what are the house rules") and "shoes off" in last(home)[1]
    assert q(home, "flip a coin") and ("heads" in last(home)[1] or "tails" in last(home)[1])
    assert q(home, "pick one of us") and any(n in last(home)[1] for n in ("Finn", "Parker", "Alex", "Jo"))


def test_lease_search(home):
    home.life.rules.save_lease(b"1. Pets are not permitted without written consent.\n\n2. Tenants must not paint walls.")
    assert home.life.rules.search("can we have a pet")[0].startswith("1. Pets")


def test_locked_out_asks_whoever_is_home(home):
    home.state.set_presence("parker", "home")
    home.sent.clear()
    assert q(home, "i'm locked out", room="front", person="finn")
    assert ("parker", "Locked out") == home.sent[0][:2]
    assert q(home, "i'm coming", room="parker's room")
    home.events.log("door.unlocked", door="front")
    assert home.life.points.totals()["parker"] == 1  # let them in: plus one


def test_poll_from_voice_and_phone(home):
    assert q(home, "poll: pizza or tacos", room="finn's room")
    assert len({s[0] for s in home.sent}) == 4
    for who in ("finn", "parker", "alex"):
        q(home, "i vote tacos", room=f"{who}'s room")
    assert home.life.from_phone("vote pizza", title="Jo")
    assert home.life.polls.poll is None and "tacos" in last(home)[1]


def test_thermostat_truce(home):
    for who, t in (("finn", 24), ("jo", 19), ("alex", 21)):
        q(home, f"i like it at {t}", room=f"{who}'s room")
    assert q(home, "thermostat truce") and "21" in last(home)[1]


def test_house_calendar_and_tv_booking(home):
    assert q(home, "the inspection is on friday at 10")
    assert any(e["kind"] == "inspection" for e in home.life.calendar.events(8))
    assert q(home, "i've got the tv saturday at 7 for the game", room="finn's room")
    assert keys(home)[-1] == "booking_ok"
    assert q(home, "i've got the tv saturday at 8", room="jo's room")
    assert keys(home)[-1] == "booking_clash" and "Finn" in last(home)[1]
    assert "BEGIN:VEVENT" in home.life.calendar.ics()


def test_errands(home):
    assert q(home, "i'm going to the shops", room="finn's room")
    home.sent.clear()
    assert q(home, "get me some milk", room="jo's room")
    assert home.sent[0][0] == "finn" and "milk" in home.sent[0][2]
    assert q(home, "i'm back from the shops", room="finn's room")
    assert home.life.points.totals()["finn"] == 1


def test_move_in_list(home):
    assert q(home, "i'm bringing a kettle and a toaster", room="finn's room")
    assert q(home, "i'm bringing a kettle", room="jo's room") and keys(home)[-1] == "movein_dup"
    assert q(home, "what do we still need") and "kettle" not in last(home)[1]


def test_birthday_stealth(home):
    assert q(home, "secret from parker: surprise dinner on saturday", room="finn's room")
    assert q(home, "what are the secret plans", room="finn's room") and "surprise dinner" in last(home)[1]
    assert q(home, "what are the secret plans", room="parker's room") and keys(home)[-1] == "secret_none"


# ---------------------------------------------------------------- uni life
def test_deadline_and_word_count_pace(home):
    assert q(home, "my essay is due friday at 5, 2000 words", room="finn's room")
    assert q(home, "i'm at 1200 words", room="finn's room")
    assert keys(home)[-1] in ("words_pace", "deadline_left") and "800" in last(home)[1]


def test_grades(home):
    assert q(home, "my calc grades are assignments 85 worth 40 percent and midterm 70 worth 20 percent", room="finn's room")
    assert q(home, "what do i need on the final to get a b in calc", room="finn's room")
    assert keys(home)[-1] == "grades_need" and "80" in last(home)[1]  # (80 - 34 - 14) / 0.4 = 80


def test_group_project(home):
    assert q(home, "group project: marketing report due friday with sam and lee", room="jo's room")
    assert q(home, "i'm doing the intro", room="jo's room")
    assert q(home, "sam's doing the survey", room="jo's room")
    assert q(home, "the intro is done", room="jo's room")
    assert q(home, "what's left on the marketing report", room="jo's room") and "survey" in last(home)[1]


def test_study_together(home):
    home.run_action({"backend": "life", "life": "study_on"}, {"here": "parker's room"})
    assert q(home, "join parker", room="finn's room")
    home.said.clear()
    home.clock.advance(home, 25 * 60 + 1)
    home.life.study.tick()
    assert keys(home)[-1] == "study_break"
    assert home.life.study.sessions["parker's room"]["members"] == ["finn's room"]


def test_hangover_and_wear(home):
    assert q(home, "i'm hungover", room="alex's room")
    assert home.instances.dnd(home.instances.all["alex"])
    assert q(home, "what should i wear", room="alex's room")


# ---------------------------------------------------------------- kitchen and money
def test_conversions_and_recipe_scaling(home):
    from phrasehome.kitchen import convert

    assert "125" in convert("how many grams in a cup of flour")
    assert "177" in convert("350 fahrenheit in celsius")
    assert "30" in convert("2 tablespoons in ml")
    from phrasehome.ai_plus import recipe_tool

    recipe_tool(home, {"action": "start", "title": "pancakes", "steps": ["mix", "fry"],
                       "ingredients": ["2 cups flour", "1/2 cup milk"]}, "kitchen")
    assert q(home, "double it")
    assert "4 cups flour" in last(home)[1] and "1 cup milk" in last(home)[1]


def test_takeout_split_and_ledger(home):
    assert q(home, "who's in on thai", room="finn's room")
    q(home, "i'm in, pad thai", room="jo's room")
    q(home, "count me in", room="parker's room")
    assert q(home, "it came to 60 dollars", room="finn's room")
    assert "$20" in last(home)[1]
    assert q(home, "who owes what") and "Jo owes Finn $20" in last(home)[1]
    assert q(home, "jo paid finn back")
    assert q(home, "who owes what") and "Jo" not in last(home)[1]


def test_bets(home):
    assert q(home, "bet: finn bets parker a coffee that it rains tomorrow")
    assert q(home, "parker won the bet")
    assert q(home, "who owes what") and "Finn owes Parker a coffee" in last(home)[1]


def test_stove_left_on_when_everyone_leaves(home):
    from phrasehome.sensors import SensorWatcher

    sw = SensorWatcher(home)
    home.life.stove.cfg = {"sensor": {"node": "kitchen", "input": "stove"}, "hot_above": 50}
    sw.values[("kitchen", "stove")] = {"node": "kitchen", "input": "stove", "value": 120.0}
    home.state.set_presence("finn", "home")
    home.sent.clear()
    home.state.set_presence("finn", "away")
    stove = [s for s in home.sent if s[1] == "Stove"]
    assert len(stove) == 4 and "bloody" not in stove[0][2]  # plain, even in aussie_uncut


# ---------------------------------------------------------------- safety
def test_safe_word_is_silent(home, tmp_path):
    from phrasehome.config import load_config

    cfg = (tmp_path / "c.yaml").read_text() + '\nsafe_word: { phrase: "pineapple pizza", tell: [parker] }\n'
    (tmp_path / "c.yaml").write_text(cfg)
    c = load_config(tmp_path / "c.yaml")
    assert any(cmd.name == "safe_word" and cmd.phrases == ["pineapple pizza"] for cmd in c.commands)
    home.life.safe_word.cfg = {"phrase": "pineapple pizza", "tell": ["parker"]}
    home.said.clear()
    home.run_action({"backend": "life", "life": "safe_word"}, {"here": "finn's room"})
    assert home.said == []  # nothing out loud
    assert home.sent[-1][0] == "parker" and "Finn used the safe word" in home.sent[-1][2]


def test_ring_my_phone_and_night_out(home):
    assert q(home, "where's my phone", room="jo's room") and home.sent[-1][0] == "jo"
    for p in ("finn", "jo"):
        home.state.set_presence(p, "home")
    assert q(home, "we're going out, back by 2am")
    home.state.set_presence("finn", "away")
    home.state.set_presence("jo", "away")
    no = home.life.night_out
    no.out["back_by"] = time.time() - 20 * 60
    home.state.set_presence("finn", "home")
    home.sent.clear()
    no.tick(clock.now())
    assert ("jo", "Are you OK?") == home.sent[0][:2]
    assert home.life.from_phone("ok", title="Jo")


def test_new_device_on_the_wifi(home):
    n = home.life.network
    n.check({"aa:bb:cc:00:00:01": "192.168.1.2"})  # learning: quiet
    d = n.load({})
    d["first"] = time.time() - 2 * 86400
    n.save(d)
    home.sent.clear()
    assert n.check({"aa:bb:cc:00:00:01": "192.168.1.2", "de:ad:be:ef:00:02": "192.168.1.9"}) == ["de:ad:be:ef:00:02"]
    assert home.sent and "192.168.1.9" in home.sent[0][2]


def test_party_head_count_locks_up_after_the_last_guest(home):
    home.run_action({"backend": "life", "life": "party_start"}, {"here": "living room"})
    assert q(home, "five more people just arrived")
    home.life.party.active["quieted"] = True
    home.doors.set("front", "unlocked")
    assert q(home, "five people left")
    assert home.doors.state("front") == "locked"


# ---------------------------------------------------------------- personal
def test_where_i_put_things_and_privacy(home):
    assert q(home, "my passport is in the desk drawer", room="finn's room")
    assert q(home, "where's my passport", room="finn's room") and "desk drawer" in last(home)[1]
    assert q(home, "the remote is behind the couch", room="kitchen")
    assert q(home, "where's the remote", room="jo's room") and "couch" in last(home)[1]


def test_reminders_by_place(home):
    assert q(home, "remind me when i get home to call mum", room="finn's room")
    home.sent.clear()
    home.state.set_presence("finn", "home")
    assert home.sent and "call mum" in home.sent[0][2]


def test_nicknames(home):
    assert q(home, "parks means parker")
    assert home.life.people.find("parks").id == "parker"
    assert q(home, "parks is vegetarian")
    assert "is vegetarian" in home.life.people.facts(home.instances.all["parker"])


def test_my_usual(home):
    from phrasehome.config import Command

    home.config.commands.append(Command("lamp_on", ["lamp on"], {"backend": "dev", "node": "{here}", "channel": "lamp", "state": True}))
    assert q(home, "set my usual to lamp on and lockdown", room="finn's room")
    from tests.test_door import Recorder

    Recorder.calls.clear()
    assert q(home, "my usual", room="finn's room")
    assert {"node": "finn's room", "channel": "lamp", "state": True} in Recorder.calls


def test_tv_cut_off_is_strict(home):
    from phrasehome.config import Command

    home.config.commands.append(Command("tv_off", ["tv off"], {"backend": "dev", "node": "living", "channel": "tv", "state": False}))
    assert q(home, "cut me off at 1am", room="living room")
    assert q(home, "cancel the tv timer", room="living room") and keys(home)[-1] == "tv_strict"
    t = home.life.tv.timers["living room"]
    t["at"] = time.time() + 5 * 60
    home.life.tv.tick(clock.now())
    assert keys(home)[-1] == "tv_warn"
    t["at"] = time.time() - 1
    from tests.test_door import Recorder

    home.life.tv.tick(clock.now())
    assert {"node": "living", "channel": "tv", "state": False} in Recorder.calls


def test_cleaning_blitz_and_semester_break(home):
    home.state.set_presence("finn", "home")
    assert q(home, "cleaning blitz")
    assert q(home, "done", room="finn's room") and home.life.points.totals()["finn"] == 1
    inst = home.instances.all["jo"]
    item = home.instances.add(inst, "alarm", clock.now() + timedelta(hours=10), label="uni")
    assert q(home, "semester break mode")
    assert home.state.mode == "vacation" and home.instances.items(inst, ("alarm",))[0].get("off")
    home.state.set_presence("finn", "away")
    home.state.set_presence("jo", "home")  # first one back
    assert home.state.mode != "vacation" and not home.instances.items(inst, ("alarm",))[0].get("off")
    assert item["id"]


def test_what_did_i_miss_horoscope_and_year(home):
    home.state.set_presence("finn", "home")
    home.state.set_presence("finn", "away")
    home.events.log("doorbell", door="front")
    home.events.log("package.delivered", name="books")
    home.state.set_presence("finn", "home")
    assert q(home, "what did i miss", room="finn's room") and "doorbell" in last(home)[1]
    assert q(home, "read the horoscopes") and "horoscope" in last(home)[1].lower()
    assert q(home, "year in review") and "year in review" in last(home)[1]


def test_quick_sentences_are_commands_too(home):
    names = {c.name for c in home.config.commands}
    assert {"life_say_referee", "life_say_blitz", "life_say_points"} <= names
    home.run_action({"backend": "life", "life": "quick"}, {"here": "kitchen"}, "flip a coin")
    assert keys(home)[-1] == "coin"


def test_house_page_has_the_new_cards(home):
    import requests

    from phrasehome.web import WebServer

    home.config.web = {"port": 0, "host": "127.0.0.1"}
    web = WebServer(home)
    web.start()
    try:
        base = f"http://127.0.0.1:{web.port}"
        assert "House points" in requests.get(base + "/house", timeout=5).text
        assert requests.post(base + "/api/house/points", json={"person": "jo", "delta": 1}, timeout=5).ok
        st = requests.get(base + "/api/house", timeout=5).json()
        assert ["Jo", 1] in st["points"]["board"] and "rooms" in st
        assert requests.post(base + "/api/house/say", json={"text": "the inspection is on friday at 10"}, timeout=5).ok
        assert "BEGIN:VCALENDAR" in requests.get(base + "/house.ics", timeout=5).text
        assert requests.post(base + "/api/house/lease", json={"text": "Pets are not allowed."}, timeout=5).ok
        assert requests.get(base + "/api/house", timeout=5).json()["lease"] is True
    finally:
        web.stop()


# ---------------------------------------------------------------- the laptop companion
def test_companion_classifies_without_sending_titles():
    from phrasehome.companion import classify

    assert classify("Google Chrome", "YouTube - lofi beats") == "distraction"
    assert classify("zoom.us", "Zoom Meeting") == "call"
    assert classify("1Password", "Vault") == "private"
    assert classify("Code", "main.py") == "code" and classify("Microsoft Word", "Essay.docx") == "study"


def test_companion_call_makes_the_door_busy_and_distraction_gets_a_nudge(home):
    c = home.life.companion
    c.report("finn", {"category": "call", "app": "zoom.us"})
    assert home.life.door_status.status("finn's room") == "busy"
    c.report("finn", {"category": "study", "app": "Word"})
    assert home.life.door_status.status("finn's room") == "free"
    home.run_action({"backend": "life", "life": "study_on"}, {"here": "finn's room"})
    c.report("finn", {"category": "distraction", "app": "YouTube"})
    home.clock.advance(home, 4 * 60)
    out = c.report("finn", {"category": "distraction", "app": "YouTube"})
    assert out["notify"] and "YouTube" in out["notify"][0] and keys(home)[-1] == "companion_nudge"


def test_look_at_my_screen_round_trip(home, monkeypatch):
    c = home.life.companion
    c.report("finn", {"category": "code", "app": "Terminal"})
    monkeypatch.setattr(home.life.vision, "ask", lambda img, question, kind, room, person: f"{kind}: fix the import")
    assert q(home, "what's this error", room="finn's room") and keys(home)[-1] == "companion_looking"
    reqs = c.poll("finn")["requests"]
    assert reqs and reqs[0]["kind"] == "screen"
    import base64

    out = c.screen("finn", {"id": reqs[0]["id"], "image": base64.b64encode(b"\xff\xd8\xffjpeg").decode()})
    assert out["answer"] == "screen: fix the import"
    assert q(home, "stop watching my screen", room="finn's room")
    assert c.report("finn", {"category": "distraction"})["paused"] is True


def test_party_cleanup_counts_for_points(home):
    home.run_action({"backend": "life", "life": "party_start"}, {"here": "living room"})
    home.run_action({"backend": "life", "life": "party_stop"}, {"here": "living room"})
    jobs = home.life.points.jobs()
    assert {j["task"] for j in jobs} == {"bottles", "kitchen", "vacuum", "bins"}
    finn_job = next(j for j in jobs if j["person"] == "finn")
    assert q(home, f"i did the {finn_job['task']}", room="finn's room")
    assert home.life.points.totals()["finn"] == 0  # their own job: just done


def test_scenarios_run_against_a_config(tmp_path):
    from phrasehome import scenarios
    from tests.test_house_life import CONFIG

    (tmp_path / "c.yaml").write_text(CONFIG + '\nscenarios:\n  mine:\n    - { time: "19:00" }\n'
                                     '    - { say: "flip a coin", person: p1 }\n    - { expect: "it\'s" }\n')
    for name in ("friday_night", "walk_home_late", "points_week", "mine"):
        r = scenarios.run(tmp_path / "c.yaml", name, show=lambda *a: None)
        assert r["failed"] == 0 and r["passed"] >= 1, (name, r["log"][-8:])
