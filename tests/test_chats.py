from datetime import date, datetime, timedelta, timezone

import pytest

from agent import chats, sessions
from agent.auth import DEMO_USER_ID
from agent.models import MustHave, NiceToHave, ShoppingBrief
from data_prep import schemas
from tests.fixtures.app.fakes import BENGALURU, FakeDB, brief_answer
from tests.test_app import make_client

CHAT = "c_" + "a" * 32
OTHER_CHAT = "c_" + "b" * 32
START = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
KURTA = [("colour=maroon", "is"), ("fabric=silk", "is")]
REQUEST = {"text": "maroon silk kurta", "pincode": BENGALURU, "language": "en", "source": "catalog", "ask": False}


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now

    def after(self, **delta):
        self.now += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(sessions, "now_utc", clock)
    return clock


def kurta(**options):
    return brief_answer("Kurtas", gender="women", must=options.pop("must", KURTA), size="M", budget=2500, **options)


def watch():
    return brief_answer("Watches", gender="men", must=[("display=analog", "is")], size="")


def scripted(*answers):
    queue = iter(answers)
    return lambda contents: next(queue)


def must(attribute, rule, value, must_id="m1", from_rule=False):
    return MustHave(id=must_id, attribute=attribute, rule=rule, value=value, from_rule=from_rule)


def brief_of(category="Kurtas", **fields):
    return ShoppingBrief(category=category, **fields)


def post_brief(client, chat_id=CHAT, **extra):
    response = client.post("/api/brief", json=REQUEST | ({"chat_id": chat_id} if chat_id else {}) | extra)
    assert response.status_code == 200, response.text
    return response.json()


def change(client, session_id, change, chat_id=CHAT):
    body = {"session_id": session_id, "change": change, "ask": False} | ({"chat_id": chat_id} if chat_id else {})
    response = client.post("/api/brief/update", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def doc(wired, chat_id=CHAT):
    return wired.store.db.docs("chats")[chat_id]


def entry(wired, name, chat_id=CHAT):
    return next(p for p in doc(wired, chat_id)["products"] if p["product"] == name)


def message(index, role="user", text="hello", **extra):
    return {"id": f"m-{index:02d}", "role": role, "text": text} | extra


def save(client, messages, pending=None, chat_id=CHAT):
    return client.put(f"/api/chats/{chat_id}/messages", json={"messages": messages, "pending": pending})


# ---------- Names ----------


def test_every_catalogue_type_has_a_plain_name():
    assert set(chats.PRODUCT_NAMES) == set(schemas.ARTICLE_TYPE_TO_GROUP)
    assert len(chats.PRODUCT_NAMES) == 16
    assert chats.PRODUCT_NAMES["Tshirts"] == "t-shirt" and chats.PRODUCT_NAMES["Casual Shoes"] == "casual shoes"


# ---------- Filters ----------


def test_filters_show_every_rule_in_the_agreed_order():
    brief = brief_of(
        gender="women", size="M", budget_max_inr=2500, min_rating=4.0, deadline_date=date(2026, 10, 23),
        must_haves=[
            must("fabric", "is", "silk", "m1"),
            must("sheerness", "is_not", "see-through", "m2"),
            must("colour", "is", "maroon", "m3"),
            must("pockets", "present", True, "m4"),
            must("neckline", "absent", False, "m5"),
        ],
        nice_to_haves=[NiceToHave(attribute="neckline", value="mandarin")],
    )
    filters = chats.chat_filters(brief, "catalog")
    assert filters == {
        "colour": "maroon", "gender": "women", "size": "M", "fabric": "silk", "sheerness": {"not": "see-through"},
        "pockets": True, "neckline": False, "budget_max_inr": 2500, "min_rating": 4, "deadline_date": "2026-10-23",
        "prefer": {"neckline": "mandarin"}, "source": "catalog",
    }
    assert list(filters) == ["colour", "gender", "size", "fabric", "sheerness", "pockets", "neckline", "budget_max_inr",
                             "min_rating", "deadline_date", "prefer", "source"]


def test_number_rules_and_several_must_haves_on_one_attribute():
    brief = brief_of(must_haves=[
        must("count", "at_most", 2.0, "m1"), must("count", "at_least", 1.5, "m2"), must("fabric", "is", "silk", "m3"),
        must("fabric", "is_not", "linen", "m4"),
    ])
    filters = chats.chat_filters(brief, "live")
    assert filters["count"] == [{"at_most": 2}, {"at_least": 1.5}]
    assert filters["fabric"] == ["silk", {"not": "linen"}]
    assert filters["source"] == "live"


def test_unset_fields_are_left_out_and_personal_rules_too():
    brief = brief_of(must_haves=[must("upper_material", "is_not", "suede", "m1", from_rule=True)])
    assert chats.chat_filters(brief, "catalog") == {"source": "catalog"}


def test_a_bags_size_is_its_own_detail_not_the_brief_size():
    brief = brief_of("Handbags", gender="women", size="M", must_haves=[must("size", "is", "large"), must("closure", "is", "zip", "m2")])
    filters = chats.chat_filters(brief, "catalog")
    assert filters["size"] == "large" and list(filters) == ["gender", "size", "closure", "source"]
    assert "size" not in chats.chat_filters(brief_of("Handbags", size="M"), "catalog")


def test_timestamps_are_utc_text_ending_in_z():
    moment = datetime(2026, 10, 13, 10, 0, 1, 500000, tzinfo=timezone.utc)
    stamps = chats.ProductTimestamps(created_at=moment, updated_at=moment)
    assert stamps.model_dump(mode="json")["created_at"] == "2026-10-13T10:00:01.500Z"


# ---------- /api/brief ----------


def test_a_brief_with_a_chat_saves_the_product_with_its_filters(clock):
    client, wired = make_client(brief=scripted(kurta()))
    response = post_brief(client)
    assert response["product"] == "kurta"
    saved = doc(wired)
    assert saved["user_id"] == DEMO_USER_ID
    assert [p["product"] for p in saved["products"]] == ["kurta"]
    assert entry(wired, "kurta")["session_id"] == response["session_id"]
    assert entry(wired, "kurta")["filters"] == {
        "colour": "maroon", "gender": "women", "size": "M", "fabric": "silk", "budget_max_inr": 2500, "source": "catalog",
    }
    assert saved["timestamps"] == {"created_at": START, "updated_at": START, "last_opened_at": START}
    assert entry(wired, "kurta")["timestamps"] == {"created_at": START, "updated_at": START}
    assert set(saved) == {"user_id", "title", "timestamps", "messages", "pending", "products"}


def test_messages_saved_while_the_brief_is_made_are_kept(clock):
    wired = None

    def answer(contents):
        # The browser saves the first message while Gemini is still reading it.
        first = chats.ChatMessage(id="m-01", role="user", text="maroon silk kurta")
        wired.store.save_chat_messages(DEMO_USER_ID, CHAT, None, [first], None)
        return kurta()

    client, wired = make_client(brief=answer)
    post_brief(client)
    saved = doc(wired)
    assert [m["text"] for m in saved["messages"]] == ["maroon silk kurta"]
    assert saved["title"] == "maroon silk kurta"
    assert [p["product"] for p in saved["products"]] == ["kurta"]


def test_the_product_name_comes_back_without_a_chat():
    client, wired = make_client(brief=scripted(kurta()))
    assert post_brief(client, chat_id=None)["product"] == "kurta"
    assert "chats" not in wired.store.db.data or not wired.store.db.docs("chats")


def test_a_guessed_product_type_is_never_saved(clock):
    guess = brief_answer("Tops", must=[("colour=red", "is")], size="M", question_about="product", question="Which product?")
    client, wired = make_client(brief=scripted(guess))
    response = post_brief(client, ask=True)
    assert response["product"] is None
    assert doc(wired)["products"] == []


@pytest.mark.parametrize("bad", ["x", "c_ABC", "c_" + "a" * 31, "c_" + "g" * 32, "../" + "a" * 32])
def test_a_badly_formed_chat_id_is_not_found(bad):
    client, _ = make_client(brief=scripted(kurta()))
    assert client.post("/api/brief", json=REQUEST | {"chat_id": bad}).status_code == 404
    assert client.post(f"/api/chats/{bad}/open", json={}).status_code == 404


def test_someone_elses_chat_is_not_found(clock):
    client, wired = make_client(brief=scripted(kurta(), kurta()))
    post_brief(client)
    doc(wired)["user_id"] = "someone-else"
    response = client.post("/api/brief", json=REQUEST | {"chat_id": CHAT})
    assert response.status_code == 404 and response.json()["detail"] == "We couldn't find that chat."
    assert save(client, [message(1)]).status_code == 404
    assert client.post(f"/api/chats/{CHAT}/open", json={}).status_code == 404
    session_id = next(iter(wired.store.db.docs("sessions")))
    body = {"session_id": session_id, "change": {"type": "set_budget", "budget_max_inr": 900}, "chat_id": CHAT}
    assert client.post("/api/brief/update", json=body).status_code == 404
    assert client.get("/api/chats").json() == {"chats": []}


# ---------- /api/brief/update ----------


def test_updates_without_a_chat_behave_as_before():
    client, wired = make_client(brief=scripted(kurta()))
    first = post_brief(client, chat_id=None)
    result = change(client, first["session_id"], {"type": "set_budget", "budget_max_inr": 900}, chat_id=None)
    assert result["session_id"] == first["session_id"] and result["product"] == "kurta"
    assert result["brief"]["budget_max_inr"] == 900
    assert not wired.store.db.docs("chats")


def test_an_edit_updates_the_filters_and_moves_only_that_products_time(clock):
    client, wired = make_client(brief=scripted(kurta(), watch()))
    first = post_brief(client)
    clock.after(minutes=5)
    second = post_brief(client, text="analog watch")
    assert second["product"] == "watch"
    clock.after(minutes=5)
    edited = change(client, first["session_id"], {"type": "set_budget", "budget_max_inr": 1800})
    assert edited["session_id"] == first["session_id"]
    assert entry(wired, "kurta")["filters"]["budget_max_inr"] == 1800
    assert entry(wired, "kurta")["timestamps"]["updated_at"] == START + timedelta(minutes=10)
    assert entry(wired, "watch")["timestamps"]["updated_at"] == START + timedelta(minutes=5)
    assert entry(wired, "kurta")["timestamps"]["created_at"] == START
    assert doc(wired)["timestamps"]["updated_at"] == START + timedelta(minutes=10)


@pytest.mark.parametrize(
    "edit",
    [
        {"type": "set_budget", "budget_max_inr": 1800},
        {"type": "set_min_rating", "min_rating": 4},
        {"type": "remove_must_have", "id": "m2"},
        {"type": "set_source", "source": "live"},
    ],
)
def test_every_kind_of_chat_edit_moves_the_product_time(clock, edit):
    client, wired = make_client(brief=scripted(kurta()))
    first = post_brief(client)
    clock.after(hours=1)
    change(client, first["session_id"], edit)
    assert entry(wired, "kurta")["timestamps"]["updated_at"] == START + timedelta(hours=1)


def test_a_typed_change_to_a_new_product_forks_a_new_search(clock):
    client, wired = make_client(brief=scripted(kurta(), watch()))
    first = post_brief(client)
    before = wired.store.db.docs("sessions")[first["session_id"]].copy()
    clock.after(hours=1)
    forked = change(client, first["session_id"], {"type": "text", "text": "an analog watch instead"})
    assert forked["session_id"] != first["session_id"] and forked["product"] == "watch"
    assert wired.store.db.docs("sessions")[first["session_id"]] == before
    new = wired.store.db.docs("sessions")[forked["session_id"]]
    assert new["query_text"] == "an analog watch instead"
    assert new["history"] == [{"change": {"type": "text", "text": "an analog watch instead"}}]
    assert new["source"] == "catalog" and new["brief"]["category"] == "Watches"
    assert entry(wired, "watch")["session_id"] == forked["session_id"]
    assert entry(wired, "kurta")["timestamps"]["updated_at"] == START
    assert entry(wired, "watch")["timestamps"]["created_at"] == START + timedelta(hours=1)


def test_switching_back_changes_the_existing_search_of_that_product(clock):
    back = brief_answer("Kurtas", gender="women", must=KURTA, size="M", budget=1800)
    client, wired = make_client(brief=scripted(kurta(), watch(), back, back))
    first = post_brief(client)
    forked = change(client, first["session_id"], {"type": "text", "text": "a watch"})
    clock.after(hours=2)
    again = change(client, forked["session_id"], {"type": "text", "text": "back to the kurta, under 1800"})
    assert again["session_id"] == first["session_id"] and again["product"] == "kurta"
    assert again["brief"]["budget_max_inr"] == 1800
    assert entry(wired, "kurta")["filters"]["fabric"] == "silk"
    assert entry(wired, "kurta")["timestamps"]["updated_at"] == START + timedelta(hours=2)
    assert entry(wired, "watch")["timestamps"]["updated_at"] == START
    assert wired.store.db.docs("sessions")[first["session_id"]]["query_text"].endswith("back to the kurta, under 1800")
    assert len(wired.store.db.docs("sessions")) == 2


def test_a_guessed_old_brief_is_edited_in_place(clock):
    guess = brief_answer("Tops", must=[("colour=red", "is")], size="M", question_about="product", question="Which?")
    client, wired = make_client(brief=scripted(guess, watch()))
    first = post_brief(client, ask=True)
    result = change(client, first["session_id"], {"type": "text", "text": "a watch"})
    assert result["session_id"] == first["session_id"] and result["product"] == "watch"
    assert [p["product"] for p in doc(wired)["products"]] == ["watch"]


# ---------- Searching never moves a product's time ----------


def test_results_and_searches_leave_the_product_time_alone(clock):
    client, wired = make_client(brief=scripted(kurta()))
    first = post_brief(client)
    clock.after(hours=3)
    assert client.post("/api/search", json={"session_id": first["session_id"]}).status_code == 200
    assert wired.store.db.docs("sessions")[first["session_id"]]["updated_at"] == START + timedelta(hours=3)
    assert entry(wired, "kurta")["timestamps"]["updated_at"] == START
    assert doc(wired)["timestamps"]["updated_at"] == START


# ---------- Saving messages ----------


def test_saving_messages_sets_the_title_and_never_touches_products(clock):
    client, wired = make_client(brief=scripted(kurta()))
    post_brief(client)
    products = doc(wired)["products"]
    clock.after(minutes=1)
    long_text = "Maroon silk kurta for Diwali " + "really nice " * 12
    messages = [message(1, text=long_text), message(2, "ai", "Checked 14 kurtas.", product="kurta", picks=["Kurtas"])]
    assert save(client, messages, pending="maroon").json() == {"ok": True}
    saved = doc(wired)
    assert saved["title"].startswith("Maroon silk kurta for Diwali") and saved["title"].endswith("…")
    assert len(saved["title"]) <= chats.MAX_TITLE + 1
    assert saved["products"] == products
    assert saved["pending"] == "maroon" and saved["messages"][1]["product"] == "kurta"
    assert "error" not in saved["messages"][0]
    assert saved["timestamps"]["updated_at"] == START + timedelta(minutes=1)
    assert entry(wired, "kurta")["timestamps"]["updated_at"] == START


def test_the_title_is_set_once(clock):
    client, wired = make_client()
    save(client, [message(1, text="first thing")])
    save(client, [message(1, text="first thing"), message(2, "ai", "ok")])
    assert doc(wired)["title"] == "first thing"
    save(client, [message(1, text="something else")])
    assert doc(wired)["title"] == "first thing"


def test_saving_the_same_messages_does_not_move_the_chat_time(clock):
    client, wired = make_client()
    messages = [message(1, text="hi"), message(2, "ai", "hello")]
    save(client, messages)
    clock.after(hours=1)
    save(client, messages)
    assert doc(wired)["timestamps"]["updated_at"] == START
    clock.after(hours=1)
    save(client, messages, pending="x")
    assert doc(wired)["timestamps"]["updated_at"] == START + timedelta(hours=2)


def test_saving_creates_the_chat_with_equal_times(clock):
    client, wired = make_client()
    save(client, [message(1)])
    assert doc(wired)["timestamps"] == {"created_at": START, "updated_at": START, "last_opened_at": START}


def test_browser_timestamps_are_rejected_or_ignored(clock):
    client, wired = make_client()
    save(client, [message(1)])
    clock.after(hours=1)
    assert save(client, [message(1, created_at="2020-01-01T00:00:00Z")]).status_code == 400
    body = {"messages": [message(1)], "pending": None, "timestamps": {"updated_at": "2020-01-01T00:00:00Z"}}
    assert client.put(f"/api/chats/{CHAT}/messages", json=body).status_code == 400
    brief_body = REQUEST | {"chat_id": CHAT, "updated_at": "2020-01-01T00:00:00Z", "timestamps": {"created_at": "x"}}
    clock.now = START
    client2, wired2 = make_client(brief=scripted(kurta()))
    assert client2.post("/api/brief", json=brief_body).status_code == 200
    assert doc(wired2)["timestamps"]["created_at"] == START
    assert doc(wired)["timestamps"]["updated_at"] == START


@pytest.mark.parametrize(
    "messages",
    [
        [message(1, text="x" * 4001)],
        [message(1) | {"id": "i" * 41}],
        [message(1) | {"role": "robot"}],
        [message(n) for n in range(201)],
    ],
)
def test_message_limits(messages):
    client, _ = make_client()
    assert save(client, messages).status_code == 400


# ---------- Opening ----------


def opened(client, chat_id=CHAT, **body):
    response = client.post(f"/api/chats/{chat_id}/open", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_opening_a_missing_chat_is_not_found():
    client, _ = make_client()
    assert client.post(f"/api/chats/{CHAT}/open", json={}).status_code == 404


def test_opening_gives_fresh_catalogue_results_and_stamps_only_when_it_was_opened(clock):
    client, wired = make_client(brief=scripted(kurta()))
    first = post_brief(client)
    client.post("/api/search", json={"session_id": first["session_id"]})
    save(client, [message(1, text="hi")])
    before = doc(wired)
    clock.after(hours=2)
    result = opened(client)
    assert result["welcome_back"] is False
    assert result["active"]["session_id"] == first["session_id"] and result["active"]["product"] == "kurta"
    assert result["active"]["source"] == "catalog" and result["active"]["query_text"] == "maroon silk kurta"
    assert result["active"]["results"] == wired.store.db.docs("sessions")[first["session_id"]]["results"]
    assert result["active"]["results"]["checked"] >= 0
    assert result["chat"]["messages"] == [message(1, text="hi")]
    assert result["chat"]["timestamps"]["last_opened_at"] == "2026-10-13T12:00:00.000Z"
    after = doc(wired)
    assert after["timestamps"]["last_opened_at"] == START + timedelta(hours=2)
    assert after["timestamps"]["updated_at"] == before["timestamps"]["updated_at"]
    assert after["messages"] == before["messages"] and after["products"] == before["products"]


def test_stale_results_are_left_out(clock):
    client, wired = make_client(brief=scripted(kurta()))
    first = post_brief(client)
    client.post("/api/search", json={"session_id": first["session_id"]})
    clock.after(hours=23)
    assert opened(client)["active"]["results"] is not None
    clock.after(hours=1, minutes=1)
    stale = opened(client)
    assert stale["welcome_back"] is False and stale["active"]["results"] is None


def test_welcome_back_after_a_day_away_leaves_results_out(clock):
    client, wired = make_client(brief=scripted(kurta()))
    first = post_brief(client)
    client.post("/api/search", json={"session_id": first["session_id"]})
    session = wired.store.db.docs("sessions")[first["session_id"]]
    clock.after(hours=25)
    session["updated_at"] = clock()
    result = opened(client)
    assert result["welcome_back"] is True and result["active"]["results"] is None
    clock.after(hours=1)
    assert opened(client)["welcome_back"] is False


def test_live_results_are_never_returned(clock):
    client, wired = make_client(brief=scripted(kurta()))
    first = post_brief(client, source="live")
    wired.store.db.docs("sessions")[first["session_id"]]["results"] = {"checked": 1}
    result = opened(client)
    assert result["active"]["source"] == "live" and result["active"]["results"] is None


def test_opening_picks_the_product_asked_for_or_the_latest(clock):
    client, wired = make_client(brief=scripted(kurta(), watch()))
    first = post_brief(client)
    clock.after(minutes=1)
    second = post_brief(client, text="a watch")
    assert opened(client)["active"]["session_id"] == second["session_id"]
    assert opened(client, product="kurta")["active"]["session_id"] == first["session_id"]
    assert opened(client, product="handbag")["active"]["session_id"] == second["session_id"]
    assert entry(wired, "kurta")["timestamps"]["updated_at"] == START
    assert entry(wired, "watch")["timestamps"]["updated_at"] == START + timedelta(minutes=1)


def test_a_gone_session_gives_no_active_product(clock):
    client, wired = make_client(brief=scripted(kurta()))
    first = post_brief(client)
    del wired.store.db.data["sessions"][first["session_id"]]
    assert opened(client)["active"] is None


def test_a_chat_with_no_products_opens_with_no_active_product(clock):
    client, _ = make_client()
    save(client, [message(1)])
    assert opened(client)["active"] is None


# ---------- Listing ----------


def test_the_list_is_newest_first_and_only_the_shoppers_own(clock):
    client, wired = make_client()
    ids = [f"c_{n:032x}" for n in range(3)]
    for chat_id in ids:
        save(client, [message(1, text=f"chat {chat_id[-1]}")], chat_id=chat_id)
        clock.after(minutes=1)
    save(client, [message(1, text="chat 0"), message(2, "ai", "more")], chat_id=ids[0])
    doc(wired, ids[1])["user_id"] = "someone-else"
    listed = client.get("/api/chats").json()["chats"]
    assert [c["chat_id"] for c in listed] == [ids[0], ids[2]]
    assert set(listed[0]) == {"chat_id", "title", "timestamps", "products"}
    assert listed[0]["timestamps"]["created_at"] == "2026-10-13T10:00:00.000Z"
    assert ids[1] in wired.store.db.docs("chats")


def test_the_list_names_the_products(clock):
    client, _ = make_client(brief=scripted(kurta(), watch()))
    post_brief(client)
    post_brief(client, text="a watch")
    assert client.get("/api/chats").json()["chats"][0]["products"] == ["kurta", "watch"]


def test_old_chats_and_chats_past_the_newest_thirty_are_deleted(clock):
    client, wired = make_client()
    old = [f"c_{n:032x}" for n in range(2)]
    for chat_id in old:
        save(client, [message(1)], chat_id=chat_id)
    clock.after(days=91)
    many = [f"c_{n:032x}" for n in range(100, 134)]
    for chat_id in many:
        save(client, [message(1)], chat_id=chat_id)
        clock.after(minutes=1)
    listed = client.get("/api/chats").json()["chats"]
    assert [c["chat_id"] for c in listed] == many[::-1][:30]
    assert set(wired.store.db.docs("chats")) == set(many[4:])


def test_a_chat_just_inside_ninety_days_stays(clock):
    client, wired = make_client()
    save(client, [message(1)])
    clock.after(days=89, hours=23)
    assert len(client.get("/api/chats").json()["chats"]) == 1
    clock.after(hours=2)
    assert client.get("/api/chats").json()["chats"] == []
    assert not wired.store.db.docs("chats")


def test_the_list_does_not_ask_firestore_to_sort(clock):
    client, wired = make_client()
    save(client, [message(1)])
    seen = []
    original = wired.store.db.collection

    def watching(name):
        query = original(name)
        seen.append(hasattr(query, "order_by"))
        return query

    wired.store.db.collection = watching
    client.get("/api/chats")
    assert not any(seen)


# ---------- Deleting the shopper ----------


def test_deleting_the_shopper_deletes_their_chats(clock):
    client, wired = make_client(brief=scripted(kurta()))
    post_brief(client)
    save(client, [message(1)], chat_id=OTHER_CHAT)
    wired.store.db.data["chats"]["c_" + "c" * 32] = {**doc(wired), "user_id": "someone-else"}
    assert client.delete("/api/me").json() == {"ok": True}
    assert list(wired.store.db.docs("chats")) == ["c_" + "c" * 32]
    assert not wired.store.db.docs("sessions")


def test_a_chat_doc_is_only_the_agreed_fields(clock):
    client, wired = make_client(brief=scripted(kurta()))
    post_brief(client)
    save(client, [message(1)])
    text = repr(doc(wired))
    assert "expires_at" not in text and "results_at" not in text
