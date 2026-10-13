import json
from pathlib import Path
from types import SimpleNamespace

from google.genai import types

from data_prep import enrich
from data_prep.enrich import Settings, apply_batch_results, load_products
from data_prep.schemas import VOCABULARY
from data_prep.vertex_batch import BatchItem, load_state, parse_output, request_line, request_signature, run_job

FIXTURES = Path(__file__).parent / "fixtures" / "enrich"


def products() -> dict[str, enrich.ProductInput]:
    return {product.id: product for product in load_products(FIXTURES / "selected.csv", FIXTURES / "photos.jsonl", FIXTURES / "real_details.jsonl")}


def answer(group: str) -> str:
    details = {
        name: {"note": f"{name} seen", "value": allowed[0], "confidence": 0.9}
        for name, allowed in VOCABULARY[group].items()
    }
    return json.dumps({"details": details, "description": "A plain test product."})


def response(text: str, prompt: int = 10, output: int = 4) -> dict:
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": output, "thoughtsTokenCount": 0},
    }


class FakeJobs:
    def __init__(self, state: str = "JOB_STATE_SUCCEEDED") -> None:
        self.state = state
        self.created: list[str] = []
        self.resource = "projects/p/locations/global/batchPredictionJobs/9"

    def create(self, *, model: str, src: str, config: object) -> SimpleNamespace:
        self.created.append(src)
        return SimpleNamespace(name=self.resource, state="JOB_STATE_PENDING")

    def get(self, *, name: str) -> SimpleNamespace:
        return SimpleNamespace(
            name=name, state=self.state, error=None,
            completion_stats=SimpleNamespace(successful_count=1, failed_count=0, incomplete_count=0),
        )


def test_request_line_has_the_key_the_photo_and_only_this_groups_values():
    line = enrich.enrich_line(products()["8796"], Settings(model="gemini-3.8-flash"))
    request = line["request"]
    assert line["key"] == "8796"
    photo = request["contents"][0]["parts"][0]["fileData"]
    assert photo["fileUri"] == "gs://test-bucket/gemini/8796.jpg"
    assert photo["mimeType"] == "image/jpeg"
    config = request["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert "responseSchema" not in config
    assert config["thinkingConfig"] == {"thinkingLevel": "LOW"}
    assert "confidence is a number from 0 to 1" in request["systemInstruction"]["parts"][0]["text"]
    assert "sleeve_length" in request["contents"][0]["parts"][1]["text"]
    assert "skinny" not in request["contents"][0]["parts"][1]["text"]
    flat = json.dumps({
        "description": "A plain test product.",
        "notes": "sleeve_length: full sleeves seen",
        "sleeve_length": "full",
        "sleeve_length_c": "c8",
        **{name: allowed[0] for name, allowed in VOCABULARY["tops"].items() if name != "sleeve_length"},
        **{f"{name}_c": "c5" for name in VOCABULARY["tops"] if name != "sleeve_length"},
    })
    parsed = json.loads(enrich.expand_batch_text("tops", flat))
    assert parsed["details"]["sleeve_length"] == {"note": "full sleeves seen", "value": "full", "confidence": 0.8}


def test_results_map_back_by_key_and_by_the_echoed_request():
    line = request_line("k1", ["What colour is it?"], model="gemini-3.8-flash")
    echoed = {"status": "", "request": line["request"], "response": response("blue", prompt=8, output=2)}
    dropped_key = dict(echoed)
    found = parse_output([json.dumps(echoed), json.dumps(dropped_key)], {request_signature(line["request"]): "k1"})
    assert set(found) == {"k1"}
    assert found["k1"].text == "blue"
    assert found["k1"].prompt_tokens == 8
    assert found["k1"].output_tokens == 2


def test_a_failed_line_is_an_error_and_does_not_count_as_text():
    raw = json.dumps({"key": "k2", "status": "Bad Request: invalid role", "request": {}, "response": {}})
    found = parse_output([raw], {})
    assert found["k2"].text is None
    assert "invalid role" in found["k2"].error


def test_run_job_reattaches_instead_of_submitting_twice(tmp_path: Path):
    line = request_line("k1", ["hello"], model="gemini-3.8-flash")
    output = [json.dumps({"key": "k1", "request": line["request"], "response": response("ok"), "status": ""})]
    jobs = FakeJobs()
    grants: list[str] = []
    shared = dict(
        model="gemini-3.8-flash", bucket="b", project="p", state_dir=tmp_path, poll_seconds=0,
        jobs=jobs, upload=lambda uri, path: None, download=lambda uri: output,
        grant_access=lambda: grants.append("grant") or "granted", sleep=lambda seconds: None, log=lambda message: None,
    )
    first = run_job("enrich-test", [line], **shared)
    assert first["k1"].text == "ok"
    assert jobs.created == ["gs://b/batch/enrich-test/input.jsonl"]
    assert grants == ["grant"]
    saved = load_state(tmp_path, "enrich-test")
    assert saved is not None and saved.resource == jobs.resource
    second = run_job("enrich-test", [line], **shared)
    assert second["k1"].text == "ok"
    assert len(jobs.created) == 1
    assert grants == ["grant"]
    changed = request_line("k1", ["a different question"], model="gemini-3.8-flash")
    run_job("enrich-test", [changed], **shared)
    assert len(jobs.created) == 2


def test_missing_and_invalid_batch_rows_fall_back_to_sync(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(enrich, "load_dotenv", lambda: None)
    loaded = products()
    out = tmp_path / "enriched.jsonl"

    def fake_job(name, lines, **kwargs):
        items = {}
        for line in lines:
            if line["key"] == "8796":
                items[line["key"]] = BatchItem(line["key"], text=answer("tops"), prompt_tokens=20, output_tokens=10)
            else:
                items[line["key"]] = BatchItem(line["key"], error="empty answer")
        return items

    class Models:
        def generate_content(self, *, model, contents, config):
            product_id = contents[0].file_data.file_uri.rsplit("/", 1)[-1].removesuffix(".jpg")
            return SimpleNamespace(text=answer(loaded[product_id].group), usage_metadata=SimpleNamespace(
                prompt_token_count=4, cached_content_token_count=0, candidates_token_count=2, thoughts_token_count=0,
            ))

    argv = [
        "--mode", "batch", "--selected", str(FIXTURES / "selected.csv"), "--photos", str(FIXTURES / "photos.jsonl"),
        "--details", str(FIXTURES / "real_details.jsonl"), "--out", str(out), "--job", "enrich-test",
        "--fallback-concurrency", "2",
    ]
    assert enrich.main(argv, client=SimpleNamespace(models=Models()), run_batch_job=fake_job) == 0
    written = {json.loads(line)["id"] for line in out.read_text().splitlines()}
    assert written == {"8796", "11006", "18164"}
    runs = json.loads(out.with_suffix(".usage.json").read_text())
    assert [run["mode"] for run in runs] == ["batch", "sync"]
    expected = enrich.Usage(prompt_tokens=20, output_tokens=10).cost(batch=True)
    assert runs[0]["cost_usd"] == round(expected, 5)


def test_bad_batch_answer_is_not_written(tmp_path: Path):
    product = products()["8796"]
    out = tmp_path / "enriched.jsonl"
    items = {"8796": BatchItem("8796", text=answer("tops").replace("sleeveless", "long", 1))}
    result = apply_batch_results([product], items, Settings(), out)
    assert result.written == 0
    assert "sleeve_length" in result.failed["8796"]
    assert not out.read_text()


def test_main_sync_mode_is_unchanged(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(enrich, "load_dotenv", lambda: None)
    out = tmp_path / "enriched.jsonl"
    called = []

    def boom(*args, **kwargs):
        called.append(1)
        raise AssertionError("batch job should not run")

    argv = [
        "--selected", str(FIXTURES / "selected.csv"), "--photos", str(FIXTURES / "photos.jsonl"),
        "--details", str(FIXTURES / "real_details.jsonl"), "--out", str(out), "--limit", "1",
    ]

    class Models:
        def generate_content(self, *, model, contents, config):
            return SimpleNamespace(text=answer("tops"), usage_metadata=None)

    assert enrich.main(argv, client=SimpleNamespace(models=Models()), run_batch_job=boom) == 0
    assert called == []
    assert len(out.read_text().splitlines()) == 1


def test_identical_requests_still_count_as_unchanged():
    from data_prep.vertex_batch import JobRecord, request_signature, requests_unchanged

    lines = [{"key": "a", "request": {"x": 1}}, {"key": "b", "request": {"x": 1}}]
    record = JobRecord(
        name="j", resource="r", location="global", model="m", input_uri="in", output_uri="out", keys=["a", "b"],
        key_signatures={line["key"]: request_signature(line["request"]) for line in lines},
    )
    assert requests_unchanged(record, lines)
    assert record.signatures == {request_signature({"x": 1}): "a"}


def test_old_state_files_with_signature_to_key_still_load():
    from data_prep.vertex_batch import JobRecord

    record = JobRecord.from_json(
        {"name": "j", "resource": "r", "location": "global", "model": "m", "input_uri": "i", "output_uri": "o",
         "keys": ["a"], "signatures": {"sig-a": "a"}}
    )
    assert record.key_signatures == {"a": "sig-a"}
