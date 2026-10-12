import math

import pytest

from agent.learn import TasteVector, clear_taste, cosine, update_taste, vectors
from agent.rank import rank
from tests.fixtures.ranking.factory import make_brief, make_candidate


def test_the_first_action_sets_the_vector():
    profile = update_taste({}, "Kurtas", [1.0, 2.0], "like")
    assert profile == {"Kurtas": TasteVector(vector=[1.0, 2.0], count=1.0)}


def test_likes_and_saves_make_a_running_average():
    profile = update_taste({}, "Kurtas", [1.0, 0.0], "like")
    profile = update_taste(profile, "Kurtas", [0.0, 1.0], "save")
    profile = update_taste(profile, "Kurtas", [0.5, 0.5], "like")
    assert profile["Kurtas"].vector == pytest.approx([0.5, 0.5])
    assert profile["Kurtas"].count == 3.0


def test_a_purchase_counts_double():
    profile = update_taste({}, "Kurtas", [1.0, 0.0], "like")
    profile = update_taste(profile, "Kurtas", [0.0, 1.0], "buy")
    assert profile["Kurtas"].vector == pytest.approx([1 / 3, 2 / 3])
    assert profile["Kurtas"].count == 3.0


def test_each_product_type_has_its_own_profile():
    profile = update_taste({}, "Kurtas", [1.0, 0.0], "like")
    profile = update_taste(profile, "Watches", [0.0, 1.0], "buy")
    assert vectors(profile) == {"Kurtas": [1.0, 0.0], "Watches": [0.0, 1.0]}


def test_an_update_leaves_the_old_profile_alone():
    before = update_taste({}, "Kurtas", [1.0, 0.0], "like")
    after = update_taste(before, "Kurtas", [0.0, 1.0], "like")
    assert before["Kurtas"].vector == [1.0, 0.0]
    assert after["Kurtas"].vector == pytest.approx([0.5, 0.5])


def test_an_embedding_of_another_length_is_rejected():
    profile = update_taste({}, "Kurtas", [1.0, 0.0], "like")
    with pytest.raises(ValueError):
        update_taste(profile, "Kurtas", [1.0, 0.0, 0.0], "like")


def test_an_unknown_action_is_rejected():
    with pytest.raises(ValueError, match="unknown action"):
        update_taste({}, "Kurtas", [1.0], "dislike")


def test_clearing_resets_the_profile_and_the_order():
    candidates = [
        make_candidate("red", "pppp", price=1000, embedding=[1.0, 0.0, 0.0]),
        make_candidate("blue", "pppp", price=1100, embedding=[0.0, 1.0, 0.0]),
    ]
    profile = update_taste({}, "Kurtas", [0.0, 1.0, 0.0], "buy")
    assert [r.product_id for r in rank(make_brief(), candidates, vectors(profile)).matches] == ["blue", "red"]

    cleared = clear_taste()
    assert cleared == {}
    assert vectors(cleared) == {}
    assert rank(make_brief(), candidates, vectors(cleared)) == rank(make_brief(), candidates)


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ([1.0, 0.0], [2.0, 0.0], 1.0),
        ([1.0, 0.0], [0.0, 3.0], 0.0),
        ([1.0, 1.0], [-1.0, -1.0], -1.0),
        ([1.0, 2.0, 3.0], [4.0, 5.0, 6.0], 32 / math.sqrt(14 * 77)),
        ([0.0, 0.0], [1.0, 1.0], 0.0),
    ],
)
def test_cosine(a, b, expected):
    assert cosine(a, b) == pytest.approx(expected)


def test_cosine_rejects_vectors_of_different_lengths():
    with pytest.raises(ValueError):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])


def test_liking_blue_items_moves_blue_up_but_never_lets_a_failing_product_through():
    candidates = [
        make_candidate("red", "pppp", price=1000, colour="Red", embedding=[0.1, 1.0, 0.0]),
        make_candidate("blue", "pppp", price=1100, colour="Blue", embedding=[1.0, 0.2, 0.0]),
        make_candidate("blue-no-pockets", "pfpp", price=900, colour="Blue", embedding=[1.0, 0.0, 0.3]),
        make_candidate("blue-unsure", "pppu", price=1200, colour="Blue", embedding=[0.9, 0.0, 0.5]),
        make_candidate("red-unsure", "pppu", price=1000, colour="Red", embedding=[0.0, 1.0, 0.4]),
    ]
    before = rank(make_brief(), candidates)

    profile = clear_taste()
    for liked in ([0.9, 0.1, 0.0], [1.0, 0.0, 0.1], [0.95, 0.05, 0.05]):
        profile = update_taste(profile, "Kurtas", liked, "like")
    after = rank(make_brief(), candidates, vectors(profile))

    assert [r.product_id for r in before.matches] == ["red", "blue"]
    assert [r.product_id for r in after.matches] == ["blue", "red"]
    assert [r.product_id for r in before.almost] == ["red-unsure", "blue-unsure"]
    assert [r.product_id for r in after.almost] == ["blue-unsure", "red-unsure"]
    for results in (before, after):
        assert "blue-no-pockets" not in [r.product_id for r in results.matches + results.almost]
        assert results.rejected == {"no pockets": 1}
