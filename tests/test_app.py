import base64
import json

import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from agent import verify
from agent.app import create_app
from agent.auth import DEMO_USER_ID
from agent.models import MustHave, ShoppingBrief
from tests.fixtures.app.fakes import (
    BENGALURU,
    IMAGE_VECTOR,
    Checker,
    FakeDB,
    backend,
    brief_answer,
    product_doc,
    sneakers,
)

ENV = {"AUTH_MODE": "dev", "ALLOWED_ORIGINS": "http://localhost:3000"}
REQUEST = {"text": "white sneakers with no logo, size 9, under 3000", "pincode": BENGALURU, "language": "en"}
NEVER_SUEDE = {"attribute": "upper_material", "rule": "is_not", "value": "suede"}
PHOTO = base64.b64encode(b"\xff\xd8\xff\xe0 a tiny jpeg").decode()

RESULT_FIELDS = {"product_id", "title", "total_price", "delivery_date", "ticks", "reason", "score"}
CARD_FIELDS = RESULT_FIELDS | {
    "brand", "thumb", "photo", "original", "price", "delivery_fee", "cod", "returns", "rating_avg", "rating_count",
    "demo_data", "sizes_in_stock",
}
DONE_FIELDS = {"checked", "passed", "unclear", "rejected", "duplicates", "tradeoffs", "matches", "almost",
               "not_checkable"}
TICK_FIELDS = {"must_have_id", "label", "status", "evidence"}


def make_client(db: FakeDB | None = None, **backend_options) -> tuple[TestClient, object]:
    wired = backend(db if db is not None else FakeDB(sneakers(4)), **backend_options)
    return TestClient(create_app(wired, env=ENV)), wired


def events(body: str) -> list[tuple[str, dict]]:
    found = []
    for block in body.split("\n\n"):
        lines = [line for line in block.splitlines() if line and not line.startswith(":")]
        if not lines:
            continue
        name = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = "\n".join(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        found.append((name, json.loads(data)))
    return found


def start(client: TestClient, **overrides) -> dict:
    response = client.post("/api/brief", json=REQUEST | overrides)
    assert response.status_code == 200, response.text
    return response.json()


def search(client: TestClient, session_id: str) -> list[tuple[str, dict]]:
    response = client.post("/api/search", json={"session_id": session_id})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return events(response.text)


# ---------- Health ----------


@pytest.mark.parametrize("path", ["/healthz", "/health"])
def test_health_needs_no_sign_in(path):
    client = TestClient(create_app(backend(FakeDB()), env={}))
    assert client.get(path).json() == {"ok": True}


# ---------- Making and editing the checklist ----------


def test_a_brief_uses_the_saved_rules_and_starts_a_session():
    client, wired = make_client()
    client.put("/api/rules", json={"rules": [NEVER_SUEDE]})
    made = start(client)
    brief = made["brief"]
    assert made["question"] is None and brief["size"] == "UK 9" and brief["budget_max_inr"] == 3000
    assert [(m["id"], m["attribute"], m["from_rule"]) for m in brief["must_haves"]] == [
        ("m1", "type", False), ("m2", "colour", False), ("m3", "logo_visible", False), ("r1", "upper_material", True),
    ]
    assert brief["rules_applied"] == ["not suede upper"]
    session = wired.store.db.docs("sessions")[made["session_id"]]
    assert session["user_id"] == DEMO_USER_ID and session["query_text"] == REQUEST["text"]
    assert wired.store.db.docs("users")[DEMO_USER_ID]["pincode"] == BENGALURU


def test_an_unknown_pincode_is_a_friendly_400():
    client, _ = make_client()
    response = client.post("/api/brief", json=REQUEST | {"pincode": "999999"})
    assert response.status_code == 400
    assert response.json() == {"detail": "We couldn't find pincode '999999'. Please check it."}


def test_a_request_needs_words_or_a_photo():
    client, _ = make_client()
    response = client.post("/api/brief", json={"pincode": BENGALURU, "language": "en"})
    assert response.status_code == 400
    assert "Tell us" in response.json()["detail"]


def test_bad_input_is_a_400_with_a_message():
    client, _ = make_client()
    response = client.post("/api/brief", json={"text": "shoes"})
    assert response.status_code == 400
    assert "pincode" in response.json()["detail"]


def test_gemini_failing_is_a_busy_503():
    refused = errors.ClientError(400, {"error": {"code": 400, "message": "blocked", "status": "INVALID_ARGUMENT"}})
    client, wired = make_client(brief=lambda contents: refused)
    response = client.post("/api/brief", json=REQUEST)
    assert response.status_code == 503
    assert "busy" in response.json()["detail"]
    assert wired.brief_client.models.model_names == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


def test_when_flash_times_out_the_checklist_comes_from_flash_lite():
    timed_out = errors.ClientError(499, {"error": {"code": 499, "message": "The operation was cancelled.",
                                                   "status": "CANCELLED"}})
    answers = iter([timed_out, brief_answer()])
    client, wired = make_client(brief=lambda contents: next(answers))
    assert client.post("/api/brief", json=REQUEST).status_code == 200
    assert wired.brief_client.models.model_names == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


def test_a_photo_only_request_is_stored_privately_and_described_for_the_checklist():
    client, wired = make_client()
    made = start(client, text=None, image_base64=PHOTO, image_mime_type="image/jpeg")
    files = wired.store.bucket.files
    [(name, (_, mime))] = files.items()
    assert name.startswith(f"uploads/{DEMO_USER_ID}/") and name.endswith(".jpg") and mime == "image/jpeg"
    assert made["brief"]["reference_image_url"] == f"gs://test-uploads/{name}"
    prompt = wired.brief_client.models.calls[0]
    assert "Something like the Casual Shoes for men in my photo." in prompt
    session = wired.store.db.docs("sessions")[made["session_id"]]
    assert session["photo"] == f"gs://test-uploads/{name}"


def test_a_photo_link_is_used_without_uploading():
    link = "https://images.example.com/shoe.jpg"
    client, wired = make_client(links={link: (b"jpeg", "image/jpeg")})
    made = start(client, image_url=link)
    assert made["brief"]["reference_image_url"] == link and wired.store.bucket.files == {}
    assert "(My photo shows Casual Shoes for men.)" in wired.brief_client.models.calls[0]


def test_a_photo_that_isnt_a_photo_is_refused():
    client, _ = make_client()
    response = client.post("/api/brief", json=REQUEST | {"image_base64": PHOTO, "image_mime_type": "image/gif"})
    assert response.status_code == 400
    response = client.post("/api/brief", json=REQUEST | {"image_base64": "not base64!"})
    assert response.status_code == 400


def test_a_photo_of_something_we_dont_carry_needs_words():
    client, _ = make_client(photo=lambda contents: '{"category": "none", "gender": "unknown"}')
    response = client.post("/api/brief", json={"image_base64": PHOTO, "pincode": BENGALURU, "language": "en"})
    assert response.status_code == 400
    assert "Add a few words" in response.json()["detail"]


def update(client: TestClient, session_id: str, change: dict):
    return client.post("/api/brief/update", json={"session_id": session_id, "change": change})


def test_structured_changes_are_applied_in_code():
    client, wired = make_client()
    client.put("/api/rules", json={"rules": [NEVER_SUEDE]})
    session_id = start(client)["session_id"]
    calls = len(wired.brief_client.models.calls)

    brief = update(client, session_id, {"type": "edit_must_have", "id": "m3", "rule": "is", "value": "subtle"}).json()["brief"]
    assert brief["must_haves"][2] == {"id": "m3", "attribute": "logo_visible", "rule": "is", "value": "subtle",
                                      "from_rule": False}
    brief = update(client, session_id, {"type": "remove_must_have", "id": "m2"}).json()["brief"]
    assert [m["attribute"] for m in brief["must_haves"]] == ["type", "logo_visible", "upper_material"]
    rule = brief["must_haves"][-1]
    brief = update(client, session_id, {"type": "set_rule", "must_have": rule, "enabled": False}).json()["brief"]
    assert [m["attribute"] for m in brief["must_haves"]] == ["type", "logo_visible"]
    brief = update(client, session_id, {"type": "set_rule", "must_have": rule, "enabled": True}).json()["brief"]
    assert brief["must_haves"][-1]["attribute"] == "upper_material"
    brief = update(client, session_id, {"type": "set_budget", "budget_max_inr": 2500}).json()["brief"]
    assert brief["budget_max_inr"] == 2500
    brief = update(client, session_id, {"type": "set_deadline", "deadline_date": "2026-10-17"}).json()["brief"]
    assert brief["deadline_date"] == "2026-10-17"
    brief = update(client, session_id, {"type": "set_pincode", "pincode": "400001"}).json()["brief"]
    assert brief["pincode"] == "400001"
    brief = update(client, session_id, {"type": "set_size", "size": "10"}).json()["brief"]
    assert brief["size"] == "UK 10"

    assert len(wired.brief_client.models.calls) == calls
    saved = wired.store.db.docs("sessions")[session_id]
    assert saved["brief"]["size"] == "UK 10" and saved["disabled_rules"] == []
    assert len(saved["history"]) == 8


@pytest.mark.parametrize(
    "change",
    [
        {"type": "edit_must_have", "id": "m2", "rule": "is", "value": "tartan"},
        {"type": "set_pincode", "pincode": "999999"},
        {"type": "set_size", "size": "47"},
        {"type": "set_deadline", "deadline_date": "2026-10-01"},
        {"type": "set_budget", "budget_max_inr": -5},
        {"type": "teleport"},
    ],
)
def test_bad_changes_are_a_400(change):
    client, _ = make_client()
    session_id = start(client)["session_id"]
    response = update(client, session_id, change)
    assert response.status_code == 400
    assert response.json()["detail"]


def test_free_text_changes_go_to_gemini():
    answers = iter([brief_answer(), brief_answer(must=[("type=sneakers", "is"), ("colour=black", "is")])])
    client, wired = make_client(brief=lambda contents: next(answers))
    session_id = start(client)["session_id"]
    brief = update(client, session_id, {"type": "answer", "question": "Which colour?", "answer": "black"}).json()["brief"]
    assert [(m["attribute"], m["value"]) for m in brief["must_haves"]] == [("type", "sneakers"), ("colour", "black")]
    assert 'Answer to "Which colour?": black' in wired.brief_client.models.calls[1]
    assert wired.store.db.docs("sessions")[session_id]["query_text"].endswith(". black")


def test_someone_elses_session_is_not_found():
    client, wired = make_client()
    session_id = start(client)["session_id"]
    wired.store.db.data["sessions"][session_id]["user_id"] = "someone-else"
    assert update(client, session_id, {"type": "set_budget", "budget_max_inr": 100}).status_code == 404
    assert client.post("/api/search", json={"session_id": session_id}).status_code == 404
    assert client.get(f"/api/products/s1?session_id={session_id}").status_code == 404


# ---------- The search stream ----------


def test_the_stream_sends_events_in_order_with_the_agreed_payloads():
    logo = product_doc("logo", attributes={"logo_visible": {"value": "prominent", "confidence": 0.95, "note": "big"}})
    db = FakeDB([*sneakers(3), logo, product_doc("grey")])
    client, wired = make_client(db, checker=Checker({"grey": {"m2": "fail"}, "logo": {"m3": "fail"}}))
    session_id = start(client)["session_id"]
    found = search(client, session_id)

    names = [name for name, _ in found]
    assert names == ["candidates"] + ["checking"] * 5 + ["verified"] * 5 + ["done"]
    assert found[0][1] == {"count": 5, "dropped": 0}
    for name, payload in found[1:6]:
        assert set(payload) == {"product_id", "title", "thumb"}
    for name, payload in found[6:11]:
        assert set(payload) == {"product_id", "status", "ticks"} and payload["status"] in ("pass", "fail", "unclear")
        assert all(set(tick) == TICK_FIELDS for tick in payload["ticks"])

    done = found[-1][1]
    assert set(done) == DONE_FIELDS
    assert done["checked"] == 5 and done["passed"] == 3 and done["unclear"] == 0
    assert done["rejected"] == {"logo visible": 1, "not white": 1}
    card = done["matches"][0]
    assert set(card) == CARD_FIELDS
    assert set(card["price"]) == {"mrp", "selling", "demo"}
    assert card["cod"] == {"available": True, "fee": 29}
    assert card["returns"] == {"returnable": True, "window_days": 14, "exchange": True}
    assert card["delivery_date"] == "2026-10-16" and card["sizes_in_stock"] == ["UK 8", "UK 9"]
    assert card["ticks"][0]["evidence"]["box_2d"] == [100, 100, 300, 300]

    saved = wired.store.db.docs("sessions")[session_id]
    assert saved["results"]["passed"] == 3 and saved["timings"]["total_ms"] > 0


def test_the_uploaded_photo_is_deleted_after_the_search_and_its_embedding_kept():
    db = FakeDB([product_doc("lookalike", embedding=IMAGE_VECTOR), *sneakers(2)])
    client, wired = make_client(db)
    session_id = start(client, image_base64=PHOTO, image_mime_type="image/jpeg")["session_id"]
    assert len(wired.store.bucket.files) == 1
    found = search(client, session_id)
    assert found[-1][0] == "done"
    assert wired.store.bucket.files == {}
    saved = wired.store.db.docs("sessions")[session_id]
    assert saved["photo"] is None and list(saved["photo_vector"]) == IMAGE_VECTOR

    again = search(client, session_id)
    assert "lookalike" in {card["product_id"] for card in again[-1][1]["matches"]}
    assert wired.services.embedder.images == 1


def test_a_failing_search_ends_with_an_error_event():
    db = FakeDB(sneakers(2))
    client, _ = make_client(db)
    session_id = start(client)["session_id"]
    db.fail_searches = RuntimeError("firestore is down")
    assert search(client, session_id) == [
        ("error", {"message": "Something went wrong while searching. Please try again.", "retryable": True})
    ]
    db.fail_searches = errors.ServerError(503, {"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}})
    [(name, payload)] = search(client, session_id)
    assert name == "error" and payload["retryable"] is True and "busy" in payload["message"]


def test_one_bad_product_doesnt_fail_the_search():
    broken = product_doc("broken", price=None)
    db = FakeDB([*sneakers(2), broken, product_doc("nophoto", images=False), product_doc("pune", city="Pune")])
    client, _ = make_client(db, checker=Checker({"s2": OSError("reset")}))
    found = search(client, start(client)["session_id"])
    done = found[-1]
    assert done[0] == "done"
    assert {c["product_id"] for c in done[1]["matches"]} == {"s1", "pune"}
    assert {c["product_id"] for c in done[1]["almost"]} == {"s2", "nophoto"}


# ---------- Products ----------


def test_a_product_comes_without_its_embedding_and_with_ticks_for_the_session():
    client, _ = make_client(checker=Checker({"s1": {"m3": "fail"}}))
    session_id = start(client)["session_id"]
    plain = client.get("/api/products/s1").json()
    assert plain["id"] == "s1" and plain["articleType"] == "Casual Shoes" and "embedding" not in plain
    assert "ticks" not in plain and plain["usage"] == "Casual" and plain["sizes_in_stock"] == ["UK 8", "UK 9"]

    before = client.get(f"/api/products/s1?session_id={session_id}").json()
    assert [tick["status"] for tick in before["ticks"]] == ["unclear", "unclear", "unclear"]
    search(client, session_id)
    after = client.get(f"/api/products/s1?session_id={session_id}").json()
    assert [tick["status"] for tick in after["ticks"]] == ["pass", "pass", "fail"]
    assert after["ticks"][2]["label"] == "no logo visible"


def test_an_unknown_product_is_not_found():
    client, _ = make_client()
    assert client.get("/api/products/nope").status_code == 404


# ---------- Rules, feedback and the shopper's data ----------


def test_rules_are_checked_against_the_vocabulary_and_numbered():
    client, _ = make_client()
    assert client.get("/api/rules").json() == {"rules": []}
    saved = client.put("/api/rules", json={"rules": [
        {"attribute": "cod", "rule": "is", "value": "yes"},
        {"id": "m7", "attribute": "fabric", "rule": "is_not", "value": "polyester", "from_rule": False},
    ]}).json()
    assert saved == {"rules": [
        {"id": "r1", "attribute": "cod", "rule": "present", "value": True, "from_rule": True},
        {"id": "r2", "attribute": "fabric", "rule": "is_not", "value": "polyester", "from_rule": True},
    ]}
    assert client.get("/api/rules").json() == saved
    bad = client.put("/api/rules", json={"rules": [{"attribute": "fabric", "rule": "is", "value": "kevlar"}]})
    assert bad.status_code == 400
    assert client.put("/api/rules", json={"rules": [{"attribute": "cod", "rule": "present", "value": "x"}]}).status_code == 400


def test_feedback_updates_likes_dislikes_and_style():
    client, wired = make_client()
    session_id = start(client)["session_id"]
    assert client.get("/api/me").json()["has_taste"] is False
    assert client.post("/api/feedback", json={"session_id": session_id, "product_id": "s1", "action": "like"}).json() == {"ok": True}
    assert client.post("/api/feedback", json={"session_id": session_id, "product_id": "s2", "action": "dislike"}).json() == {"ok": True}
    user = wired.store.db.docs("users")[DEMO_USER_ID]
    assert user["likes"] == ["s1"] and user["dislikes"] == ["s2"]
    assert list(user["taste"]) == ["Casual Shoes"] and user["taste"]["Casual Shoes"]["count"] == 1.0
    assert client.get("/api/me").json()["has_taste"] is True
    client.post("/api/feedback", json={"session_id": session_id, "product_id": "s2", "action": "buy"})
    user = wired.store.db.docs("users")[DEMO_USER_ID]
    assert user["likes"] == ["s1", "s2"] and user["dislikes"] == [] and user["taste"]["Casual Shoes"]["count"] == 3.0
    assert len(wired.store.db.docs("sessions")[session_id]["feedback"]) == 3
    missing = client.post("/api/feedback", json={"session_id": session_id, "product_id": "nope", "action": "like"})
    assert missing.status_code == 404

    assert client.delete("/api/me/taste").json() == {"ok": True}
    assert client.get("/api/me").json()["has_taste"] is False


def test_me_shows_and_updates_the_profile():
    client, wired = make_client()
    me = client.get("/api/me").json()
    assert me == {"name": "Demo Shopper", "email": "demo@ecommai.local", "pincode": None, "language": None,
                  "rules": [], "has_taste": False}
    assert wired.store.db.docs("users")[DEMO_USER_ID]["email"] == "demo@ecommai.local"
    updated = client.put("/api/me", json={"pincode": "400001", "language": "hinglish"}).json()
    assert updated["pincode"] == "400001" and updated["language"] == "hinglish"
    assert client.put("/api/me", json={"pincode": "000000"}).status_code == 400


def test_delete_my_data_removes_the_profile_searches_and_photos():
    client, wired = make_client()
    client.put("/api/rules", json={"rules": [NEVER_SUEDE]})
    first = start(client)["session_id"]
    start(client, image_base64=PHOTO, image_mime_type="image/png")
    search(client, first)
    other = wired.store.new_session("someone-else", watch_brief(), query_text="", rules=[], photo=None)
    wired.store.bucket.files["uploads/someone-else/x.jpg"] = (b"x", "image/jpeg")
    assert len(wired.store.db.docs("sessions")) == 3 and len(wired.store.bucket.files) == 2

    assert client.delete("/api/me").json() == {"ok": True}
    assert DEMO_USER_ID not in wired.store.db.docs("users")
    assert list(wired.store.db.docs("sessions")) == [other.id]
    assert list(wired.store.bucket.files) == ["uploads/someone-else/x.jpg"]
    assert client.get("/api/rules").json() == {"rules": []}


def watch_brief() -> ShoppingBrief:
    return ShoppingBrief(category="Watches", must_haves=[MustHave(id="m1", attribute="display", rule="is", value="analog")])


# ---------- Checkout ----------


def test_checkout_sends_the_shopper_to_the_store_with_the_size(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://ecommai.example")
    watch = product_doc("w1", article_type="Watches", sizes={"One size": 4})
    client, _ = make_client(FakeDB([*sneakers(1), watch]))
    response = client.post("/api/checkout", json={"product_id": "s1", "size": "UK 9"})
    assert response.json() == {"redirect_url": "https://ecommai.example/checkout?product=s1&size=UK%209"}
    assert client.post("/api/checkout", json={"product_id": "w1"}).json() == {
        "redirect_url": "https://ecommai.example/checkout?product=w1"
    }
    assert client.post("/api/checkout", json={"product_id": "s1"}).status_code == 400
    assert client.post("/api/checkout", json={"product_id": "s1", "size": "UK 10"}).json()["detail"] == "That size is out of stock."
    assert client.post("/api/checkout", json={"product_id": "s1", "size": "UK 12"}).status_code == 400
    assert client.post("/api/checkout", json={"product_id": "nope", "size": "UK 9"}).status_code == 404


def test_saved_checks_are_reused_between_searches():
    checker = Checker()
    client, wired = make_client(FakeDB(sneakers(2)), checker=checker)
    session_id = start(client)["session_id"]
    search(client, session_id)
    search(client, session_id)
    assert checker.calls == 2
    assert isinstance(wired.services.check_cache, verify.MemoryCache) and len(wired.services.check_cache.docs) == 6
