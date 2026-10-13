import pytest

from agent.models import MustHave, ShoppingBrief
from agent.rules import apply_rules, failure_label, label

NEVER_POLYESTER = MustHave(id="saved1", attribute="fabric", rule="is_not", value="polyester")
ALWAYS_COD = MustHave(id="saved2", attribute="cod", rule="present", value=True)
NO_LOGO = MustHave(id="saved3", attribute="logo_visible", rule="absent", value=True)


def kurta_brief(*must_haves: MustHave) -> ShoppingBrief:
    return ShoppingBrief(category="Kurtas", must_haves=list(must_haves))


@pytest.mark.parametrize(
    "must_have, chip",
    [
        (MustHave(id="m", attribute="fabric", rule="is", value="cotton"), "cotton"),
        (MustHave(id="m", attribute="sleeve_length", rule="is", value="three-quarter"), "three-quarter sleeves"),
        (MustHave(id="m", attribute="neckline", rule="is", value="mandarin"), "mandarin collar"),
        (MustHave(id="m", attribute="pockets", rule="is", value="chest"), "chest pocket"),
        (MustHave(id="m", attribute="dial_colour", rule="is", value="black"), "black dial"),
        (MustHave(id="m", attribute="strap_material", rule="is", value="steel"), "steel strap"),
        (MustHave(id="m", attribute="frame_shape", rule="is", value="aviator"), "aviator frames"),
        (MustHave(id="m", attribute="usage", rule="is", value="office"), "for office"),
        (MustHave(id="m", attribute="colour", rule="is_not", value="black"), "not black"),
        (MustHave(id="m", attribute="colour", rule="is", value="navy blue"), "navy blue"),
        (MustHave(id="m", attribute="colour", rule="is", value="off-white"), "off-white"),
        (MustHave(id="m", attribute="colour", rule="is_not", value="mustard"), "not mustard"),
        (MustHave(id="m", attribute="pockets", rule="is_not", value="none"), "pockets"),
        (MustHave(id="m", attribute="size_fit", rule="is", value="true to size"), "true to size"),
        (MustHave(id="m", attribute="fabric", rule="is_not", value="polyester"), "not polyester"),
        (MustHave(id="m", attribute="pockets", rule="present", value=True), "pockets"),
        (MustHave(id="m", attribute="cod", rule="present", value=True), "cash on delivery"),
        (MustHave(id="m", attribute="logo_visible", rule="absent", value=True), "no logo visible"),
        (MustHave(id="m", attribute="pockets", rule="present", value=False), "no pockets"),
        (MustHave(id="m", attribute="polarised", rule="is", value="yes"), "polarised"),
        (MustHave(id="m", attribute="cod", rule="is", value="no"), "no cash on delivery"),
        (MustHave(id="m", attribute="case_size_mm", rule="at_most", value=40), "≤ 40"),
        (MustHave(id="m", attribute="rating", rule="at_least", value=4), "≥ 4"),
        (MustHave(id="m", attribute="rating", rule="at_least", value=4.5), "≥ 4.5"),
    ],
)
def test_label(must_have, chip):
    assert label(must_have) == chip


@pytest.mark.parametrize(
    "must_have, reason",
    [
        (MustHave(id="m", attribute="pockets", rule="present", value=True), "no pockets"),
        (MustHave(id="m", attribute="fabric", rule="is", value="cotton"), "not cotton"),
        (MustHave(id="m", attribute="colour", rule="is", value="navy blue"), "not navy blue"),
        (MustHave(id="m", attribute="colour", rule="is_not", value="sky blue"), "sky blue"),
        (MustHave(id="m", attribute="sleeve_length", rule="is", value="three-quarter"), "not three-quarter sleeves"),
        (MustHave(id="m", attribute="pockets", rule="is", value="chest"), "no chest pocket"),
        (MustHave(id="m", attribute="pockets", rule="is", value="none"), "has pockets"),
        (MustHave(id="m", attribute="print_intensity", rule="is_not", value="loud"), "loud print"),
        (MustHave(id="m", attribute="sheerness", rule="is_not", value="see-through"), "see-through"),
        (MustHave(id="m", attribute="logo_visible", rule="absent", value=True), "logo visible"),
        (MustHave(id="m", attribute="cod", rule="present", value=True), "no cash on delivery"),
        (MustHave(id="m", attribute="polarised", rule="is", value="yes"), "not polarised"),
        (MustHave(id="m", attribute="case_size_mm", rule="at_most", value=40), "over 40"),
        (MustHave(id="m", attribute="rating", rule="at_least", value=4), "under 4"),
    ],
)
def test_failure_label(must_have, reason):
    assert failure_label(must_have) == reason


def test_added_rules_are_locked_and_labelled():
    own = MustHave(id="m1", attribute="pockets", rule="present", value=True)
    brief = apply_rules(kurta_brief(own), [NEVER_POLYESTER, ALWAYS_COD])
    assert brief.must_haves[0] == own
    added = brief.must_haves[1:]
    assert all(m.from_rule for m in added)
    assert [(m.attribute, m.rule, m.value) for m in added] == [
        ("fabric", "is_not", "polyester"),
        ("cod", "present", True),
    ]
    assert brief.rules_applied == ["not polyester", "cash on delivery"]


def test_a_rule_switched_off_for_this_search_is_skipped():
    brief = apply_rules(kurta_brief(), [NEVER_POLYESTER, ALWAYS_COD], disabled={NEVER_POLYESTER.key()})
    assert [m.attribute for m in brief.must_haves] == ["cod"]
    assert brief.rules_applied == ["cash on delivery"]


def test_the_shoppers_own_must_have_on_the_same_attribute_wins():
    own = MustHave(id="m1", attribute="fabric", rule="is", value="polyester")
    brief = apply_rules(kurta_brief(own), [NEVER_POLYESTER])
    assert brief.must_haves == [own]
    assert brief.rules_applied == []


def test_a_rule_is_skipped_where_its_detail_does_not_apply():
    brief = apply_rules(ShoppingBrief(category="Watches"), [NEVER_POLYESTER, NO_LOGO])
    assert brief.must_haves == []
    assert brief.rules_applied == []


def test_a_rule_applies_where_its_detail_exists():
    brief = apply_rules(ShoppingBrief(category="Casual Shoes"), [NEVER_POLYESTER, NO_LOGO])
    assert [m.attribute for m in brief.must_haves] == ["logo_visible"]


@pytest.mark.parametrize(
    "category", ["Kurtas", "Tshirts", "Jeans", "Sports Shoes", "Watches", "Handbags", "Backpacks", "Sunglasses"]
)
def test_the_cash_on_delivery_rule_applies_to_every_group(category):
    brief = apply_rules(ShoppingBrief(category=category), [ALWAYS_COD])
    assert [m.attribute for m in brief.must_haves] == ["cod"]
    assert brief.rules_applied == ["cash on delivery"]


def test_added_rules_get_new_unique_ids():
    own = [
        MustHave(id="m1", attribute="pockets", rule="present", value=True),
        MustHave(id="r1", attribute="sleeve_length", rule="is", value="full"),
    ]
    brief = apply_rules(kurta_brief(*own), [NEVER_POLYESTER, ALWAYS_COD])
    assert [m.id for m in brief.must_haves] == ["m1", "r1", "r2", "r3"]


def test_the_same_saved_rule_twice_is_added_once():
    copy = NEVER_POLYESTER.model_copy(update={"id": "saved9"})
    brief = apply_rules(kurta_brief(), [NEVER_POLYESTER, copy])
    assert len(brief.must_haves) == 1


def test_two_different_rules_on_one_attribute_both_apply():
    never_rayon = MustHave(id="saved4", attribute="fabric", rule="is_not", value="rayon")
    brief = apply_rules(kurta_brief(), [NEVER_POLYESTER, never_rayon])
    assert brief.rules_applied == ["not polyester", "not rayon"]


def test_neither_the_brief_nor_the_saved_rules_are_changed():
    own = MustHave(id="m1", attribute="pockets", rule="present", value=True)
    brief = kurta_brief(own)
    before = brief.model_dump()
    rules = [NEVER_POLYESTER, ALWAYS_COD]
    rules_before = [r.model_dump() for r in rules]

    updated = apply_rules(brief, rules)
    updated.must_haves[0].value = False
    updated.missing.append("size")

    assert brief.model_dump() == before
    assert [r.model_dump() for r in rules] == rules_before
    assert updated is not brief


def test_applying_again_replaces_earlier_rules():
    first = apply_rules(kurta_brief(), [NEVER_POLYESTER, ALWAYS_COD])
    again = apply_rules(first, [NEVER_POLYESTER, ALWAYS_COD])
    switched_off = apply_rules(first, [NEVER_POLYESTER, ALWAYS_COD], disabled={ALWAYS_COD.key()})
    assert again.must_haves == first.must_haves
    assert switched_off.rules_applied == ["not polyester"]
    assert [m.attribute for m in switched_off.must_haves] == ["fabric"]


def test_an_unknown_category_is_an_error():
    with pytest.raises(ValueError, match="unknown category"):
        apply_rules(ShoppingBrief(category="Socks"), [NEVER_POLYESTER])
