import pytest

from agent import mix, workflow
from agent.models import NiceToHave, ShoppingBrief
from agent.rank import rank
from agent.retrieve import find_candidates
from tests.fixtures.app import fakes as app_fakes
from tests.fixtures.ranking.factory import make_brief, make_candidate
from tests.fixtures.retrieve.factory import TUESDAY_10AM, FakeEmbedder, FakeFirestore, brief, product_doc, unit

BLUE_BLACK_RED = [
    NiceToHave(attribute="colour", value="blue", weight=0.51),
    NiceToHave(attribute="colour", value="black", weight=0.306),
    NiceToHave(attribute="colour", value="red", weight=0.184),
]


def first_letter(item: str) -> str | None:
    return {"b": "blue", "k": "black", "r": "red"}.get(item[0])


def test_shares_group_colours_by_family_and_ignore_plain_wishes():
    wishes = [NiceToHave(attribute="colour", value="navy blue", weight=0.3), NiceToHave(attribute="colour", value="blue", weight=0.3),
              NiceToHave(attribute="colour", value="black", weight=0.4), NiceToHave(attribute="fabric", value="cotton")]
    assert mix.shares(make_brief(nice_to_haves=wishes)) == pytest.approx({"blue": 0.6, "black": 0.4})
    assert mix.shares(make_brief(nice_to_haves=[NiceToHave(attribute="colour", value="blue")])) == {}


def test_interleave_takes_turns_by_share_and_keeps_the_order_within_a_colour():
    items = ["b1", "b2", "b3", "b4", "b5", "k1", "k2", "k3", "r1", "r2", "g1"]
    shares = {"blue": 0.51, "black": 0.306, "red": 0.184}
    assert mix.interleave(items, first_letter, shares) == ["b1", "k1", "b2", "r1", "b3", "k2", "b4", "b5", "k3", "r2", "g1"]
    assert mix.interleave(items, first_letter, {}) == items


def test_pick_keeps_each_colours_places_and_fills_the_rest_closest_first():
    closest_first = ["b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "k1", "k2", "r1", "g1"]
    shares = {"blue": 0.5, "black": 0.3, "red": 0.2}
    assert mix.pick(closest_first, first_letter, shares, 6) == ["b1", "b2", "b3", "k1", "k2", "r1"]
    assert mix.pick(closest_first, first_letter, shares, 10) == [*closest_first[:7], "k1", "k2", "r1"]
    assert mix.pick(closest_first, first_letter, {}, 3) == ["b1", "b2", "b3"]


def test_a_colour_mix_searches_each_colour_and_keeps_its_share_of_the_places():
    docs = [product_doc(f"blue{n}", baseColour="Navy Blue", embedding=unit(1, 0.01 * n, 0)) for n in range(6)]
    docs += [product_doc(f"black{n}", baseColour="Black", embedding=unit(1, 0.1 + 0.01 * n, 0)) for n in range(4)]
    wishes = [NiceToHave(attribute="colour", value="blue", weight=0.6), NiceToHave(attribute="colour", value="black", weight=0.4)]
    embedder = FakeEmbedder()
    hits = find_candidates(brief(nice_to_haves=wishes), query_text="kurtas", db=FakeFirestore(*docs), embedder=embedder,
                           limit=5, now=TUESDAY_10AM)
    assert [hit.product.id for hit in hits] == ["blue0", "blue1", "blue2", "black0", "black1"]
    assert len(embedder.texts) == 3
    assert any(text.endswith("Preferably: colour: black") for text in embedder.texts)


def test_without_a_mix_the_search_is_unchanged():
    docs = [product_doc(f"blue{n}", baseColour="Blue", embedding=unit(1, 0.01 * n, 0)) for n in range(4)]
    embedder = FakeEmbedder()
    plain = [NiceToHave(attribute="colour", value="black")]
    hits = find_candidates(brief(nice_to_haves=plain), query_text="kurtas", db=FakeFirestore(*docs), embedder=embedder,
                           limit=2, now=TUESDAY_10AM)
    assert [hit.product.id for hit in hits] == ["blue0", "blue1"] and len(embedder.texts) == 1


def test_ranking_shows_mostly_the_newest_colour_then_fewer_of_the_older_ones():
    candidates = [make_candidate(f"blue{n}", "pppp", price=1000 + n, colour="Blue") for n in range(4)]
    candidates += [make_candidate(f"black{n}", "pppp", price=900 + n, colour="Black") for n in range(2)]
    candidates += [make_candidate("red0", "pppp", price=800, colour="Red"), make_candidate("unsure", "pppu", colour="Red")]
    results = rank(make_brief(nice_to_haves=BLUE_BLACK_RED), candidates)
    assert [r.product_id for r in results.matches] == ["blue0", "black0", "blue1", "red0", "blue2", "black1", "blue3"]
    assert [r.product_id for r in results.almost] == ["unsure"]


def test_a_colour_mix_counts_as_one_wish_met_by_any_of_its_colours():
    cotton = NiceToHave(attribute="fabric", value="cotton")
    candidates = [
        make_candidate("red-cotton", "pppp", price=1000, colour="Red", attributes={"colour": "red", "fabric": "cotton"}),
        make_candidate("red-silk", "pppp", price=900, colour="Red", attributes={"colour": "red", "fabric": "silk"}),
    ]
    results = rank(make_brief(nice_to_haves=[*BLUE_BLACK_RED, cotton]), candidates)
    assert [r.product_id for r in results.matches] == ["red-cotton", "red-silk"]


def test_the_careful_check_takes_the_colours_by_share_not_only_the_closest():
    docs = [app_fakes.product_doc(f"w{n}", embedding=app_fakes.near_text(0.1 + 0.01 * n)) for n in range(14)]
    docs += [app_fakes.product_doc(f"k{n}", colour="Black", embedding=app_fakes.near_text(0.5 + 0.01 * n)) for n in range(8)]
    wishes = [NiceToHave(attribute="colour", value="white", weight=0.6), NiceToHave(attribute="colour", value="black", weight=0.4)]
    request = ShoppingBrief(category="Casual Shoes", gender="men", nice_to_haves=wishes, pincode=app_fakes.BENGALURU, size="UK 9")
    seen = []
    workflow.search(request, query_text="sneakers", services=app_fakes.services(app_fakes.FakeDB(docs)), emit=seen.append,
                    now=app_fakes.TUESDAY_10AM)
    checking = [event.product_id[0] for event in seen if event.kind == "checking"]
    assert len(checking) == workflow.CHECK_COUNT and checking.count("k") == 5
