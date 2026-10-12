import csv
import io
import json
import os
import uuid
from pathlib import Path

import pytest
from PIL import Image

from data_prep import get_photos
from data_prep.get_photos import (
    DATASET,
    IMAGE_PATH,
    GcsStore,
    KaggleFiles,
    LocalStore,
    finished_ids,
    make_versions,
    parse_args,
    read_ids,
    run,
)
from data_prep.kaggle_zip import KaggleRateLimited

SELECTED_COLUMNS = [
    "id", "title", "gender", "masterCategory", "subCategory", "articleType", "baseColour", "usage", "group"
]


def jpeg(size: tuple[int, int], colour=(180, 40, 40), exif: Image.Exif | None = None) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "JPEG", quality=92, **({"exif": exif} if exif else {}))
    return buffer.getvalue()


def png_with_transparency(size: tuple[int, int]) -> bytes:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    image.paste((20, 120, 20, 255), (0, 0, size[0] // 2, size[1]))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def opened(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


class FakeDownloads:
    """Stands in for the dataset: a synthetic photo per id, or a failure for the broken ones."""

    def __init__(self, size=(1800, 2400), broken: set[str] = frozenset()):
        self.size = size
        self.broken = set(broken)
        self.calls: list[str] = []

    def __call__(self, product_id: str) -> bytes:
        self.calls.append(product_id)
        if product_id in self.broken:
            raise ConnectionError(f"Kaggle refused {product_id}")
        return jpeg(self.size)


class MemoryStore:
    def __init__(self, bucket: str = "test-bucket"):
        self.bucket = bucket
        self.saved: dict[str, bytes] = {}

    def save(self, path: str, data: bytes) -> str:
        self.saved[path] = data
        return f"https://storage.googleapis.com/{self.bucket}/{path}"


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_quietly(ids, download, store, tmp_path: Path, **kwargs):
    return run(ids, download, store, tmp_path / "photos.jsonl", tmp_path / "photos_failed.txt", delay=0, **kwargs)


# ---------- Resize rules ----------


def test_portrait_photo_gets_exact_long_sides():
    versions, size = make_versions(jpeg((1800, 2400)))
    assert size == (1800, 2400)
    assert opened(versions["original"]).size == (1800, 2400)
    assert opened(versions["gemini"]).size == (750, 1000)
    assert opened(versions["thumb"]).size == (300, 400)


def test_landscape_photo_uses_width_as_long_side():
    versions, _ = make_versions(jpeg((2400, 1600)))
    assert opened(versions["gemini"]).size == (1000, 667)
    assert opened(versions["thumb"]).size == (400, 267)


@pytest.mark.parametrize(
    ("size", "gemini", "thumb"),
    [
        ((600, 800), (600, 800), (300, 400)),
        ((300, 200), (300, 200), (300, 200)),
        ((60, 80), (60, 80), (60, 80)),
        ((1000, 1000), (1000, 1000), (400, 400)),
    ],
)
def test_small_photos_are_never_upscaled(size, gemini, thumb):
    versions, _ = make_versions(jpeg(size))
    assert opened(versions["gemini"]).size == gemini
    assert opened(versions["thumb"]).size == thumb


def test_every_version_is_an_rgb_jpeg_at_its_quality_setting():
    versions, _ = make_versions(jpeg((1800, 2400)))
    for name in ("original", "gemini", "thumb"):
        image = opened(versions[name])
        assert (image.format, image.mode) == ("JPEG", "RGB")
    assert len(versions["thumb"]) < len(versions["gemini"]) < len(versions["original"])


def test_plain_jpeg_original_is_kept_as_downloaded():
    data = jpeg((1080, 1440))
    versions, _ = make_versions(data)
    assert versions["original"] == data


def test_png_with_transparency_becomes_rgb_jpeg_on_white():
    versions, size = make_versions(png_with_transparency((1200, 1600)))
    assert size == (1200, 1600)
    for name in ("original", "gemini", "thumb"):
        image = opened(versions[name])
        assert (image.format, image.mode) == ("JPEG", "RGB")
        right_half = image.getpixel((image.width - 5, image.height // 2))
        left_half = image.getpixel((5, image.height // 2))
        assert min(right_half) > 245, f"{name}: transparent area should be white, got {right_half}"
        assert left_half[1] > left_half[0] + 50, f"{name}: the green half should stay green"
    assert opened(versions["gemini"]).size == (750, 1000)


@pytest.mark.parametrize("mode", ["L", "CMYK", "P"])
def test_other_colour_modes_are_converted_to_rgb(mode):
    buffer = io.BytesIO()
    image = Image.new("RGB", (500, 700), (90, 90, 200)).convert(mode)
    image.save(buffer, "PNG" if mode == "P" else "JPEG")
    versions, size = make_versions(buffer.getvalue())
    assert size == (500, 700)
    for name in ("original", "gemini", "thumb"):
        assert opened(versions[name]).mode == "RGB"


def test_sideways_camera_photo_is_turned_upright():
    exif = Image.Exif()
    exif[0x0112] = 6
    versions, size = make_versions(jpeg((2400, 1800), exif=exif))
    assert size == (1800, 2400)
    assert opened(versions["original"]).size == (1800, 2400)
    assert opened(versions["gemini"]).size == (750, 1000)


# ---------- Running over products ----------


def test_three_versions_are_written_and_listed(tmp_path):
    store = LocalStore(tmp_path / "photos")
    summary = run_quietly(["15970", "59263"], FakeDownloads(), store, tmp_path)

    assert (summary.done, summary.skipped, summary.failed) == (2, 0, [])
    for product_id in ("15970", "59263"):
        for name in ("original", "gemini", "thumb"):
            assert (tmp_path / "photos" / name / f"{product_id}.jpg").is_file()
    assert opened((tmp_path / "photos" / "gemini" / "15970.jpg").read_bytes()).size == (750, 1000)


def test_photos_jsonl_has_one_row_per_product_with_links_and_size(tmp_path):
    run_quietly(["15970", "59263"], FakeDownloads(), MemoryStore(), tmp_path)

    by_id = {row["id"]: row for row in rows(tmp_path / "photos.jsonl")}
    assert set(by_id) == {"15970", "59263"}
    assert by_id["15970"] == {
        "id": "15970",
        "original": "https://storage.googleapis.com/test-bucket/original/15970.jpg",
        "gemini": "https://storage.googleapis.com/test-bucket/gemini/15970.jpg",
        "thumb": "https://storage.googleapis.com/test-bucket/thumb/15970.jpg",
        "width": 1800,
        "height": 2400,
    }


def test_store_receives_the_three_paths(tmp_path):
    store = MemoryStore()
    run_quietly(["7"], FakeDownloads(), store, tmp_path)
    assert set(store.saved) == {"original/7.jpg", "gemini/7.jpg", "thumb/7.jpg"}


def test_second_run_skips_finished_products(tmp_path):
    download = FakeDownloads()
    run_quietly(["1", "2"], download, MemoryStore(), tmp_path)
    download.calls.clear()

    summary = run_quietly(["1", "2", "3"], download, MemoryStore(), tmp_path)

    assert download.calls == ["3"]
    assert (summary.done, summary.skipped) == (1, 2)
    assert sorted(row["id"] for row in rows(tmp_path / "photos.jsonl")) == ["1", "2", "3"]


def test_failing_download_is_recorded_and_others_finish(tmp_path):
    download = FakeDownloads(broken={"2"})
    summary = run_quietly(["1", "2", "3"], download, MemoryStore(), tmp_path, attempts=3, workers=2)

    assert summary.failed == ["2"]
    assert summary.done == 2
    assert download.calls.count("2") == 3
    assert sorted(row["id"] for row in rows(tmp_path / "photos.jsonl")) == ["1", "3"]
    assert (tmp_path / "photos_failed.txt").read_text() == "2\n"


def test_failed_products_are_retried_on_the_next_run(tmp_path):
    download = FakeDownloads(broken={"2"})
    run_quietly(["1", "2"], download, MemoryStore(), tmp_path, attempts=1)
    download.broken.clear()

    summary = run_quietly(["1", "2"], download, MemoryStore(), tmp_path, attempts=1)

    assert (summary.done, summary.skipped, summary.failed) == (1, 1, [])
    assert (tmp_path / "photos_failed.txt").read_text() == ""


def test_broken_photo_file_counts_as_failed(tmp_path):
    def download(product_id: str) -> bytes:
        return b"<html>not a photo</html>" if product_id == "bad" else jpeg((800, 800))

    summary = run_quietly(["good", "bad"], download, MemoryStore(), tmp_path, attempts=1)
    assert (summary.done, summary.failed) == (1, ["bad"])


def test_half_written_last_line_does_not_break_resuming(tmp_path):
    out = tmp_path / "photos.jsonl"
    finished = {"id": "1", "original": "a", "gemini": "b", "thumb": "c", "width": 1, "height": 1}
    out.write_text(json.dumps(finished) + '\n{"id": "2", "orig')

    assert finished_ids(out) == {"1"}
    summary = run_quietly(["1", "2"], FakeDownloads(), MemoryStore(), tmp_path)

    assert (summary.done, summary.skipped) == (1, 1)
    assert finished_ids(out) == {"1", "2"}


def test_read_ids_keeps_order_and_drops_duplicates(tmp_path):
    selected = tmp_path / "selected.csv"
    with selected.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SELECTED_COLUMNS)
        for product_id in ("15970", " 59263 ", "15970", ""):
            writer.writerow([product_id, "t", "Men", "Apparel", "Topwear", "Shirts", "Blue", "Casual", "tops"])
    assert read_ids(selected) == ["15970", "59263"]


# ---------- Cloud Storage and Kaggle wiring ----------


class FakeBlob:
    def __init__(self, uploads: list, path: str):
        self.uploads = uploads
        self.path = path
        self.cache_control = None

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self.uploads.append((self.path, data, content_type, self.cache_control))


class FakeClient:
    uploads: list = []
    projects: list = []

    def __init__(self, project=None):
        FakeClient.projects.append(project)

    def bucket(self, name: str):
        uploads = self.uploads

        class Bucket:
            def blob(self, path: str) -> FakeBlob:
                return FakeBlob(uploads, f"{name}/{path}")

        return Bucket()


def test_gcs_store_uploads_jpeg_with_cache_control(monkeypatch):
    FakeClient.uploads, FakeClient.projects = [], []
    monkeypatch.setattr("google.cloud.storage.Client", FakeClient)

    link = GcsStore("ecommai-catalog", project="ecommai").save("gemini/15970.jpg", b"jpeg")

    assert link == "https://storage.googleapis.com/ecommai-catalog/gemini/15970.jpg"
    assert FakeClient.uploads == [("ecommai-catalog/gemini/15970.jpg", b"jpeg", "image/jpeg", "public, max-age=604800")]
    assert FakeClient.projects == ["ecommai"]


def test_kaggle_files_asks_kagglehub_for_one_file(monkeypatch, tmp_path):
    for name in ("KAGGLEHUB_CACHE", "TQDM_DISABLE", "KAGGLEHUB_VERBOSITY"):
        monkeypatch.setenv(name, "placeholder")
    calls = []

    def fake_download(handle, path=None, **kwargs):
        calls.append((handle, path))
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"photo")
        return str(target)

    monkeypatch.setattr("kagglehub.dataset_download", fake_download)
    files = KaggleFiles(DATASET, IMAGE_PATH, cache_dir=tmp_path / "cache")

    assert files("15970") == b"photo"
    assert calls == [("paramaggarwal/fashion-product-images-dataset/versions/1", "fashion-dataset/images/15970.jpg")]
    assert os.environ["KAGGLEHUB_CACHE"] == str(tmp_path / "cache")


# ---------- Command line ----------


def test_cloud_mode_needs_a_bucket(monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    with pytest.raises(SystemExit):
        parse_args([])


def test_bucket_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET", "ecommai-catalog")
    args = parse_args([])
    assert args.bucket == "ecommai-catalog"
    assert args.out == Path("data/photos.jsonl")
    assert args.failed == Path("data/photos_failed.txt")


def test_local_only_keeps_its_progress_file_in_the_folder(monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    args = parse_args(["--local-only", "trial"])
    assert args.out == Path("trial/photos.jsonl")
    assert args.failed == Path("trial/photos_failed.txt")


def test_main_local_only_end_to_end(monkeypatch, tmp_path, capsys):
    selected = tmp_path / "selected.csv"
    with selected.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SELECTED_COLUMNS)
        writer.writerow(["15970", "Turtle Shirt", "Men", "Apparel", "Topwear", "Shirts", "Navy Blue", "Casual", "tops"])
        writer.writerow(["59263", "Titan Watch", "Women", "Accessories", "Watches", "Watches", "Silver", "", "watches"])
    fake = FakeDownloads(broken={"59263"})
    sources = []
    monkeypatch.setattr(get_photos, "open_source", lambda *args: sources.append(args) or fake)

    get_photos.main(["--selected", str(selected), "--local-only", str(tmp_path / "out"), "--attempts", "1"])

    printed = capsys.readouterr().out
    assert "done 1, skipped 0 (already finished), failed 1" in printed
    assert [row["id"] for row in rows(tmp_path / "out" / "photos.jsonl")] == ["15970"]
    assert (tmp_path / "out" / "photos_failed.txt").read_text() == "59263\n"
    assert (tmp_path / "out" / "thumb" / "15970.jpg").is_file()
    assert sources == [
        ("zip", DATASET, IMAGE_PATH, Path("downloads/kagglehub"), Path("downloads/kaggle_zip_index.json"))
    ]


def test_open_source_picks_zip_or_kagglehub(monkeypatch, tmp_path):
    opened_zips = []

    class FakeZip:
        def __init__(self, dataset, index_path):
            opened_zips.append((dataset, index_path))

        def files(self, pattern):
            return lambda product_id: pattern.format(id=product_id).encode()

    monkeypatch.setattr(get_photos, "KaggleZip", FakeZip)
    monkeypatch.setattr(get_photos, "KaggleFiles", lambda *args: ("kagglehub", *args))

    download = get_photos.open_source("zip", DATASET, IMAGE_PATH, tmp_path, tmp_path / "index.json")
    assert download("15970") == b"fashion-dataset/images/15970.jpg"
    assert opened_zips == [(DATASET, tmp_path / "index.json")]
    assert get_photos.open_source("kagglehub", DATASET, IMAGE_PATH, tmp_path, tmp_path / "i.json") == (
        "kagglehub", DATASET, IMAGE_PATH, tmp_path
    )


def test_rate_limit_is_not_retried_and_reported_once(tmp_path, capsys):
    calls = []

    def limited(product_id):
        calls.append(product_id)
        raise KaggleRateLimited("1455")

    out, failed = tmp_path / "p.jsonl", tmp_path / "f.txt"
    summary = run(["1", "2", "3"], limited, MemoryStore(), out, failed, workers=1, attempts=4, delay=0)

    assert calls == ["1", "2", "3"]
    assert sorted(summary.failed) == ["1", "2", "3"]
    assert "1455 seconds" in summary.rate_limited
    assert capsys.readouterr().out.count("stopping:") == 1


# ---------- Real services (run with: pytest -m live) ----------


@pytest.mark.live
def test_live_kaggle_full_size_photo(tmp_path):
    photo = KaggleFiles(DATASET, IMAGE_PATH, cache_dir=tmp_path)("15970")
    versions, size = make_versions(photo)
    assert size == (1800, 2400)
    assert max(opened(versions["gemini"]).size) == 1000


@pytest.mark.live
def test_live_gcs_upload():
    bucket = os.environ.get("GCS_BUCKET")
    if not bucket:
        pytest.skip("GCS_BUCKET is not set")
    from google.cloud import storage

    path = f"live-test/{uuid.uuid4().hex}.jpg"
    link = GcsStore(bucket, os.environ.get("GOOGLE_CLOUD_PROJECT")).save(path, jpeg((40, 40)))
    blob = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT")).bucket(bucket).get_blob(path)
    try:
        assert link == f"https://storage.googleapis.com/{bucket}/{path}"
        assert (blob.content_type, blob.cache_control) == ("image/jpeg", "public, max-age=604800")
    finally:
        blob.delete()
