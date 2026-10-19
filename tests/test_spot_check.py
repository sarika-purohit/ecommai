from data_prep.spot_check import build_page, pick_sample, score


def _catalogue(groups: dict[str, int]) -> tuple[dict, dict]:
    enriched, selected = {}, {}
    next_id = 1
    for group, count in groups.items():
        for _ in range(count):
            enriched[str(next_id)] = {"attributes": {}}
            selected[str(next_id)] = {"group": group}
            next_id += 1
    return enriched, selected


def test_sample_covers_every_group_and_is_repeatable():
    enriched, selected = _catalogue({"tops": 1100, "watches": 190, "sunglasses": 190, "bags": 560})
    sample = pick_sample(enriched, selected, 100, seed=1)
    assert len(sample) == 100 and len(set(sample)) == 100
    assert {selected[i]["group"] for i in sample} == {"tops", "watches", "sunglasses", "bags"}
    assert sample == pick_sample(enriched, selected, 100, seed=1)


def test_page_embeds_products_without_breaking_out_of_the_script():
    page = build_page([{"id": "1", "title": "</script><b>x</b>", "type": "Tops", "photo": "p", "original": "o", "details": []}])
    assert "</script><b>" not in page
    assert "__PRODUCTS__" not in page


def test_score_separates_visible_accuracy_from_guesses_and_misses():
    marks = [
        {"id": "1", "name": "pockets", "value": "chest", "verdict": "right", "group": "tops"},
        {"id": "1", "name": "sleeve_length", "value": "full", "verdict": "wrong", "group": "tops"},
        {"id": "1", "name": "fabric", "value": "cotton", "verdict": "unknown", "group": "tops"},
        {"id": "1", "name": "sheerness", "value": "unknown", "verdict": "right", "group": "tops"},
        {"id": "2", "name": "dial_colour", "value": "unknown", "verdict": "wrong", "group": "watches"},
        {"id": "2", "name": "display", "value": "analog", "verdict": "right", "group": "watches"},
    ]
    summary = score(marks)
    assert summary["tops"]["visible_accuracy"] == round(1 / 3, 3)
    assert summary["tops"]["guessed_when_should_be_unknown"] == 1
    assert summary["watches"]["missed_visible_details"] == 1
    assert summary["all"]["visible_details_marked"] == 4
    assert summary["all"]["visible_accuracy"] == 0.5
