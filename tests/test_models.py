import pytest
from pydantic import ValidationError

from agent.models import Check, Evidence, MustHave, Product, ShoppingBrief, Verification


def test_must_have_accepts_each_rule_with_the_right_value():
    MustHave(id="m1", attribute="fabric", rule="is", value="cotton")
    MustHave(id="m2", attribute="pockets", rule="present", value=True)
    MustHave(id="m3", attribute="case_size_mm", rule="at_most", value=40)


@pytest.mark.parametrize(
    "rule, value",
    [("present", "yes"), ("absent", 1.0), ("at_most", "cheap"), ("at_least", True), ("is", True), ("is_not", 3.0)],
)
def test_must_have_rejects_a_value_that_does_not_fit_the_rule(rule, value):
    with pytest.raises(ValidationError):
        MustHave(id="m1", attribute="x", rule=rule, value=value)


def test_must_have_key_is_stable_and_ignores_the_id():
    a = MustHave(id="m1", attribute="sleeve_length", rule="is", value="Three Quarter")
    b = MustHave(id="m7", attribute="sleeve_length", rule="is", value="three quarter")
    assert a.key() == b.key() == "sleeve_length__is__three-quarter"


def test_brief_parses_the_plan_example():
    brief = ShoppingBrief.model_validate(
        {
            "category": "Kurtas",
            "gender": "women",
            "must_haves": [
                {"id": "m1", "attribute": "fabric", "rule": "is", "value": "cotton"},
                {"id": "m2", "attribute": "pockets", "rule": "present", "value": True},
            ],
            "nice_to_haves": [{"attribute": "usage", "value": "office"}],
            "rules_applied": ["never polyester"],
            "budget_max_inr": 1200,
            "deadline_date": "2026-10-16",
            "pincode": "411038",
            "language": "hinglish",
            "missing": ["size"],
        }
    )
    assert brief.must_haves[1].value is True
    assert brief.deadline_date.isoformat() == "2026-10-16"


@pytest.mark.parametrize("pincode", ["011038", "41103", "4110388", "abcdef"])
def test_brief_rejects_bad_pincodes(pincode):
    with pytest.raises(ValidationError):
        ShoppingBrief(category="Kurtas", pincode=pincode)


def test_brief_rejects_duplicate_must_have_ids():
    with pytest.raises(ValidationError):
        ShoppingBrief(
            category="Kurtas",
            must_haves=[
                MustHave(id="m1", attribute="fabric", rule="is", value="cotton"),
                MustHave(id="m1", attribute="pockets", rule="present", value=True),
            ],
        )


@pytest.mark.parametrize("box", [[0, 0, 0, 10], [10, 10, 5, 20], [0, 0, 1001, 10], [1, 2, 3]])
def test_evidence_rejects_bad_boxes(box):
    with pytest.raises(ValidationError):
        Evidence(type="image", note="pocket", box_2d=box)


def test_pass_without_evidence_becomes_unclear():
    assert Check(must_have_id="m1", status="pass", confidence=0.9).status == "unclear"
    empty_note = Evidence(type="image", note="  ")
    assert Check(must_have_id="m1", status="pass", confidence=0.9, evidence=empty_note).status == "unclear"


def test_pass_with_evidence_stays_pass():
    evidence = Evidence(type="image", note="Side pocket visible", box_2d=[520, 120, 640, 260])
    assert Check(must_have_id="m1", status="pass", confidence=0.9, evidence=evidence).status == "pass"


def _check(status: str) -> Check:
    return Check(must_have_id="m", status=status, confidence=0.8, evidence=Evidence(type="details", note="n"))


def test_verification_overall():
    assert Verification(product_id="1", checks=[_check("pass"), _check("pass")]).overall == "pass"
    assert Verification(product_id="1", checks=[_check("pass"), _check("unclear")]).overall == "unclear"
    assert Verification(product_id="1", checks=[_check("unclear"), _check("fail")]).overall == "fail"
    assert Verification(product_id="1", checks=[]).all_pass is False


def test_product_round_trips_with_firestore_field_names():
    product = Product.model_validate(
        {
            "id": "15970",
            "title": "Turtle Check Men Navy Blue Shirt",
            "gender": "Men",
            "masterCategory": "Apparel",
            "subCategory": "Topwear",
            "articleType": "Shirts",
            "baseColour": "Navy Blue",
            "usage": "Casual",
            "group": "tops",
        }
    )
    assert product.gender == "men"
    stored = product.to_firestore()
    assert stored["articleType"] == "Shirts" and "article_type" not in stored
    assert Product.model_validate(stored) == product


def test_product_rejects_unknown_group():
    with pytest.raises(ValidationError):
        Product(
            id="1", title="t", gender="men", masterCategory="a", subCategory="b", articleType="Socks", group="socks"
        )
