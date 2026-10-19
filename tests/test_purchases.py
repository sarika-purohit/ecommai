from datetime import datetime, timedelta, timezone

import pytest

from agent import purchases
from agent.browse import CatalogItem
from agent.outfit import Suggestion
from agent.purchases import Anchor, Order

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def order(product_id="shirt", days=0, article_type="Shirts", colour="White", usage="Formal", **extra) -> Order:
    return Order(
        id=f"o-{product_id}-{days}", product_id=product_id, title=f"Title {product_id}", article_type=article_type,
        gender="men", base_colour=colour, usage=usage, ordered_at=NOW - timedelta(days=days), **extra,
    )


def row(product_id, match=80, article_type="Casual Shoes", colour="Blue", usage="Casual", slot="shoes") -> Suggestion:
    item = CatalogItem(id=product_id, title=f"Item {product_id}", gender="men", article_type=article_type,
                       base_colour=colour, usage=usage)
    return Suggestion(slot=slot, product=item, match=match, parts=[], reason="goes well")


def anchor(o: Order, weight=0.8) -> Anchor:
    return Anchor(order=o, weight=weight)


def ids(picks):
    return [p.product.id for p in picks]


# ---------- Recency and frequency ----------


def test_recency_halves_every_sixty_days_and_stops_after_a_year():
    assert purchases.recency(0) == 1
    assert purchases.recency(60) == pytest.approx(0.5)
    assert purchases.recency(120) == pytest.approx(0.25)
    assert purchases.recency(365) > 0
    assert purchases.recency(366) == 0


def test_a_future_date_counts_as_today():
    assert purchases.age_days(NOW + timedelta(days=3), NOW) == 0
    assert purchases.recency(purchases.age_days(NOW + timedelta(days=3), NOW)) == 1


def test_frequency_is_relative_to_the_most_ordered_value():
    freq = purchases.frequencies([order("a", 0), order("b", 0), order("c", 0, "Jeans", "Blue", "Casual")], NOW)
    assert freq.type == {"Shirts": 1.0, "Jeans": 0.5}
    assert freq.colour == {"white": 1.0, "blue": 0.5}
    assert freq.usage == {"Formal": 1.0, "Casual": 0.5}


def test_orders_over_a_year_old_count_for_nothing():
    assert purchases.anchors([order("old", 400)], NOW) == []


def test_frequency_lifts_the_anchor_of_the_type_ordered_most():
    orders = [order("s1", 10), order("s2", 10), order("jeans", 10, "Jeans", "Blue", "Casual")]
    top = {a.order.product_id: a.weight for a in purchases.anchors(orders, NOW)}
    r = purchases.recency(10)
    assert top["s1"] == pytest.approx(r * 1.0)
    assert top["jeans"] == pytest.approx(r * (0.6 + 0.4 * 0.5))
    assert top["s1"] > top["jeans"]


def test_only_the_top_three_distinct_purchases_are_anchors():
    orders = [order("a", 1), order("a", 2), order("b", 3), order("c", 4, "Jeans"), order("d", 5, "Watches")]
    chosen = purchases.anchors(orders, NOW)
    assert len(chosen) == 3
    assert len({a.order.product_id for a in chosen}) == 3
    assert chosen[0].order.product_id == "a"


# ---------- The rail ----------


def test_the_item_that_goes_with_two_purchases_beats_a_slightly_better_single_match():
    shirt, trousers = anchor(order("shirt")), anchor(order("trousers", 0, "Trousers"))
    rows = [
        (shirt, [row("both", 70), row("one", 75)]),
        (trousers, [row("both", 70)]),
    ]
    picks = purchases.suggest(rows, [shirt.order, trousers.order], set(), NOW)
    assert ids(picks) == ["both", "one"]
    assert {g.product_id for g in picks[0].goes_with} == {"shirt", "trousers"}
    assert [g.product_id for g in picks[1].goes_with] == ["shirt"]


def test_the_habit_lift_favours_the_usage_and_colour_the_shopper_orders_most():
    shirt = anchor(order("shirt", 0, usage="Casual", colour="Blue"))
    rows = [(shirt, [row("odd", 80, colour="Red", usage="Party"), row("usual", 80, colour="Blue", usage="Casual")])]
    assert ids(purchases.suggest(rows, [shirt.order], set(), NOW)) == ["usual", "odd"]


def test_never_suggests_what_was_ordered_or_disliked():
    shirt = anchor(order("shirt"))
    rows = [(shirt, [row("owned"), row("hated"), row("fine")])]
    owned = order("owned", 400, "Watches")
    picks = purchases.suggest(rows, [shirt.order, owned], {"hated"}, NOW)
    assert ids(picks) == ["fine"]


def test_skips_product_types_ordered_in_the_last_45_days_but_not_older_ones():
    shirt = anchor(order("shirt", 0))
    rows = [(shirt, [row("shoes"), row("watch", article_type="Watches", slot="watch")])]
    recent = order("sneaks", 45, "Casual Shoes")
    assert ids(purchases.suggest(rows, [shirt.order, recent], set(), NOW)) == ["watch"]
    older = order("sneaks", 46, "Casual Shoes")
    assert set(ids(purchases.suggest(rows, [shirt.order, older], set(), NOW))) == {"shoes", "watch"}


def test_at_most_three_picks_per_anchor_and_eight_in_all():
    first, second, third = (anchor(order(n, 0, t), 0.9) for n, t in (("a", "Shirts"), ("b", "Jeans"), ("c", "Watches")))
    many = [(first, [row(f"a{n}", 90 - n) for n in range(6)]),
            (second, [row(f"b{n}", 90 - n) for n in range(6)]),
            (third, [row(f"c{n}", 90 - n) for n in range(6)])]
    orders = [first.order, second.order, third.order]
    picks = purchases.suggest(many, orders, set(), NOW)
    assert len(picks) == 8
    assert all(sum(p.goes_with[0].product_id == name for p in picks) <= 3 for name in "abc")
    one = purchases.suggest(many[:1], orders[:1], set(), NOW)
    assert len(one) == 3


def test_unscored_rows_and_matches_under_50_are_left_out():
    shirt = anchor(order("shirt"))
    rows = [(shirt, [row("none", None), row("low", 49), row("edge", 50)])]
    assert ids(purchases.suggest(rows, [shirt.order], set(), NOW)) == ["edge"]


# ---------- Ideas ----------


def test_kurta_ideas_are_ideas_and_never_products():
    kurta = order("k", 0, "Kurtas", "Maroon", "Ethnic")
    calls = []
    result = purchases.from_orders([kurta], set(), NOW, lambda o: calls.append(o) or [row("shoe", 80)])
    assert [i.text for i in result.ideas] == ["pyjama or churidar", "juttis or mojaris"]
    assert all(i.goes_with_title == "Title k" for i in result.ideas)
    assert ids(result.items) == ["shoe"]
    assert not {"pyjama", "churidar", "jutti", "mojari"} & {w for p in result.items for w in p.product.title.lower().split()}


def test_kurtis_and_tunics_get_their_own_ideas():
    for kind in ("Kurtis", "Tunics"):
        result = purchases.from_orders([order("k", 0, kind)], set(), NOW, lambda o: [])
        assert [i.text for i in result.ideas] == ["leggings or palazzos", "dupatta"]


def test_no_orders_gives_an_empty_answer_without_asking_for_looks():
    def never(_):
        raise AssertionError("no look should be asked for")

    result = purchases.from_orders([], set(), NOW, never)
    assert result.items == [] and result.ideas == [] and result.sample_data and result.demo_orders


def test_a_look_that_failed_is_noted_and_the_rest_still_show():
    a, b = order("a", 0), order("b", 1, "Jeans")
    result = purchases.from_orders([a, b], set(), NOW, lambda o: [row("x")] if o.product_id == "a" else None)
    assert ids(result.items) == ["x"] and result.note
