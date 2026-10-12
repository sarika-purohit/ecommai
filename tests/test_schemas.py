from data_prep.schemas import (
    ARTICLE_TYPE_TO_GROUP,
    GROUPS,
    REVIEW_ATTRIBUTES,
    SHOP_ATTRIBUTES,
    UNKNOWN,
    VOCABULARY,
    attributes_for,
    group_for,
    is_allowed,
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


def test_review_attributes_are_never_asked_of_photos():
    for attributes in VOCABULARY.values():
        assert not set(REVIEW_ATTRIBUTES) & set(attributes)


def test_is_allowed():
    assert is_allowed("tops", "sleeve_length", "three-quarter")
    assert is_allowed("tops", "sleeve_length", UNKNOWN)
    assert is_allowed("watches", "cod", "yes")
    assert not is_allowed("tops", "sleeve_length", "3/4")
    assert not is_allowed("tops", "dial_colour", "green")
