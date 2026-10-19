import itertools
from datetime import datetime, timedelta, timezone

import pytest

from agent import chats, memory, sessions
from agent.auth import DEMO_USER_ID
from agent.models import MustHave, ShoppingBrief
from tests.fixtures.app.fakes import BENGALURU, brief_answer
from tests.test_app import make_client

CHAT = "c_" + "a" * 32
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
REQUEST = {"text": "i want shirts", "pincode": BENGALURU, "language": "en", "source": "catalog", "ask": False,
           "chat_id": CHAT, "memory": True}
_ids = itertools.count(1)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(sessions, "now_utc", lambda: NOW)


def shirt(colour=None, gender="unknown", size="", **options):
    must = options.pop("must", [(f"colour={colour}", "is")] if colour else [])
    return brief_answer("Shirts", gender=gender, must=must, size=size, **{"budget": 0, **options})


def scripted(*answers):
    queue = iter(answers)
    return lambda contents: next(queue)


def stamp(days):
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def past(wired, product, days, filters, remembered=(), user=DEMO_USER_ID):
    """A saved chat of another day with one entry for this product."""
    chat_id = f"c_{next(_ids):032x}"
    moment = stamp(days)
    entry = {"product": product, "timestamps": {"created_at": moment, "updated_at": moment}, "filters": filters,
             "remembered": list(remembered), "session_id": "s-old"}
    wired.store.db.data.setdefault("chats", {})[chat_id] = {
        "user_id": user, "title": "old", "timestamps": {"created_at": moment, "updated_at": moment, "last_opened_at": moment},
        "messages": [], "pending": None, "products": [entry],
    }
    return chat_id


def post(client, **extra):
    response = client.post("/api/brief", json=REQUEST | extra)
    assert response.status_code == 200, response.text
    return response.json()


def update(client, session_id, change, **extra):
    body = {"session_id": session_id, "change": change, "ask": False, "chat_id": CHAT, "memory": True} | extra
    response = client.post("/api/brief/update", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def attributes(brief):
    return {m["attribute"]: m["value"] for m in brief["must_haves"]}


def mixed(brief):
    return {n["value"]: n["weight"] for n in brief["nice_to_haves"] if n.get("weight")}


def entry(wired, name):
    return next(p for p in wired.store.db.docs("chats")[CHAT]["products"] if p["product"] == name)


def product(filters, days, remembered=()):
    moment = NOW - timedelta(days=days)
    stamps = chats.ProductTimestamps(created_at=moment, updated_at=moment)
    return chats.ChatProduct(product="shirt", timestamps=stamps, filters=filters, remembered=list(remembered), session_id="s")


# ---------- Through the app ----------


def test_fills_what_the_prompt_left_open_from_past_shirts():
    client, wired = make_client(brief=scripted(shirt()))
    past(wired, "shirt", 3, {"colour": "yellow", "gender": "men", "size": "M", "source": "catalog"})
    brief = post(client)["brief"]
    assert attributes(brief) == {"colour": "yellow"}
    assert (brief["gender"], brief["size"], brief["remembered"]) == ("men", "M", ["colour", "gender", "size"])
    assert "size" not in brief["missing"]
    assert entry(wired, "shirt")["remembered"] == ["colour", "gender", "size"]
    assert entry(wired, "shirt")["filters"]["colour"] == "yellow"


def test_only_entries_for_the_same_product_are_used():
    client, wired = make_client(brief=scripted(shirt()))
    past(wired, "casual shoes", 1, {"colour": "red", "gender": "men", "size": "UK 9"})
    brief = post(client)["brief"]
    assert brief["remembered"] == [] and brief["gender"] is None and attributes(brief) == {}


def test_a_recent_colour_beats_two_old_ones_and_an_old_one_loses_to_a_recent_one():
    client, wired = make_client(brief=scripted(shirt(), shirt()))
    past(wired, "shirt", 1, {"colour": "yellow"})
    past(wired, "shirt", 60, {"colour": "red"})
    past(wired, "shirt", 61, {"colour": "red"})
    brief = post(client)["brief"]
    assert attributes(brief) == {} and mixed(brief) == {"yellow": 0.804, "red": 0.196}
    assert brief["remembered"] == ["colour"]
    other, wired = make_client(brief=scripted(shirt()))
    past(wired, "shirt", 60, {"colour": "yellow"})
    past(wired, "shirt", 2, {"colour": "red"})
    assert mixed(post(other)["brief"]) == {"red": 0.864, "yellow": 0.136}


def test_three_colours_mix_mostly_the_newest_some_the_next_a_little_the_oldest():
    client, wired = make_client(brief=scripted(brief_answer("Kurtas", gender="unknown", must=[], size="", budget=0)))
    past(wired, "kurta", 0.01, {"colour": "blue", "gender": "women"})
    past(wired, "kurta", 0.02, {"colour": "black", "gender": "women"})
    past(wired, "kurta", 0.03, {"colour": "red"})
    brief = post(client, text="i want kurtas")["brief"]
    assert mixed(brief) == {"blue": 0.51, "black": 0.306, "red": 0.184}
    assert attributes(brief) == {} and brief["gender"] == "women" and brief["remembered"] == ["gender", "colour"]
    assert entry(wired, "kurta")["filters"]["prefer"] == {"colour": ["blue", "black", "red"]}


def test_a_colour_with_under_a_tenth_of_the_weight_is_left_out():
    client, wired = make_client(brief=scripted(shirt()))
    past(wired, "shirt", 1, {"colour": "yellow"})
    past(wired, "shirt", 200, {"colour": "red"})
    brief = post(client)["brief"]
    assert attributes(brief) == {"colour": "yellow"} and mixed(brief) == {}


def test_a_mix_survives_an_unrelated_edit_and_ends_when_the_shopper_names_a_colour():
    mix_back = shirt(budget=2000, nice=[("colour=yellow", "is"), ("colour=red", "is")])
    client, wired = make_client(brief=scripted(shirt(), mix_back, shirt("red", budget=2000)))
    past(wired, "shirt", 1, {"colour": "yellow", "gender": "men"})
    past(wired, "shirt", 3, {"colour": "red", "gender": "men"})
    made = post(client)
    first = mixed(made["brief"])
    assert set(first) == {"yellow", "red"} and first["yellow"] > first["red"]
    kept = update(client, made["session_id"], {"type": "text", "text": "under 2000"})["brief"]
    assert mixed(kept) == first and "colour" in kept["remembered"]
    red = update(client, made["session_id"], {"type": "text", "text": "red"})["brief"]
    assert attributes(red) == {"colour": "red"} and mixed(red) == {} and "colour" not in red["remembered"]


def test_memory_off_drops_the_mix():
    client, wired = make_client(brief=scripted(shirt()))
    past(wired, "shirt", 1, {"colour": "yellow"})
    past(wired, "shirt", 3, {"colour": "red"})
    made = post(client)
    assert mixed(made["brief"])
    off = update(client, made["session_id"], {"type": "set_memory", "enabled": False})["brief"]
    assert off["nice_to_haves"] == [] and off["remembered"] == []
    on = update(client, made["session_id"], {"type": "set_memory", "enabled": True})["brief"]
    assert mixed(on) == mixed(made["brief"])


def test_an_exact_tie_gives_nothing():
    client, wired = make_client(brief=scripted(shirt()))
    past(wired, "shirt", 5, {"fabric": "linen", "gender": "men"})
    past(wired, "shirt", 5, {"fabric": "cotton", "gender": "men"})
    brief = post(client)["brief"]
    assert attributes(brief) == {} and brief["gender"] == "men" and brief["remembered"] == ["gender"]


def test_memory_off_changes_nothing():
    client, wired = make_client(brief=scripted(shirt()))
    past(wired, "shirt", 1, {"colour": "yellow", "gender": "men", "size": "M"})
    brief = post(client, memory=False)["brief"]
    assert attributes(brief) == {} and brief["gender"] is None and brief["size"] is None
    assert brief["remembered"] == [] and "size" in brief["missing"]
    assert entry(wired, "shirt")["remembered"] == []
    client, wired = make_client(brief=scripted(shirt()))
    past(wired, "shirt", 1, {"colour": "yellow"})


def test_the_shoppers_words_win_and_memory_fills_the_rest():
    client, wired = make_client(brief=scripted(shirt("red")))
    past(wired, "shirt", 2, {"colour": "yellow", "gender": "men"})
    brief = post(client, text="red shirts")["brief"]
    assert attributes(brief) == {"colour": "red"} and brief["gender"] == "men" and brief["remembered"] == ["gender"]


def test_saying_red_after_memory_picked_yellow_keeps_men_remembered():
    client, wired = make_client(brief=scripted(shirt(), shirt("red", gender="men")))
    past(wired, "shirt", 2, {"colour": "yellow", "gender": "men"})
    made = post(client)
    assert made["brief"]["remembered"] == ["colour", "gender"]
    brief = update(client, made["session_id"], {"type": "text", "text": "red"})["brief"]
    assert attributes(brief) == {"colour": "red"} and brief["gender"] == "men"
    assert brief["remembered"] == ["gender"]
    assert entry(wired, "shirt")["remembered"] == ["gender"]


def test_set_memory_off_removes_exactly_the_remembered_fields_and_on_restores_them():
    client, wired = make_client(brief=scripted(shirt(must=[("fabric=cotton", "is")])))
    past(wired, "shirt", 2, {"colour": "yellow", "gender": "men", "size": "M", "budget_max_inr": 1500})
    made = post(client)
    first = made["brief"]
    assert first["remembered"] == ["colour", "gender", "size", "budget_max_inr"]
    off = update(client, made["session_id"], {"type": "set_memory", "enabled": False})["brief"]
    assert attributes(off) == {"fabric": "cotton"}
    assert (off["gender"], off["size"], off["budget_max_inr"], off["remembered"]) == (None, None, None, [])
    assert "size" in off["missing"]
    on = update(client, made["session_id"], {"type": "set_memory", "enabled": True})["brief"]
    assert on["remembered"] == first["remembered"]
    assert attributes(on) == {"fabric": "cotton", "colour": "yellow"} and (on["gender"], on["size"]) == ("men", "M")


def test_values_that_came_from_memory_are_not_remembered_again():
    client, wired = make_client(brief=scripted(shirt()))
    past(wired, "shirt", 1, {"colour": "yellow", "gender": "men"}, remembered=["colour"])
    brief = post(client)["brief"]
    assert attributes(brief) == {} and brief["remembered"] == ["gender"]


def test_a_saved_rule_wins_over_memory():
    client, wired = make_client(brief=scripted(shirt()))
    wired.store.update_user(DEMO_USER_ID, {"rules": [MustHave(id="r1", attribute="colour", rule="is_not", value="yellow")]})
    past(wired, "shirt", 1, {"colour": "yellow"})
    brief = post(client)["brief"]
    assert [(m["rule"], m["value"], m["from_rule"]) for m in brief["must_haves"]] == [("is_not", "yellow", True)]
    assert brief["remembered"] == []


def test_a_suggestion_that_is_not_valid_for_the_product_is_skipped():
    client, wired = make_client(brief=scripted(shirt()))
    past(wired, "shirt", 1, {"size": "UK 9", "gender": "men", "colour": "sparkly", "nonsense": True})
    brief = post(client)["brief"]
    assert brief["size"] is None and attributes(brief) == {} and brief["remembered"] == ["gender"]


def test_switching_product_drops_the_old_values_and_fills_the_new_products():
    kurta = brief_answer("Kurtas", gender="unknown", must=[], size="", budget=0)
    to_shirt = shirt("yellow", gender="women", size="M")
    client, wired = make_client(brief=scripted(kurta, to_shirt))
    past(wired, "kurta", 1, {"colour": "yellow", "gender": "women", "size": "M"})
    past(wired, "shirt", 1, {"colour": "blue", "gender": "men", "size": "L"})
    made = post(client, text="yellow kurta")
    assert made["brief"]["remembered"] == ["colour", "gender", "size"]
    moved = update(client, made["session_id"], {"type": "text", "text": "show me shirts"})
    assert moved["session_id"] != made["session_id"] and moved["product"] == "shirt"
    brief = moved["brief"]
    assert attributes(brief) == {"colour": "blue"} and (brief["gender"], brief["size"]) == ("men", "L")
    assert brief["remembered"] == ["colour", "gender", "size"]
    assert entry(wired, "kurta")["filters"]["colour"] == "yellow"


def test_switching_product_with_memory_off_drops_the_old_values_and_fills_nothing():
    kurta = brief_answer("Kurtas", gender="unknown", must=[], size="", budget=0)
    client, wired = make_client(brief=scripted(kurta, shirt("yellow", gender="women", size="M")))
    past(wired, "kurta", 1, {"colour": "yellow", "gender": "women", "size": "M"})
    past(wired, "shirt", 1, {"colour": "blue"})
    made = post(client, text="yellow kurta")
    brief = update(client, made["session_id"], {"type": "text", "text": "show me shirts"}, memory=False)["brief"]
    assert attributes(brief) == {} and brief["gender"] is None and brief["remembered"] == []


def test_another_shoppers_chats_are_never_used():
    client, wired = make_client(brief=scripted(shirt()))
    past(wired, "shirt", 1, {"colour": "yellow", "gender": "men"}, user="someone-else")
    brief = post(client)["brief"]
    assert brief["remembered"] == [] and attributes(brief) == {}


def test_a_guessed_product_type_gets_no_memory():
    client, wired = make_client(brief=scripted(shirt(question_about="product", question="What kind of clothes?")))
    past(wired, "shirt", 1, {"colour": "yellow", "gender": "men"})
    made = post(client)
    assert made["product"] is None and made["brief"]["remembered"] == []


def test_the_current_chats_own_entries_are_not_used():
    client, wired = make_client(brief=scripted(shirt()))
    moment = stamp(1)
    wired.store.db.data.setdefault("chats", {})[CHAT] = {
        "user_id": DEMO_USER_ID, "title": "this", "timestamps": {"created_at": moment, "updated_at": moment, "last_opened_at": moment},
        "messages": [], "pending": None,
        "products": [{"product": "shirt", "timestamps": {"created_at": moment, "updated_at": moment},
                      "filters": {"colour": "yellow"}, "remembered": [], "session_id": "s-this"}],
    }
    assert post(client)["brief"]["remembered"] == []


# ---------- The vote ----------


def test_a_weight_halves_every_thirty_days():
    assert memory.weight(product({}, 0), NOW) == 1
    assert memory.weight(product({}, 30), NOW) == pytest.approx(0.5)
    assert memory.weight(product({}, 90), NOW) == pytest.approx(0.125)


def test_votes_ignore_case_and_list_order_and_skip_dates_sources_and_wishes():
    entries = [
        product({"colour": "Yellow", "fabric": ["cotton", "linen"], "deadline_date": "2026-10-20", "source": "live",
                 "prefer": {"fit": "slim"}}, 1),
        product({"colour": "yellow", "fabric": ["linen", "cotton"], "min_rating": 4}, 2),
        product({"fabric": "silk"}, 3),
    ]
    found = memory.suggest(entries, NOW)
    assert found == {"colour": "Yellow", "fabric": ["cotton", "linen"], "min_rating": 4}


def test_the_same_day_still_counts_the_newest_chat_most():
    entries = [product({"colour": "red"}, 0.03), product({"colour": "blue"}, 0.01), product({"colour": "black"}, 0.02)]
    shares = dict(memory.suggest(entries, NOW)["colour"].shares)
    assert shares["blue"] > shares["black"] > shares["red"] > 0.1
    assert sum(shares.values()) == pytest.approx(1)


def test_a_mix_keeps_at_most_three_colours():
    entries = [product({"colour": c}, n * 0.01) for n, c in enumerate(["blue", "black", "red", "green"])]
    assert [c for c, _ in memory.suggest(entries, NOW)["colour"].shares] == ["blue", "black", "red"]


def test_booleans_and_numbers_are_not_mixed_up():
    entries = [product({"pockets": True, "min_rating": 1}, 1), product({"pockets": False, "min_rating": True}, 1)]
    assert memory.suggest(entries, NOW) == {}


def test_forget_and_carry_directly():
    brief = ShoppingBrief(
        category="Shirts", gender="men", size="M", remembered=["gender", "size"],
        must_haves=[MustHave(id="m1", attribute="colour", rule="is", value="red")],
    )
    assert memory.forget(brief).gender is None and memory.forget(brief).must_haves == brief.must_haves
    changed = brief.model_copy(update={"gender": "women", "remembered": []})
    assert memory.carry(brief, changed).remembered == ["size"]
