import pytest

from data_prep.schemas import (
    ARTICLE_TYPE_TO_GROUP,
    COLOUR_DETAILS,
    COLOUR_FAMILIES,
    COLOUR_SHADES,
    GROUPS,
    REVIEW_ATTRIBUTES,
    SHADE_HINTS,
    SHADE_WORDS,
    SHOP_ATTRIBUTES,
    UNKNOWN,
    VOCABULARY,
    attributes_for,
    colour_family,
    colour_within,
    family_of,
    group_for,
    is_allowed,
    is_shade,
)


def test_every_group_has_a_vocabulary():
    assert set(GROUPS) == set(VOCABULARY)


def test_each_product_type_belongs_to_exactly_one_group():
    all_types = [t for types in GROUPS.values() for t in types]
    assert len(all_types) == len(set(all_types)) == len(ARTICLE_TYPE_TO_GROUP)


def test_values_are_lowercase_and_unique_and_never_unknown():
    for group, attributes in VOCABULARY.items():
        for attribute, values in attributes.items():
            assert values, f"{group}.{attribute} has no values"
            assert len(values) == len(set(values)), f"{group}.{attribute} has duplicates"
            assert all(v == v.lower() for v in values), f"{group}.{attribute} has uppercase values"
            assert UNKNOWN not in values, f"{group}.{attribute} lists unknown explicitly"


def test_group_lookup():
    assert group_for("Kurtas") == "tops"
    assert group_for("Sports Shoes") == "shoes"
    assert group_for("Socks") is None


def test_shop_and_review_attributes_apply_to_every_group():
    for group in GROUPS:
        assert set(SHOP_ATTRIBUTES) <= set(attributes_for(group))
        assert set(REVIEW_ATTRIBUTES) <= set(attributes_for(group))


def test_colour_applies_to_every_group_except_watches():
    assert "colour" not in attributes_for("watches")
    for group in GROUPS:
        if group != "watches":
            assert attributes_for(group)["colour"] == [*COLOUR_FAMILIES, *COLOUR_SHADES]


def test_every_shade_is_a_lowercase_name_in_a_known_family():
    assert not set(COLOUR_SHADES) & set(COLOUR_FAMILIES)
    for shade, family in COLOUR_SHADES.items():
        assert shade == shade.lower().strip() and family in COLOUR_FAMILIES
        assert colour_family(shade) in (None, family), f"{shade} is in another family in the catalogue"


def test_other_shade_words_and_hints_point_at_listed_shades():
    assert set(SHADE_WORDS.values()) <= set(COLOUR_SHADES)
    assert not set(SHADE_WORDS) & (set(COLOUR_SHADES) | set(COLOUR_FAMILIES))
    assert set(SHADE_HINTS) <= set(COLOUR_SHADES)


@pytest.mark.parametrize("shade", ["navy blue", "sky blue", "light blue", "baby blue", "dark blue", "royal blue",
                                   "cobalt blue", "teal", "turquoise", "mint green", "sea green", "olive",
                                   "dark green", "light green", "pista green", "maroon", "wine", "burgundy",
                                   "mustard", "peach", "lavender", "lilac", "cream", "off-white", "ivory",
                                   "charcoal", "khaki", "tan", "rust"])
def test_common_shopper_shades_are_listed(shade):
    assert is_shade(shade) and is_allowed("tops", "colour", shade)


def test_family_of_a_family_a_shade_and_anything_else():
    assert family_of("blue") == "blue"
    assert family_of("Navy Blue ") == "blue" and family_of("mint green") == "green"
    assert family_of("navy") is None and family_of("unknown") is None


@pytest.mark.parametrize(
    "colour, wanted, within",
    [
        ("blue", "blue", True),
        ("navy blue", "navy blue", True),
        ("navy blue", "blue", True),
        ("red", "blue", False),
        ("green", "navy blue", False),
        ("mint green", "light blue", False),
        ("blue", "navy blue", None),
        ("sky blue", "navy blue", None),
        ("multi", "blue", False),
    ],
)
def test_colour_within(colour, wanted, within):
    assert colour_within(colour, wanted) is within


def test_colour_details_are_details_of_some_group():
    every = {attribute for group in GROUPS for attribute in attributes_for(group)}
    assert COLOUR_DETAILS <= every
    assert not COLOUR_DETAILS & set(REVIEW_ATTRIBUTES)


def test_every_catalogue_colour_has_a_family():
    catalogue = ["Black", "Navy Blue", "Off White", "Grey Melange", "Turquoise Blue", "Coffee Brown", "Sea Green", "Steel"]
    assert all(colour_family(c) for c in catalogue)
    assert colour_family("navy blue") == "blue"
    assert colour_family("NA") is None and colour_family(None) is None


def test_review_attributes_are_never_asked_of_photos():
    for attributes in VOCABULARY.values():
        assert not set(REVIEW_ATTRIBUTES) & set(attributes)


def test_is_allowed():
    assert is_allowed("tops", "sleeve_length", "three-quarter")
    assert is_allowed("tops", "sleeve_length", UNKNOWN)
    assert is_allowed("watches", "cod", "yes")
    assert not is_allowed("tops", "sleeve_length", "3/4")
    assert not is_allowed("tops", "dial_colour", "green")


def test_a_colour_must_have_may_be_a_family_or_a_listed_shade():
    assert is_allowed("tops", "colour", "blue") and is_allowed("tops", "colour", "navy blue")
    assert is_allowed("shoes", "colour", "off-white") and is_allowed("bags", "colour", "maroon")
    assert not is_allowed("tops", "colour", "navy")
    assert not is_allowed("tops", "colour", "peacock blue")
    assert not is_allowed("watches", "colour", "navy blue")
    assert not is_allowed("watches", "dial_colour", "navy blue")
