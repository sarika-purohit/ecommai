import pytest

from agent.quick_check import CONFIDENT, meets, quick_check, saved_value
from agent.rules import failure_label
from tests.fixtures.retrieve.factory import brief, catalogue, gemini, hit, must, product

COTTON = must("fabric", "is", "cotton")
POCKETS = must("pockets", "present", True, "m2")
NOT_LOUD = must("print_intensity", "is_not", "loud", "m3")
COD = must("cod", "present", True, "r1")


def check(must_haves, *products):
    kept, dropped = quick_check(brief(must_haves=must_haves), [hit(p) for p in products])
    return [h.product.id for h in kept], {h.product.id: reason for h, reason in dropped}


# ---------- Which saved details are clear enough ----------


def test_a_catalogue_value_that_breaks_a_must_have_drops_the_product():
    polyester = product("poly", attributes={"fabric": catalogue("polyester")})
    assert check([COTTON], polyester) == ([], {"poly": "not cotton"})


@pytest.mark.parametrize(
    "confidence, dropped",
    [(1.0, True), (0.9, True), (CONFIDENT, True), (0.84, False), (0.6, False), (0.0, False)],
)
def test_a_gemini_value_drops_only_at_or_above_the_confidence_threshold(confidence, dropped):
    kept, drops = check([COTTON], product("p", attributes={"fabric": gemini("polyester", confidence)}))
    assert (drops == {"p": "not cotton"}) is dropped
    assert (kept == ["p"]) is not dropped


@pytest.mark.parametrize("attribute", [catalogue("unknown"), gemini("unknown", 0.95), catalogue("")])
def test_unknown_never_drops(attribute):
    assert check([COTTON], product("p", attributes={"fabric": attribute})) == (["p"], {})


def test_a_missing_detail_never_drops():
    assert check([COTTON, POCKETS], product("p")) == (["p"], {})


def test_a_detail_flagged_for_review_never_drops():
    flagged = product("p", attributes={"fabric": catalogue("polyester")}, needs_review=["fabric"])
    assert check([COTTON], flagged) == (["p"], {})


def test_a_catalogue_value_does_not_drop_when_geminis_own_reading_meets_the_must_have():
    disputed = product("p", attributes={"fabric": catalogue("polyester", gemini_value="cotton")})
    assert check([COTTON], disputed) == (["p"], {})
    agreed = product("q", attributes={"fabric": catalogue("polyester", gemini_value="polyester")})
    assert check([COTTON], agreed) == ([], {"q": "not cotton"})


# ---------- Shop details and review-only details ----------


@pytest.mark.parametrize(
    "must_have, fields, reason",
    [
        (COD, {"cod": (False, 0)}, "no cash on delivery"),
        (must("returnable", "present", True), {"returns": (False, False)}, "not returnable"),
        (must("exchange", "is", "yes"), {"returns": (True, False)}, "no exchange"),
        (must("cod", "absent", True), {"cod": (True, 29)}, "cash on delivery"),
    ],
)
def test_shop_details_are_certain(must_have, fields, reason):
    assert check([must_have], product("p", **fields)) == ([], {"p": reason})
    assert reason == failure_label(must_have)


def test_shop_details_that_are_met_or_missing_keep_the_product():
    assert check([COD], product("has cod", cod=(True, 29)), product("no shop data", cod=None)) == (
        ["has cod", "no shop data"],
        {},
    )


@pytest.mark.parametrize("attribute", ["size_fit", "durability", "colour_fastness", "colour_match"])
def test_review_only_details_are_never_decided_here(attribute):
    saved = product("p", attributes={attribute: catalogue("something else")})
    assert check([must(attribute, "is", "good")], saved) == (["p"], {})


# ---------- The rules ----------


@pytest.mark.parametrize(
    "must_have, value, expected",
    [
        (COTTON, "cotton", True),
        (COTTON, "Cotton ", True),
        (COTTON, "blend", False),
        (NOT_LOUD, "loud", False),
        (NOT_LOUD, "subtle", True),
        (POCKETS, "side", True),
        (POCKETS, "none", False),
        (must("pockets", "absent", True), "none", True),
        (must("pockets", "absent", True), "chest", False),
        (must("pockets", "present", False), "chest", False),
        (must("logo_visible", "absent", True), "subtle", False),
        (must("polarised", "present", True), "no", False),
        (must("polarised", "is", "yes"), "yes", True),
        (COD, True, True),
        (COD, False, False),
        (must("case_size", "at_most", 40.0), "medium", None),
        (must("case_size", "at_most", 40.0), "38", True),
        (must("case_size", "at_least", 40.0), "38", False),
        (COTTON, "unknown", None),
        (COTTON, None, None),
    ],
)
def test_meets(must_have, value, expected):
    assert meets(must_have, value) is expected


NAVY = must("colour", "is", "navy blue")
NOT_NAVY = must("colour", "is_not", "navy blue")
BLUE = must("colour", "is", "blue")
NOT_BLUE = must("colour", "is_not", "blue")


@pytest.mark.parametrize(
    "must_have, saved, expected",
    [
        (BLUE, "blue", True),
        (BLUE, "red", False),
        (NOT_BLUE, "blue", False),
        (NOT_BLUE, "green", True),
        (NAVY, "blue", None),
        (NAVY, "green", False),
        (NAVY, "multi", False),
        (NOT_NAVY, "blue", None),
        (NOT_NAVY, "red", True),
        (NAVY, "unknown", None),
    ],
)
def test_a_saved_colour_family_against_a_family_or_a_shade(must_have, saved, expected):
    assert meets(must_have, saved) is expected


def test_a_shade_drops_only_other_families_and_sends_its_own_family_to_the_careful_check():
    navy_label = product("navy", attributes={"colour": catalogue("blue")}, baseColour="Navy Blue")
    teal_label = product("teal", attributes={"colour": catalogue("blue")}, baseColour="Teal")
    green = product("green", attributes={"colour": catalogue("green")}, baseColour="Sea Green")
    assert check([NAVY], navy_label, teal_label, green) == (["navy", "teal"], {"green": "not navy blue"})
    assert check([NOT_NAVY], navy_label, green) == (["navy", "green"], {})


def test_the_reason_is_the_first_broken_must_have_in_brief_order_with_the_rank_label():
    loud_without_pockets = product(
        "p",
        attributes={
            "fabric": catalogue("cotton"),
            "pockets": gemini("none", 0.9),
            "print_intensity": catalogue("loud"),
        },
    )
    _, dropped = check([COTTON, POCKETS, NOT_LOUD], loud_without_pockets)
    assert dropped == {"p": "no pockets"} == {"p": failure_label(POCKETS)}


def test_kept_and_dropped_keep_the_search_order_and_add_up():
    products = [
        product("a", attributes={"fabric": catalogue("cotton")}),
        product("b", attributes={"fabric": catalogue("silk")}),
        product("c"),
        product("d", attributes={"fabric": gemini("rayon", 0.95)}),
        product("e", attributes={"fabric": gemini("rayon", 0.7)}),
    ]
    kept, dropped = check([COTTON], *products)
    assert kept == ["a", "c", "e"]
    assert list(dropped) == ["b", "d"]


def test_saved_value_ignores_confidence_and_reads_shop_data():
    saved = product("p", attributes={"fabric": gemini("cotton", 0.3)}, cod=(False, 0))
    assert saved_value(saved, "fabric") == "cotton"
    assert saved_value(saved, "cod") is False
    assert saved_value(saved, "pockets") is None
    assert saved_value(product("q", attributes={"size_fit": catalogue("runs small")}), "size_fit") is None
