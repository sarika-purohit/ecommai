from datetime import date

import pytest
from google.adk.agents import LlmAgent
from google.genai import errors

from agent import agent as adk
from agent import workflow
from agent.models import MustHave, NiceToHave, Product, ShoppingBrief
from agent.retrieve import Hit
from agent.sessions import Store
from agent.workflow import (
    AddMustHave,
    ChangeProblem,
    EditMustHave,
    PhotoKind,
    PhotoProblem,
    PhotoSearch,
    RemoveMustHave,
    RemoveNiceToHave,
    SetBudget,
    SetDeadline,
    SetPincode,
    SetRule,
    SetSize,
    TextChange,
)
from tests.fixtures.app.fakes import (
    BENGALURU,
    IMAGE_VECTOR,
    TUESDAY_10AM,
    Checker,
    FakeClient,
    FakeDB,
    brief_answer,
    near_text,
    product_doc,
    services,
    sneakers,
)

TUESDAY = date(2026, 10, 13)
SNEAKERS = MustHave(id="m1", attribute="type", rule="is", value="sneakers")
WHITE = MustHave(id="m2", attribute="colour", rule="is", value="white")
NO_LOGO = MustHave(id="m3", attribute="logo_visible", rule="absent", value=True)
NEVER_SUEDE = MustHave(id="r1", attribute="upper_material", rule="is_not", value="suede", from_rule=True)
COD = MustHave(id="r2", attribute="cod", rule="present", value=True, from_rule=True)


def shoes_brief(**fields) -> ShoppingBrief:
    base = {
        "category": "Casual Shoes",
        "gender": "men",
        "must_haves": [SNEAKERS, WHITE, NO_LOGO],
        "budget_max_inr": 3000,
        "pincode": BENGALURU,
        "size": "UK 9",
    }
    return ShoppingBrief(**(base | fields))


def run(db: FakeDB, brief: ShoppingBrief | None = None, checker: Checker | None = None, **kwargs):
    seen: list[workflow.Event] = []
    outcome = workflow.search(
        brief or shoes_brief(), query_text="white sneakers with no logo", services=kwargs.pop("svc", None)
        or services(db, checker), emit=seen.append, now=TUESDAY_10AM, **kwargs,
    )
    return outcome, seen


def kinds(events: list[workflow.Event]) -> list[str]:
    return [event.kind for event in events]


def hit(product_id: str, *, over_budget: bool = False, late: bool = False) -> Hit:
    product = Product.model_validate(product_doc(product_id))
    return Hit(product=product, distance=0.1, total_price=1999, over_budget=over_budget, late=late)


# ---------- Choosing what to check ----------


def test_the_careful_check_takes_the_best_within_limits_plus_four_over():
    pool = [hit(f"w{n}") for n in range(10)] + [hit(f"o{n}", over_budget=True) for n in range(6)]
    chosen = [h.product.id for h in workflow.choose_for_check(pool)]
    assert chosen == [f"w{n}" for n in range(8)] + [f"o{n}" for n in range(4)]


def test_over_limit_products_fill_the_places_when_few_are_within_limits():
    pool = [hit("o0", late=True), hit("w0"), hit("o1", over_budget=True), hit("w1")] + [
        hit(f"o{n}", over_budget=True) for n in range(2, 20)
    ]
    chosen = [h.product.id for h in workflow.choose_for_check(pool)]
    assert len(chosen) == 12
    assert chosen[:4] == ["o0", "w0", "o1", "w1"]


# ---------- The search ----------


LOGO = {"logo_visible": {"value": "prominent", "confidence": 0.95, "note": "big logo"}}
BLACK = {"colour": {"value": "black", "confidence": 1.0, "source": "catalogue"}}


def test_events_arrive_in_order_and_the_summary_adds_up():
    logo = product_doc("logo", attributes=LOGO)
    pricey = product_doc("pricey", selling=3400, embedding=near_text(0.9))
    black = product_doc("black", attributes={**LOGO, **BLACK})
    db = FakeDB([*sneakers(4), logo, pricey, black])
    checker = Checker({"s4": {"m2": "fail"}, "logo": {"m3": "fail"}})
    outcome, seen = run(db, checker=checker)

    names = kinds(seen)
    assert names[0] == "candidates" and names[-1] == "done"
    assert names == ["candidates"] + ["checking"] * 6 + ["verified"] * 6 + ["done"]
    assert seen[0].count == 6 and seen[0].dropped == 1
    assert [e.product_id for e in seen if e.kind == "checking"] == ["s1", "s2", "s3", "s4", "pricey", "logo"]
    verified = {e.product_id: e.status for e in seen if e.kind == "verified"}
    assert verified == {"s1": "pass", "s2": "pass", "s3": "pass", "s4": "fail", "pricey": "pass", "logo": "fail"}

    done = outcome.done
    assert [card.product_id for card in done.matches] == ["s1", "s2", "s3"]
    assert done.rejected == {"not white": 2, "logo visible": 1, "over budget": 1}
    assert done.checked == 6 and done.passed == 3 and done.unclear == 0
    card = done.matches[0]
    assert card.thumb.endswith("/thumb/s1.jpg") and card.photo.endswith("/gemini/s1.jpg")
    assert card.sizes_in_stock == ["UK 8", "UK 9"]
    assert card.total_price == 1999 and card.delivery_date == date(2026, 10, 16)
    assert [tick.label for tick in card.ticks] == ["sneakers", "white", "no logo visible"]
    assert outcome.timings["total_ms"] >= outcome.timings["check_ms"]


def test_few_matches_bring_trade_offs_from_checked_over_budget_products():
    db = FakeDB([product_doc("only"), product_doc("pricey1", selling=3400), product_doc("pricey2", selling=3500)])
    outcome, _ = run(db)
    assert outcome.done.passed == 1
    assert [t.model_dump() for t in outcome.done.tradeoffs] == [{"relax": "budget", "to": 3600, "matches": 3}]


def test_when_the_quick_check_drops_everything_one_away_products_are_checked_for_trade_offs():
    db = FakeDB([product_doc(f"logo{n}", attributes=LOGO) for n in range(3)] + [
        product_doc("black", attributes={**LOGO, **BLACK}),
        product_doc("plain_black", attributes=BLACK),
    ])
    checker = Checker({f"logo{n}": {"m3": "fail"} for n in range(3)} | {"plain_black": {"m2": "fail"}})
    outcome, seen = run(db, checker=checker)
    assert (seen[0].count, seen[0].dropped) == (4, 1)
    assert sorted(e.product_id for e in seen if e.kind == "checking") == ["logo0", "logo1", "logo2", "plain_black"]
    done = outcome.done
    assert done.passed == 0 and done.rejected == {"logo visible": 3, "not white": 2}
    assert [t.model_dump() for t in done.tradeoffs] == [
        {"relax": "must_have:logo_visible", "to": None, "matches": 3},
        {"relax": "must_have:colour", "to": None, "matches": 1},
    ]


def test_a_one_away_product_never_becomes_a_match_against_its_saved_detail():
    db = FakeDB([product_doc("blue_white", attributes={"colour": {"value": "blue", "confidence": 1.0,
                                                                  "source": "catalogue"}})])
    outcome, seen = run(db)
    [verified] = [e for e in seen if e.kind == "verified"]
    assert verified.status == "fail"
    tick = verified.ticks[1]
    assert (tick.label, tick.status, tick.evidence.type) == ("white", "fail", "attribute")
    assert tick.evidence.note == "The saved details say colour: blue."
    assert outcome.done.matches == [] and outcome.done.rejected == {"not white": 1}
    assert [t.model_dump() for t in outcome.done.tradeoffs] == [{"relax": "must_have:colour", "to": None, "matches": 1}]


def test_one_away_only_fills_free_places():
    brief = shoes_brief()
    logo = hit("logo")
    logo.product.attributes = Product.model_validate(product_doc("logo", attributes=LOGO)).attributes
    assert workflow.one_away(brief, [(logo, "logo visible")], 0) == []
    assert workflow.one_away(brief, [(logo, "logo visible")], 3) == [logo]


def test_one_products_bad_data_never_fails_the_search():
    unreadable = product_doc("broken")
    del unreadable["gender"]
    db = FakeDB([
        *sneakers(2),
        product_doc("nophoto", images=False),
        product_doc("pune", city="Pune"),
        product_doc("boom"),
        unreadable,
    ])
    checker = Checker({"boom": OSError("connection reset")})
    outcome, seen = run(db, checker=checker)

    assert kinds(seen)[-1] == "done"
    statuses = {e.product_id: e.status for e in seen if e.kind == "verified"}
    assert statuses["nophoto"] == "unclear" and statuses["boom"] == "unclear"
    assert "broken" not in statuses
    shown = {card.product_id for card in outcome.done.matches + outcome.done.almost}
    assert {"s1", "s2"} <= {card.product_id for card in outcome.done.matches}
    assert "pune" in shown
    assert next(c for c in outcome.done.matches if c.product_id == "pune").delivery_date is None


def test_a_card_that_cant_be_built_is_left_out(monkeypatch):
    real = workflow.result_card

    def flaky(result, product):
        if product.id == "s2":
            raise ValueError("bad image link")
        return real(result, product)

    monkeypatch.setattr(workflow, "result_card", flaky)
    outcome, seen = run(FakeDB(sneakers(3)))
    assert [card.product_id for card in outcome.done.matches] == ["s1", "s3"]
    assert kinds(seen)[-1] == "done"


def test_saved_checks_make_the_second_search_free():
    db = FakeDB(sneakers(3))
    checker = Checker()
    svc = services(db, checker)
    run(db, svc=svc)
    first = checker.calls
    _, seen = run(db, svc=svc)
    assert first == 3 and checker.calls == first
    assert [e.status for e in seen if e.kind == "verified"] == ["pass"] * 3


def test_a_checklist_without_must_haves_shows_matches():
    outcome, seen = run(FakeDB(sneakers(2)), shoes_brief(must_haves=[]))
    assert [e.status for e in seen if e.kind == "verified"] == ["pass", "pass"]
    assert outcome.done.passed == 2


def test_no_products_ends_with_an_empty_summary():
    outcome, seen = run(FakeDB([]))
    assert kinds(seen) == ["candidates", "done"]
    assert outcome.done.matches == [] and outcome.done.tradeoffs == []


def test_friendly_errors():
    busy = errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
    assert workflow.friendly_error(busy).retryable is True
    assert "busy" in workflow.friendly_error(busy).message
    assert workflow.friendly_error(RuntimeError("x")).message == "Something went wrong while searching. Please try again."
    assert workflow.friendly_error(ValueError("unknown category 'Sofas'")).retryable is False


# ---------- Photos ----------


def test_the_photo_is_embedded_once_and_reused_without_reading_it_again():
    db = FakeDB([product_doc("near_photo", embedding=IMAGE_VECTOR), *sneakers(2)])
    svc = services(db)
    reads: list[str] = []

    def read(link: str) -> tuple[bytes, str]:
        reads.append(link)
        return b"jpeg-bytes", "image/jpeg"

    photo = PhotoSearch("gs://test-uploads/uploads/u1/a.jpg", read)
    outcome, _ = run(db, svc=svc, photo=photo)
    assert outcome.photo_vector == IMAGE_VECTOR and reads == ["gs://test-uploads/uploads/u1/a.jpg"]
    assert "near_photo" in {c.product_id for c in outcome.done.matches}

    again = PhotoSearch(None, read, outcome.photo_vector)
    outcome, _ = run(db, svc=svc, photo=again)
    assert len(reads) == 1 and svc.embedder.images == 1
    assert "near_photo" in {c.product_id for c in outcome.done.matches}


@pytest.mark.parametrize(
    "text, kind, expected",
    [
        ("white sneakers", None, "white sneakers"),
        ("", PhotoKind(category="Casual Shoes", gender="men"), "Something like the Casual Shoes for men in my photo."),
        ("like this but in white", PhotoKind(category="Kurtas", gender="unisex"),
         "like this but in white (My photo shows Kurtas.)"),
    ],
)
def test_request_text_adds_what_the_photo_shows(text, kind, expected):
    assert workflow.request_text(text, kind) == expected


def test_a_photo_we_cant_place_needs_words():
    with pytest.raises(PhotoProblem):
        workflow.request_text("  ", None)


def test_photo_kind_reads_the_category_and_retries_busy_answers():
    busy = errors.ClientError(429, {"error": {"code": 429, "message": "exhausted", "status": "RESOURCE_EXHAUSTED"}})
    answers = iter([busy, '{"category": "Watches", "gender": "women"}'])
    client = FakeClient(lambda contents: next(answers))
    kind = workflow.photo_kind(b"x", "image/jpeg", client=client, sleep=lambda s: None)
    assert kind == PhotoKind(category="Watches", gender="women")
    nothing = FakeClient(lambda contents: '{"category": "none", "gender": "unknown"}')
    assert workflow.photo_kind(b"x", "image/jpeg", client=nothing) is None


@pytest.mark.parametrize("url", ["http://example.com/a.jpg", "https://127.0.0.1/a.jpg", "https://10.0.0.8/a.jpg",
                                 "https://169.254.169.254/latest", "file:///etc/passwd"])
def test_photo_links_to_private_places_are_refused(url):
    with pytest.raises(PhotoProblem):
        workflow.fetch_link(url)


@pytest.mark.parametrize("mime, data", [("image/gif", b"x"), ("image/jpeg", b""), ("text/html", b"<p>")])
def test_only_real_photos_are_taken(mime, data):
    with pytest.raises(PhotoProblem):
        workflow.decode_photo(mime, data)


# ---------- Editing the checklist ----------


def edit(brief: ShoppingBrief, change, *, rules=(), disabled=(), client=None) -> workflow.Edited:
    return workflow.apply_change(brief, change, rules=list(rules), disabled=set(disabled), today=TUESDAY, client=client)


def test_editing_a_must_have_uses_the_standard_form():
    edited = edit(shoes_brief(), EditMustHave(type="edit_must_have", id="m3", rule="is", value="subtle"))
    assert edited.brief.must_haves[2] == MustHave(id="m3", attribute="logo_visible", rule="is", value="subtle")
    edited = edit(shoes_brief(), EditMustHave(type="edit_must_have", id="m2", rule="is_not", value="black"))
    assert edited.brief.must_haves[1].rule == "is_not"


@pytest.mark.parametrize(
    "change, message",
    [
        (EditMustHave(type="edit_must_have", id="m2", rule="is", value="tartan"), "isn't an option"),
        (EditMustHave(type="edit_must_have", id="m9", rule="is", value="white"), "couldn't find"),
        (EditMustHave(type="edit_must_have", id="r1", rule="is", value="canvas"), "saved rules"),
        (SetSize(type="set_size", size="47"), "Please pick one of these sizes"),
        (SetDeadline(type="set_deadline", deadline_date=date(2026, 10, 12)), "later date"),
        (SetPincode(type="set_pincode", pincode="999999"), "couldn't find pincode"),
    ],
)
def test_bad_edits_are_refused_with_a_clear_message(change, message):
    brief = shoes_brief(must_haves=[SNEAKERS, WHITE, NO_LOGO, NEVER_SUEDE])
    with pytest.raises(ChangeProblem, match=message):
        edit(brief, change, rules=[NEVER_SUEDE])


def test_an_edit_that_clashes_with_another_must_have_is_refused():
    brief = shoes_brief(must_haves=[WHITE, MustHave(id="m4", attribute="colour", rule="is_not", value="black")])
    with pytest.raises(ChangeProblem, match="clashes"):
        edit(brief, EditMustHave(type="edit_must_have", id="m2", rule="is", value="black"))


def test_removing_must_haves_and_switching_rules_off_and_on():
    rules = [NEVER_SUEDE, COD]
    brief = workflow.apply_rules(shoes_brief(), rules)
    assert [m.id for m in brief.must_haves] == ["m1", "m2", "m3", "r1", "r2"]

    edited = edit(brief, RemoveMustHave(type="remove_must_have", id="m2"), rules=rules)
    assert [m.attribute for m in edited.brief.must_haves] == ["type", "logo_visible", "upper_material", "cod"]

    off = edit(edited.brief, SetRule(type="set_rule", must_have=COD, enabled=False), rules=rules)
    assert [m.attribute for m in off.brief.must_haves][-1] == "upper_material"
    assert off.disabled == {COD.key()} and off.brief.rules_applied == ["not suede upper"]

    on = edit(off.brief, SetRule(type="set_rule", must_have=COD, enabled=True), rules=off.rules, disabled=off.disabled)
    assert on.disabled == set() and on.brief.must_haves[-1].attribute == "cod"

    chip = edit(on.brief, RemoveMustHave(type="remove_must_have", id="r1"), rules=rules)
    assert chip.disabled == {NEVER_SUEDE.key()}


def test_switching_on_a_rule_that_isnt_saved_adds_it_for_this_search():
    rule = MustHave(id="x", attribute="returnable", rule="is", value="yes")
    edited = edit(shoes_brief(), SetRule(type="set_rule", must_have=rule, enabled=True))
    assert edited.rules == [MustHave(id="x", attribute="returnable", rule="present", value=True, from_rule=True)]
    assert edited.brief.must_haves[-1].attribute == "returnable" and edited.brief.must_haves[-1].from_rule


def test_limits_size_pincode_and_nice_to_haves():
    brief = shoes_brief(
        size=None, missing=["size"], question="Which shoe size do you wear (UK)?",
        nice_to_haves=[NiceToHave(attribute="sole_colour", value="gum"), NiceToHave(attribute="usage", value="casual")],
    )
    brief = edit(brief, SetSize(type="set_size", size="9")).brief
    assert brief.size == "UK 9" and brief.missing == [] and brief.question is None
    brief = edit(brief, SetBudget(type="set_budget", budget_max_inr=2500)).brief
    assert brief.budget_max_inr == 2500
    brief = edit(brief, SetBudget(type="set_budget", budget_max_inr=None)).brief
    assert brief.budget_max_inr is None
    brief = edit(brief, SetDeadline(type="set_deadline", deadline_date=date(2026, 10, 16))).brief
    assert brief.deadline_date == date(2026, 10, 16)
    brief = edit(brief, SetPincode(type="set_pincode", pincode=" 400001 ")).brief
    assert brief.pincode == "400001"
    brief = edit(brief, RemoveNiceToHave(type="remove_nice_to_have", attribute="sole_colour", value="Gum")).brief
    assert brief.nice_to_haves == [NiceToHave(attribute="usage", value="casual")]
    brief = edit(brief, SetSize(type="set_size", size=None)).brief
    assert brief.size is None and brief.missing == ["size"]


def test_watches_come_in_one_size():
    watch = ShoppingBrief(category="Watches", gender="men")
    with pytest.raises(ChangeProblem, match="one size"):
        edit(watch, SetSize(type="set_size", size="M"))


def test_free_text_edits_go_to_gemini_and_keep_the_rules():
    client = FakeClient(lambda contents: brief_answer(must=[("type=sneakers", "is"), ("colour=black", "is")]))
    brief = workflow.apply_rules(shoes_brief(), [NEVER_SUEDE])
    edited = edit(brief, TextChange(type="text", text="black instead"), rules=[NEVER_SUEDE], client=client)
    assert [(m.attribute, m.value) for m in edited.brief.must_haves] == [
        ("type", "sneakers"), ("colour", "black"), ("upper_material", "suede"),
    ]
    assert edited.words == "black instead"
    assert "<change>\nblack instead\n</change>" in client.models.calls[0]

    added = edit(brief, AddMustHave(type="add_must_have", text="velcro"), rules=[NEVER_SUEDE], client=client)
    assert "It must have: velcro" in client.models.calls[1]
    assert added.words == "velcro"


# ---------- Saved rules ----------


def test_saved_rules_are_standardised_numbered_and_locked():
    items = [
        MustHave(id="", attribute="cod", rule="is", value="yes"),
        MustHave(id="", attribute="fabric", rule="is_not", value="polyester"),
        MustHave(id="", attribute="cod", rule="present", value=True),
    ]
    assert workflow.saved_rules(items) == [
        MustHave(id="r1", attribute="cod", rule="present", value=True, from_rule=True),
        MustHave(id="r2", attribute="fabric", rule="is_not", value="polyester", from_rule=True),
    ]
    with pytest.raises(ChangeProblem):
        workflow.saved_rules([MustHave(id="", attribute="fabric", rule="is", value="kevlar")])


# ---------- The ADK agent ----------


@pytest.fixture
def fake_deps(monkeypatch):
    db = FakeDB(sneakers(3))
    svc = services(db)
    deps = adk.Deps(
        services=lambda: svc,
        store=lambda: Store(db),
        brief_client=FakeClient(lambda contents: brief_answer()),
        now=lambda: TUESDAY_10AM,
    )
    monkeypatch.setattr(adk, "deps", deps)
    return deps


def test_root_agent_builds_with_the_three_tools():
    assert isinstance(adk.root_agent, LlmAgent)
    assert adk.root_agent.name == "ecommai"
    assert [tool.__name__ for tool in adk.root_agent.tools] == ["make_checklist", "search", "product_details"]


def test_adk_tools_run_the_same_workflow(fake_deps):
    made = adk.make_checklist("white sneakers with no logo, size 9, under 3000", "560001")
    checklist = ShoppingBrief.model_validate(made["checklist"])
    assert checklist.category == "Casual Shoes" and checklist.size == "UK 9" and made["question"] is None

    found = adk.search(checklist)
    assert found["passed"] == 3 and found["checked"] == 3
    assert found["matches"][0]["ticks"][0] == {"label": "sneakers", "status": "pass", "proof": "m1 seen in the photo"}

    details = adk.product_details("s1")
    assert details["id"] == "s1" and "embedding" not in details
    assert adk.product_details("nope") == {"error": "We couldn't find that product."}
    assert "error" in adk.make_checklist("sneakers", "999999")


def test_the_adk_search_tool_takes_a_checklist_as_json(fake_deps):
    from google.adk.tools.function_tool import FunctionTool

    tool = FunctionTool(adk.search)
    args, problems = tool._preprocess_args_with_validation({"checklist": shoes_brief().model_dump(mode="json")})
    assert not problems and isinstance(args["checklist"], ShoppingBrief)
