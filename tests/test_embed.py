import json
import math
from pathlib import Path

import httpx
import pytest
from google.genai import errors, types

from agent.models import Attribute
from data_prep import embed
from data_prep.embed import (
    DESCRIPTION_CHARS,
    Embedding,
    GeminiEmbedder,
    Summary,
    catalogue_attributes,
    document_text,
    embed_query,
    normalise,
    plan,
    query_text,
    read_jsonl_by_id,
    read_selected,
    retrying,
    run,
    shorten,
)

FIXTURES = Path(__file__).parent / "fixtures" / "firestore"
DIMENSIONS = 768


@pytest.fixture(scope="module")
def products() -> dict[str, dict[str, str]]:
    return read_selected(FIXTURES / "selected.csv")


@pytest.fixture(scope="module")
def real() -> dict[str, dict]:
    return read_jsonl_by_id(FIXTURES / "real_details.jsonl")


@pytest.fixture(scope="module")
def enriched() -> dict[str, dict]:
    return read_jsonl_by_id(FIXTURES / "enriched.jsonl")


@pytest.fixture(scope="module")
def photos() -> dict[str, dict]:
    return read_jsonl_by_id(FIXTURES / "photos.jsonl")


def fake_values(text: str, dimensions: int = DIMENSIONS) -> list[float]:
    seed = sum(map(ord, text)) % 89 + 2
    return [((seed * (i + 3)) % 101) / 25 + 0.5 for i in range(dimensions)]


def statistics(text_tokens: int, image_tokens: int) -> types.ContentEmbeddingStatistics:
    return types.ContentEmbeddingStatistics(
        token_count=text_tokens + image_tokens,
        tokens_details=[
            types.ModalityTokenCount(modality=types.MediaModality.TEXT, token_count=text_tokens),
            types.ModalityTokenCount(modality=types.MediaModality.IMAGE, token_count=image_tokens),
        ],
    )


class FakeModels:
    def __init__(self, dimensions: int = DIMENSIONS, embeddings: int = 1):
        self.dimensions = dimensions
        self.embeddings = embeddings
        self.calls: list[dict] = []

    def embed_content(self, model: str, contents: list[types.Content], config: types.EmbedContentConfig):
        self.calls.append({"model": model, "contents": contents, "config": config})
        text = " ".join(part.text for part in contents[0].parts if part.text)
        has_photo = any(part.inline_data for part in contents[0].parts)
        embedding = types.ContentEmbedding(
            values=fake_values(text, self.dimensions), statistics=statistics(len(text.split()), 258 * has_photo)
        )
        return types.EmbedContentResponse(embeddings=[embedding] * self.embeddings)


class FakeClient:
    def __init__(self, **kwargs):
        self.models = FakeModels(**kwargs)


class FakeEmbedder:
    def __init__(self, fail: dict[str, Exception] | None = None):
        self.fail = fail or {}
        self.texts: list[str] = []

    def __call__(self, parts: list[types.Part]) -> Embedding:
        text = parts[-1].text
        self.texts.append(text)
        for product_id, error in self.fail.items():
            if f"#{product_id}" in parts[0].inline_data.data.decode():
                raise error
        return Embedding(normalise(fake_values(text))[0], text_tokens=40, image_tokens=258, raw_norm=1.0)


def fake_fetch(url: str) -> bytes:
    return f"photo #{Path(url).stem}".encode()


def unit_length(values: list[float]) -> bool:
    return math.isclose(math.sqrt(sum(v * v for v in values)), 1.0, rel_tol=1e-9)


# ---------- The text sent with each photo ----------


def test_text_has_title_type_colour_confident_details_and_the_enriched_description(products, real, enriched):
    text = document_text(products["30763"], real["30763"], enriched["30763"])
    assert text.startswith("title: Fabindia Men Printed White Kurta | text: Kurtas for men, colour White. Details: ")
    assert "sleeve length: full; neckline: mandarin; pockets: chest" in text
    assert "pattern" not in text, "details under the confidence bar are left out"
    assert "unknown" not in text and "fabric" not in text
    assert text.endswith(enriched["30763"]["description"])
    assert "Composition" not in text, "the enriched description replaces the real one"


def test_without_enrichment_the_text_uses_the_catalogue_labels_and_real_description(products, real):
    text = document_text(products["29493"], real["29493"], None)
    assert "Details: display: analog; case shape: round; strap material: leather; case size: small" in text
    assert "Case diameter: 32 mm Gold plated case" in text, "line breaks become spaces"
    assert "\n" not in text


def test_text_with_nothing_but_the_catalogue_row():
    row = {"title": " Plain Top ", "articleType": "Tops", "gender": "women", "baseColour": ""}
    assert document_text(row, None, None) == "title: Plain Top | text: Tops for women"


def test_long_descriptions_are_cut_at_a_word():
    words = "soft breathable cotton with a relaxed fit " * 40
    short = shorten(words.strip(), DESCRIPTION_CHARS)
    assert len(short) <= DESCRIPTION_CHARS + 3
    assert short.endswith("...") and not short.endswith(" ...")
    assert words.startswith(short[:-3])
    assert shorten("short", DESCRIPTION_CHARS) == "short"


def test_queries_use_the_search_task_prefix_and_products_the_document_format(products, real):
    assert query_text("  green dial\nwatch ") == "task: search result | query: green dial watch"
    assert "task:" not in document_text(products["36067"], real["36067"], None)


def test_catalogue_labels_become_valid_attributes(real):
    attributes = catalogue_attributes(real["29493"])
    assert set(attributes) == {"display", "case_shape", "strap_material", "case_size"}
    for attribute in attributes.values():
        checked = Attribute.model_validate(attribute)
        assert (checked.source, checked.confidence) == ("catalogue", 1.0)
    assert catalogue_attributes(None) == {}


# ---------- The model call ----------


def test_one_call_with_photo_and_text_gives_one_normalised_embedding_and_token_counts():
    client = FakeClient()
    embedder = GeminiEmbedder(client, "gemini-embedding-2", DIMENSIONS)
    result = embedder(embed.product_parts(b"jpeg bytes", "title: A | text: B"))

    [call] = client.models.calls
    assert call["model"] == "gemini-embedding-2"
    assert call["config"].output_dimensionality == DIMENSIONS
    assert call["config"].task_type is None, "gemini-embedding-2 has no task_type setting"
    [content] = call["contents"]
    assert [bool(p.inline_data) for p in content.parts] == [True, False]
    assert content.parts[0].inline_data.mime_type == "image/jpeg"
    assert len(result.values) == DIMENSIONS and unit_length(result.values)
    assert result.raw_norm > 1
    assert (result.text_tokens, result.image_tokens) == (5, 258)


def test_a_query_is_embedded_as_text_only_with_the_search_prefix():
    client = FakeClient()
    result = embed_query(GeminiEmbedder(client, "gemini-embedding-2", DIMENSIONS), "white sneakers")
    [content] = client.models.calls[0]["contents"]
    assert [p.text for p in content.parts] == ["task: search result | query: white sneakers"]
    assert result.image_tokens == 0 and unit_length(result.values)


@pytest.mark.parametrize("kwargs, message", [({"dimensions": 3072}, "3072 numbers"), ({"embeddings": 2}, "2 embed")])
def test_wrong_shaped_answers_are_rejected(kwargs, message):
    embedder = GeminiEmbedder(FakeClient(**kwargs), "gemini-embedding-2", DIMENSIONS)
    with pytest.raises(ValueError, match=message):
        embedder(embed.product_parts(b"x", "t"))


def test_normalise():
    values, norm = normalise([3.0, 4.0])
    assert values == [0.6, 0.8] and norm == 5.0
    with pytest.raises(ValueError):
        normalise([0.0, 0.0])


def busy() -> errors.ServerError:
    return errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})


@pytest.mark.parametrize(
    "error",
    [
        busy(),
        errors.ClientError(429, {"error": {"code": 429, "message": "slow down", "status": "RESOURCE_EXHAUSTED"}}),
        httpx.ConnectError("reset"),
    ],
)
def test_busy_and_network_errors_are_retried_with_growing_waits(error):
    attempts, waits = [], []

    def job() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise error
        return "ok"

    assert retrying(job, attempts=4, delay=1.0, sleep=waits.append) == "ok"
    assert len(attempts) == 3
    assert 0.75 <= waits[0] <= 1.25 and 1.5 <= waits[1] <= 2.5


def test_bad_requests_are_not_retried_and_retries_give_up():
    bad = errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})
    calls = []

    def job():
        calls.append(1)
        raise bad

    with pytest.raises(errors.ClientError):
        retrying(job, attempts=4, delay=1.0, sleep=lambda _: None)
    assert len(calls) == 1

    calls.clear()

    def always_busy():
        calls.append(1)
        raise busy()

    with pytest.raises(errors.ServerError):
        retrying(always_busy, attempts=3, delay=1.0, sleep=lambda _: None)
    assert len(calls) == 3


# ---------- Running, resuming and logging cost ----------


def test_run_writes_one_row_per_product_and_skips_products_without_a_photo(tmp_path, products, real, enriched, photos):
    out = tmp_path / "embeddings.jsonl"
    jobs, summary = plan(products, products, photos, real, enriched, {})
    assert summary.no_photo == ["11297"]
    run(jobs, summary, FakeEmbedder(), fake_fetch, "gemini-embedding-2", out, workers=2)

    rows = read_jsonl_by_id(out)
    assert set(rows) == {"30763", "29493", "36067"}
    for product_id, row in rows.items():
        assert set(row) == {"id", "embedding", "text_used", "model", "tokens"}
        assert row["model"] == "gemini-embedding-2"
        assert len(row["embedding"]) == DIMENSIONS and unit_length(row["embedding"])
        assert row["text_used"] == document_text(products[product_id], real.get(product_id), enriched.get(product_id))
        assert row["tokens"] == {"text": 40, "image": 258}
    assert summary.done == 3 and not summary.failed


def test_rerunning_skips_finished_products_and_re_embeds_ones_whose_text_changed(tmp_path, products, real, photos):
    out = tmp_path / "embeddings.jsonl"
    jobs, summary = plan(products, products, photos, real, {}, {})
    run(jobs, summary, FakeEmbedder(), fake_fetch, "m", out)

    done = {i: row["text_used"] for i, row in read_jsonl_by_id(out).items()}
    jobs, summary = plan(products, products, photos, real, {}, done)
    assert jobs == [] and summary.unchanged == 3

    enriched = read_jsonl_by_id(FIXTURES / "enriched.jsonl")
    jobs, summary = plan(products, products, photos, real, enriched, done)
    assert [job.id for job in jobs] == ["30763"]
    run(jobs, summary, FakeEmbedder(), fake_fetch, "m", out)
    assert len(out.read_text().splitlines()) == 4
    assert "pockets: chest" in read_jsonl_by_id(out)["30763"]["text_used"], "the newer line wins"


def test_a_failed_product_is_reported_and_the_rest_are_still_written(tmp_path, products, real, photos):
    out = tmp_path / "embeddings.jsonl"
    jobs, summary = plan(products, products, photos, real, {}, {})
    bad = errors.ClientError(400, {"error": {"code": 400, "message": "bad image", "status": "INVALID_ARGUMENT"}})
    run(jobs, summary, FakeEmbedder(fail={"29493": bad}), fake_fetch, "m", out, attempts=3, delay=0)
    assert summary.failed == ["29493"]
    assert set(read_jsonl_by_id(out)) == {"30763", "36067"}


def test_cost_comes_from_the_token_counts():
    summary = Summary(done=2, text_tokens=1_000_000, image_tokens=2_000_000)
    assert summary.cost == pytest.approx(0.20 + 0.90)
    report = embed.report(Summary(done=1, planned=1, text_tokens=60, image_tokens=258, seconds=1.0, norms=[1.0]))
    assert "US$0.000128 per product" in report and "text 60" in report and "image 258" in report


def test_reading_jsonl_skips_a_broken_last_line(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text('{"id": "1", "v": 1}\n{"id": "1", "v": 2}\n{"id": "2", "v"', encoding="utf-8")
    assert read_jsonl_by_id(path) == {"1": {"id": "1", "v": 2}}


def test_main_embeds_the_ids_file_into_out(tmp_path, monkeypatch):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("29493\n11297\n36067\n29493\n", encoding="utf-8")
    out = tmp_path / "trials" / "embeddings_trial.jsonl"
    client = FakeClient()
    monkeypatch.setattr(embed, "load_dotenv", lambda: None)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", str(DIMENSIONS))
    monkeypatch.setattr(embed, "load_credentials", lambda settings: None)
    monkeypatch.setattr(embed, "genai_client", lambda settings, credentials: client)
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"\xff\xd8jpeg"))
    monkeypatch.setattr(embed.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))

    args = ["--selected", str(FIXTURES / "selected.csv"), "--photos", str(FIXTURES / "photos.jsonl")]
    args += ["--real-details", str(FIXTURES / "real_details.jsonl"), "--enriched", str(tmp_path / "missing.jsonl")]
    args += ["--ids-file", str(ids_file), "--out", str(out)]
    assert embed.main(args) == 0

    assert set(read_jsonl_by_id(out)) == {"29493", "36067"}
    assert len(client.models.calls) == 2
    assert (out.parent / "embeddings_trial_failed.txt").read_text() == ""
    assert embed.main(args + ["--limit", "1"]) == 0
    assert len(client.models.calls) == 2, "nothing left to do on a second run"
    assert all(json.loads(line)["model"] == "gemini-embedding-2" for line in out.read_text().splitlines())

    monkeypatch.setenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-3")
    assert embed.main(args) == 0
    assert len(client.models.calls) == 4, "embeddings from another model are made again"
    assert {row["model"] for row in read_jsonl_by_id(out).values()} == {"gemini-embedding-3"}
