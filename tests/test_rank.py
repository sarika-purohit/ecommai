import pytest

from agent.models import MustHave, NiceToHave
from agent.rank import Results, rank, rupees
from tests.fixtures.ranking.factory import FRI, THU, make_brief, make_candidate

OFFICE = NiceToHave(attribute="usage", value="Office")


def ids(results: list) -> list[str]:
    return [r.product_id for r in results]


def shown(results: Results) -> list[str]:
    return ids(results.matches) + ids(results.almost)


def assert_counts_add_up(results: Results) -> None:
    assert results.passed == len(results.matches)
    assert results.unclear == len(results.almost)
    assert results.checked == results.passed + results.unclear + sum(results.rejected.values()) + results.duplicates


def test_full_matches_rank_above_almost_even_when_almost_scores_higher():
    plain_match = make_candidate("match", "pppp", price=1200)
    great_almost = make_candidate(
        "almost", "pppu", price=500, rating=4.9, reviews=500, attributes={"usage": "office"}
    )
    results = rank(make_brief(nice_to_haves=[OFFICE]), [great_almost, plain_match])
    assert ids(results.matches) == ["match"]
    assert ids(results.almost) == ["almost"]
    assert results.almost[0].score > results.matches[0].score


@pytest.mark.parametrize("statuses", ["fppp", "pfpp", "ppfp", "pppf", "ufpp", "-ppf", "ffff"])
def test_a_product_failing_any_must_have_is_never_shown(statuses):
    results = rank(make_brief(), [make_candidate("bad", statuses), make_candidate("good", "pppp")])
    assert "bad" not in shown(results)
    assert ids(results.matches) == ["good"]
    assert sum(results.rejected.values()) == 1


def test_rejections_are_counted_under_the_first_failed_must_have_in_brief_order():
    candidates = [
        make_candidate("a", "pfpp"),
        make_candidate("b", "pfpf"),
        make_candidate("c", "ufpp"),
        make_candidate("d", "pppf"),
        make_candidate("e", "fppp"),
        make_candidate("f", "upfp"),
    ]
    results = rank(make_brief(), candidates)
    assert results.rejected == {"no pockets": 3, "see-through": 1, "not cotton": 1, "not three-quarter sleeves": 1}
    assert list(results.rejected)[0] == "no pockets"


def test_over_budget_and_late_products_are_never_shown_even_if_everything_passes():
    candidates = [
        make_candidate("over", "pppp", price=1201, arrives=THU),
        make_candidate("late", "pppp", price=1200, arrives=FRI),
        make_candidate("both", "pppp", price=1500, arrives=FRI),
        make_candidate("late-unsure", "pppu", price=900, arrives=FRI),
        make_candidate("ok", "pppp", price=1200, arrives=THU),
    ]
    results = rank(make_brief(budget=1200, deadline=THU), candidates)
    assert ids(results.matches) == ["ok"]
    assert results.almost == []
    assert results.rejected == {"over budget": 2, "late delivery": 2}


def test_limits_are_counted_before_must_haves():
    results = rank(make_brief(budget=1000), [make_candidate("a", "fppp", price=1100)])
    assert results.rejected == {"over budget": 1}


def test_an_unknown_delivery_date_is_not_counted_as_late():
    results = rank(make_brief(deadline=THU), [make_candidate("a", "pppp", arrives=None)])
    assert ids(results.matches) == ["a"]


def test_a_must_have_without_a_check_counts_as_unclear():
    results = rank(make_brief(), [make_candidate("a", "ppp-")])
    assert ids(results.almost) == ["a"]
    tick = results.almost[0].ticks[3]
    assert (tick.must_have_id, tick.status, tick.evidence) == ("m4", "unclear", None)


def test_ticks_follow_the_brief_with_labels_and_evidence():
    results = rank(make_brief(), [make_candidate("a", "pppp")])
    ticks = results.matches[0].ticks
    assert [t.label for t in ticks] == ["cotton", "pockets", "three-quarter sleeves", "not see-through"]
    assert all(t.status == "pass" and t.evidence is not None for t in ticks)
    assert ticks[1].evidence.note == "m2 pass"


def test_counts_add_up():
    candidates = [
        make_candidate("m1", "pppp", price=900, arrives=THU),
        make_candidate("m2", "pppp", price=1000, arrives=THU),
        make_candidate("m2-copy", "pppp", price=1050, arrives=THU, title="Kurta m2"),
        make_candidate("a1", "pppu", arrives=THU),
        make_candidate("a2", "u--p", arrives=THU),
        make_candidate("r1", "pfpp", arrives=THU),
        make_candidate("r2", "pfpp", arrives=THU),
        make_candidate("r3", "pppf", arrives=THU),
        make_candidate("r4", "pppp", price=1300, arrives=THU),
        make_candidate("r5", "pppp", arrives=FRI),
    ]
    results = rank(make_brief(budget=1200, deadline=THU), candidates)
    assert_counts_add_up(results)
    assert (results.checked, results.passed, results.unclear, results.duplicates) == (10, 2, 2, 1)
    assert results.rejected == {"no pockets": 2, "see-through": 1, "over budget": 1, "late delivery": 1}


def test_counts_add_up_with_nothing_to_show():
    results = rank(make_brief(), [])
    assert_counts_add_up(results)
    assert (results.checked, results.matches, results.almost, results.rejected) == (0, [], [], {})


def test_same_brand_colour_and_title_are_shown_once_keeping_the_higher_score():
    cheap = make_candidate("cheap", "pppp", price=900, title="Libas Women Blue Kurta", colour="Blue")
    pricey = make_candidate("pricey", "pppp", price=1100, title="  libas women  BLUE kurta", brand="LIBAS", colour="blue")
    results = rank(make_brief(), [pricey, cheap])
    assert ids(results.matches) == ["cheap"]
    assert results.duplicates == 1
    assert_counts_add_up(results)


def test_the_same_title_in_another_colour_or_brand_is_not_a_duplicate():
    blue = make_candidate("blue", "pppp", title="Women Kurta", colour="Blue")
    red = make_candidate("red", "pppp", title="Women Kurta", colour="Red")
    other_brand = make_candidate("other", "pppp", title="Women Kurta", colour="Blue", brand="Biba")
    results = rank(make_brief(), [blue, red, other_brand])
    assert sorted(ids(results.matches)) == ["blue", "other", "red"]
    assert results.duplicates == 0


def test_near_identical_embeddings_are_shown_once():
    a = make_candidate("a", "pppp", price=1000, embedding=[1.0, 0.0, 0.0])
    b = make_candidate("b", "pppp", price=1100, embedding=[1.0, 0.1, 0.0])
    different = make_candidate("c", "pppp", price=1100, embedding=[0.0, 1.0, 0.0])
    results = rank(make_brief(), [b, a, different])
    assert ids(results.matches) == ["a", "c"]
    assert results.duplicates == 1


def test_an_almost_copy_of_a_match_is_dropped_and_the_match_kept():
    match = make_candidate("match", "pppp", price=1200, title="Kurta X")
    copy = make_candidate("copy", "pppu", price=800, title="Kurta X")
    results = rank(make_brief(), [copy, match])
    assert ids(results.matches) == ["match"]
    assert results.almost == []
    assert results.duplicates == 1


def test_nice_to_haves_change_the_order_but_not_who_is_shown():
    candidates = [
        make_candidate("office", "pppp", price=1100, attributes={"usage": "office"}),
        make_candidate("casual", "pppp", price=1000, attributes={"usage": "casual"}),
        make_candidate("unsure-office", "ppup", attributes={"usage": "office"}),
        make_candidate("failing-office", "fppp", attributes={"usage": "office"}),
    ]
    plain = rank(make_brief(), candidates)
    preferring = rank(make_brief(nice_to_haves=[OFFICE]), candidates)
    assert ids(plain.matches) == ["casual", "office"]
    assert ids(preferring.matches) == ["office", "casual"]
    assert ids(preferring.almost) == ids(plain.almost) == ["unsure-office"]
    assert preferring.rejected == plain.rejected == {"not cotton": 1}


def test_taste_changes_the_order_but_not_who_is_shown():
    candidates = [
        make_candidate("red", "pppp", price=1000, embedding=[1.0, 0.0, 0.0]),
        make_candidate("blue", "pppp", price=1100, embedding=[0.0, 1.0, 0.0]),
        make_candidate("blue-failing", "pfpp", price=900, embedding=[0.0, 1.0, 0.1]),
    ]
    plain = rank(make_brief(), candidates)
    likes_blue = rank(make_brief(), candidates, taste={"Kurtas": [0.0, 1.0, 0.0]})
    other_type = rank(make_brief(), candidates, taste={"Watches": [0.0, 1.0, 0.0]})
    assert ids(plain.matches) == ids(other_type.matches) == ["red", "blue"]
    assert ids(likes_blue.matches) == ["blue", "red"]
    assert likes_blue.rejected == plain.rejected == {"no pockets": 1}


def test_a_taste_vector_of_another_length_is_ignored():
    candidates = [make_candidate("a", "pppp", embedding=[1.0, 0.0, 0.0])]
    assert rank(make_brief(), candidates, taste={"Kurtas": [1.0, 0.0]}) == rank(make_brief(), candidates)


def test_with_nothing_else_to_go_on_the_cheaper_one_comes_first():
    candidates = [make_candidate("dear", "pppp", price=1500), make_candidate("cheap", "pppp", price=900)]
    assert ids(rank(make_brief(), candidates).matches) == ["cheap", "dear"]


def test_at_the_same_price_the_sooner_one_comes_first():
    candidates = [make_candidate("fri", "pppp", arrives=FRI), make_candidate("thu", "pppp", arrives=THU)]
    assert ids(rank(make_brief(), candidates).matches) == ["thu", "fri"]


def test_a_rating_from_many_reviews_beats_a_perfect_one_from_a_single_review():
    candidates = [
        make_candidate("one-review", "pppp", rating=5.0, reviews=1),
        make_candidate("popular", "pppp", rating=4.4, reviews=300),
        make_candidate("unrated", "pppp"),
    ]
    assert ids(rank(make_brief(), candidates).matches) == ["popular", "one-review", "unrated"]


def test_match_reason_line():
    candidate = make_candidate("a", "pppp", price=1049, arrives=THU, rating=4.3, reviews=120)
    reason = rank(make_brief(), [candidate]).matches[0].reason
    assert reason == "All 4 must-haves confirmed · ₹1,049 delivered · arrives Thu 15 Oct · 4.3★ (120 reviews)"


def test_match_reason_leaves_out_what_we_do_not_know():
    brief = make_brief([MustHave(id="m1", attribute="fabric", rule="is", value="cotton")])
    single = rank(brief, [make_candidate("a", "p", price=124999, rating=4.0, reviews=1)])
    bare = rank(make_brief([]), [make_candidate("b", "", price=799)])
    assert single.matches[0].reason == "1 must-have confirmed · ₹1,24,999 delivered · 4.0★ (1 review)"
    assert bare.matches[0].reason == "₹799 delivered"


def test_almost_reason_lists_every_unsure_must_have():
    results = rank(make_brief(), [make_candidate("a", "pupu"), make_candidate("b", "pppu")])
    reasons = {r.product_id: r.reason for r in results.almost}
    assert reasons == {
        "a": "2 of 4 confirmed · not sure about: pockets, not see-through",
        "b": "3 of 4 confirmed · not sure about: not see-through",
    }


def test_every_shown_result_has_a_reason_line():
    statuses = ["pppp", "pppu", "uuuu", "----", "pppp"]
    candidates = [make_candidate(f"p{n}", letters) for n, letters in enumerate(statuses)]
    results = rank(make_brief(), candidates)
    assert len(results.matches) + len(results.almost) == 5
    assert all(r.reason.strip() for r in results.matches + results.almost)


@pytest.mark.parametrize(
    "amount, text",
    [
        (0, "₹0"),
        (49, "₹49"),
        (999, "₹999"),
        (1049, "₹1,049"),
        (12345, "₹12,345"),
        (124999, "₹1,24,999"),
        (1000000, "₹10,00,000"),
        (10000000, "₹1,00,00,000"),
    ],
)
def test_rupees_use_indian_grouping(amount, text):
    assert rupees(amount) == text
