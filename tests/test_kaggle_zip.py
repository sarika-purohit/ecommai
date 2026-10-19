import base64
import io
import json
import os
import random
import re
import struct
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from data_prep.kaggle_zip import (
    BearerAuth,
    Handle,
    KaggleError,
    KaggleRateLimited,
    KaggleZip,
    RangeFile,
    RangeNotSupported,
    kaggle_auth,
)

DATASET = "paramaggarwal/fashion-product-images-dataset/versions/1"
STORAGE = "storage.googleapis.com"


def random_bytes(size: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(size)


def style_json(product_id: int) -> bytes:
    return json.dumps({"data": {"id": product_id, "brandName": "Turtle", "price": 1299 + product_id}}).encode()


def build_zip(path: Path) -> dict[str, bytes]:
    """A small zip laid out like the Kaggle one: stored photos, deflated JSON and CSV, a folder entry, a zip64
    member and a member whose local extra field is bigger than the reader's first guess."""
    contents = {f"fashion-dataset/images/{i}.jpg": random_bytes(5_000 + i * 37, i) for i in range(1, 41)}
    contents |= {f"fashion-dataset/styles/{i}.json": style_json(i) for i in range(1, 41)}
    contents["fashion-dataset/styles.csv"] = b"id,gender\n" + b"".join(f"{i},Men\n".encode() for i in range(1, 41))
    contents["fashion-dataset/images/zip64.jpg"] = random_bytes(3_000, 99)
    contents["fashion-dataset/images/big-extra.jpg"] = random_bytes(2_000, 98)

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(zipfile.ZipInfo("fashion-dataset/images/"), b"")
        for name, data in contents.items():
            info = zipfile.ZipInfo(name, date_time=(2019, 3, 14, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if name.endswith(".jpg") else zipfile.ZIP_DEFLATED
            if name.endswith("big-extra.jpg"):
                info.extra = struct.pack("<HH", 0xCAFE, 700) + b"x" * 700
            with archive.open(info, "w", force_zip64=name.endswith("zip64.jpg")) as member:
                member.write(data)
    return contents


class FakeKaggle:
    """Kaggle's download endpoint plus Cloud Storage, behind an httpx.MockTransport."""

    def __init__(self, archive: bytes):
        self.archive = archive
        self.kaggle_requests: list[httpx.Request] = []
        self.ranges: list[str | None] = []
        self.storage_auth: list[str | None] = []
        self.links = 0
        self.expired: set[str] = set()
        self.expire_every_link = False
        self.rate_limited = False
        self.honour_range = True

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.kaggle.com":
            self.kaggle_requests.append(request)
            if self.rate_limited:
                return httpx.Response(429, headers={"retry-after": "1455"}, json={"code": 429})
            self.links += 1
            link = f"https://{STORAGE}/kaggle-data-sets/1/archive.zip?X-Goog-Signature=sig{self.links}"
            return httpx.Response(302, headers={"location": link})

        assert request.url.host == STORAGE
        self.storage_auth.append(request.headers.get("authorization"))
        signature = request.url.params["X-Goog-Signature"]
        if self.expire_every_link or signature in self.expired:
            return httpx.Response(400, text="<Error><Code>ExpiredToken</Code></Error>")
        wanted = request.headers.get("range")
        self.ranges.append(wanted)
        if not self.honour_range or wanted is None:
            return httpx.Response(200, content=self.archive)
        first, last = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", wanted).groups())
        last = min(last, len(self.archive) - 1)
        return httpx.Response(
            206,
            headers={"content-range": f"bytes {first}-{last}/{len(self.archive)}"},
            content=self.archive[first : last + 1],
        )

    def expire_current_link(self) -> None:
        self.expired.add(f"sig{self.links}")


@pytest.fixture
def contents_and_server(tmp_path):
    contents = build_zip(tmp_path / "archive.zip")
    return contents, FakeKaggle((tmp_path / "archive.zip").read_bytes())


def open_zip(server: FakeKaggle, tmp_path: Path, **kwargs) -> KaggleZip:
    kwargs.setdefault("auth", BearerAuth("KGAT_test"))
    return KaggleZip(DATASET, index_path=tmp_path / "index.json", client=server.client(), **kwargs)


# ---------- Reading members ----------


def test_fetch_returns_every_member_unchanged(contents_and_server, tmp_path):
    contents, server = contents_and_server
    archive = open_zip(server, tmp_path)

    for name, data in contents.items():
        assert archive.fetch(name) == data
    assert "fashion-dataset/images/" not in archive.members
    assert archive.members["fashion-dataset/images/1.jpg"].compress_type == zipfile.ZIP_STORED
    assert archive.members["fashion-dataset/styles/1.json"].compress_type == zipfile.ZIP_DEFLATED


def test_a_small_member_costs_one_range_request(contents_and_server, tmp_path):
    contents, server = contents_and_server
    archive = open_zip(server, tmp_path)
    before = len(server.ranges)

    assert archive.fetch("fashion-dataset/images/7.jpg") == contents["fashion-dataset/images/7.jpg"]
    assert len(server.ranges) == before + 1


def test_large_local_extra_field_takes_a_second_request(contents_and_server, tmp_path):
    contents, server = contents_and_server
    archive = open_zip(server, tmp_path)
    before = len(server.ranges)

    assert archive.fetch("fashion-dataset/images/big-extra.jpg") == contents["fashion-dataset/images/big-extra.jpg"]
    assert len(server.ranges) == before + 2


def test_files_fills_in_the_product_id(contents_and_server, tmp_path):
    contents, server = contents_and_server
    styles = open_zip(server, tmp_path).files("fashion-dataset/styles/{id}.json")
    assert json.loads(styles("12")) == json.loads(contents["fashion-dataset/styles/12.json"])


def test_missing_member_is_a_key_error(contents_and_server, tmp_path):
    _, server = contents_and_server
    with pytest.raises(KeyError, match="fashion-dataset/images/42431.jpg"):
        open_zip(server, tmp_path).fetch("fashion-dataset/images/42431.jpg")


def test_corrupt_data_fails_the_checksum(contents_and_server, tmp_path):
    _, server = contents_and_server
    archive = open_zip(server, tmp_path)
    member = archive.members["fashion-dataset/images/3.jpg"]
    archive.members["fashion-dataset/images/3.jpg"] = member._replace(crc=member.crc ^ 1)
    with pytest.raises(zipfile.BadZipFile, match="checksum"):
        archive.fetch("fashion-dataset/images/3.jpg")


def test_parallel_fetches(contents_and_server, tmp_path):
    contents, server = contents_and_server
    archive = open_zip(server, tmp_path)
    names = list(contents) * 3

    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(archive.fetch, names))

    assert fetched == [contents[name] for name in names]
    assert len(server.kaggle_requests) == 1


def test_zip64_end_of_central_directory(tmp_path):
    path = tmp_path / "many.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        for i in range(70_000):
            archive.writestr(f"fashion-dataset/styles/{i}.json", b"{}")
        archive.writestr("fashion-dataset/images/1.jpg", b"photo")
    server = FakeKaggle(path.read_bytes())
    assert b"PK\x06\x06" in server.archive[-200:]

    archive = open_zip(server, tmp_path)

    assert len(archive.members) == 70_001
    assert archive.fetch("fashion-dataset/images/1.jpg") == b"photo"
    assert archive.fetch("fashion-dataset/styles/69999.json") == b"{}"


# ---------- Index cache ----------


def test_index_is_cached_and_reused(contents_and_server, tmp_path):
    contents, server = contents_and_server
    open_zip(server, tmp_path)
    cached = json.loads((tmp_path / "index.json").read_text())
    assert cached["dataset"] == DATASET
    assert cached["size"] == len(server.archive)
    assert set(cached["members"]) == set(contents)

    server.ranges.clear()
    archive = open_zip(server, tmp_path)
    assert server.ranges == ["bytes=0-0"]
    assert archive.fetch("fashion-dataset/images/5.jpg") == contents["fashion-dataset/images/5.jpg"]


def test_index_for_another_zip_is_rebuilt(contents_and_server, tmp_path, capsys):
    contents, server = contents_and_server
    (tmp_path / "index.json").write_text(json.dumps({"dataset": DATASET, "size": 1, "members": {}}))

    archive = open_zip(server, tmp_path)

    assert set(archive.members) == set(contents)
    assert "different zip" in capsys.readouterr().out
    assert json.loads((tmp_path / "index.json").read_text())["size"] == len(server.archive)


def test_index_is_read_with_few_requests(contents_and_server, tmp_path):
    _, server = contents_and_server
    open_zip(server, tmp_path)
    assert len(server.ranges) <= 6


# ---------- Kaggle and Cloud Storage replies ----------


def test_one_kaggle_call_with_the_token_and_version(contents_and_server, tmp_path, capsys):
    _, server = contents_and_server
    open_zip(server, tmp_path).fetch("fashion-dataset/images/1.jpg")

    [request] = server.kaggle_requests
    assert request.method == "GET"
    assert request.url.path == "/api/v1/datasets/download/paramaggarwal/fashion-product-images-dataset"
    assert request.url.params["datasetVersionNumber"] == "1"
    assert request.headers["authorization"] == "Bearer KGAT_test"
    assert set(server.storage_auth) == {None}
    assert server.ranges[0] == "bytes=0-0"
    printed = capsys.readouterr().out
    assert "Kaggle API call 1: GET https://www.kaggle.com/api/v1/datasets/download/" in printed
    assert f"redirected to {STORAGE}" in printed
    assert "KGAT_test" not in printed


def test_expired_link_is_refreshed_once(contents_and_server, tmp_path):
    contents, server = contents_and_server
    archive = open_zip(server, tmp_path)
    server.expire_current_link()

    assert archive.fetch("fashion-dataset/images/2.jpg") == contents["fashion-dataset/images/2.jpg"]
    assert archive.fetch("fashion-dataset/images/3.jpg") == contents["fashion-dataset/images/3.jpg"]
    assert len(server.kaggle_requests) == 2


def test_parallel_fetches_share_one_refresh(contents_and_server, tmp_path):
    contents, server = contents_and_server
    archive = open_zip(server, tmp_path)
    server.expire_current_link()
    names = [n for n in contents if n.endswith(".jpg")]

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(archive.fetch, names)) == [contents[n] for n in names]
    assert len(server.kaggle_requests) == 2


def test_refreshes_are_capped(contents_and_server, tmp_path):
    _, server = contents_and_server
    archive = open_zip(server, tmp_path, max_link_requests=3)
    server.expire_every_link = True

    for _ in range(5):
        with pytest.raises((httpx.HTTPStatusError, KaggleError)):
            archive.fetch("fashion-dataset/images/2.jpg")
    assert len(server.kaggle_requests) == 3


def test_server_ignoring_range_is_a_clear_error(contents_and_server, tmp_path):
    _, server = contents_and_server
    server.honour_range = False
    with pytest.raises(RangeNotSupported, match="ignored the Range header"):
        open_zip(server, tmp_path)


def test_rate_limit_stops_with_the_retry_time(contents_and_server, tmp_path):
    _, server = contents_and_server
    server.rate_limited = True
    with pytest.raises(KaggleRateLimited, match="retry after 1455 seconds, at about") as caught:
        open_zip(server, tmp_path)
    assert caught.value.retry_after == "1455"
    assert len(server.kaggle_requests) == 1


def test_rate_limit_during_a_refresh_stops_further_calls(contents_and_server, tmp_path):
    _, server = contents_and_server
    archive = open_zip(server, tmp_path)
    server.expire_current_link()
    server.rate_limited = True

    for _ in range(3):
        with pytest.raises(KaggleRateLimited):
            archive.fetch("fashion-dataset/images/2.jpg")
    assert len(server.kaggle_requests) == 2


def test_kaggle_answering_without_a_redirect(contents_and_server, tmp_path):
    _, server = contents_and_server
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={"message": "nope"})))
    with pytest.raises(KaggleError, match="HTTP 403 instead of a redirect"):
        KaggleZip(DATASET, index_path=tmp_path / "i.json", client=client, auth=BearerAuth("KGAT_test"))


# ---------- Credentials and handles ----------


def test_token_comes_first(monkeypatch, tmp_path):
    monkeypatch.setenv("KAGGLE_API_TOKEN", "KGAT_abc")
    monkeypatch.setenv("KAGGLE_USERNAME", "someone")
    monkeypatch.setenv("KAGGLE_KEY", "key")
    auth = kaggle_auth(home=tmp_path)
    assert isinstance(auth, BearerAuth) and auth.token == "KGAT_abc"


def test_username_and_key_are_the_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("KAGGLE_API_TOKEN", raising=False)
    monkeypatch.setenv("KAGGLE_USERNAME", "someone")
    monkeypatch.setenv("KAGGLE_KEY", "key")
    request = next(kaggle_auth(home=tmp_path).auth_flow(httpx.Request("GET", "https://www.kaggle.com/")))
    assert request.headers["authorization"] == "Basic " + base64.b64encode(b"someone:key").decode()


def test_credentials_from_kaggle_folder(monkeypatch, tmp_path):
    for name in ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".kaggle").mkdir()
    (tmp_path / ".kaggle" / "kaggle.json").write_text(json.dumps({"username": "u", "key": "k"}))
    assert isinstance(kaggle_auth(home=tmp_path), httpx.BasicAuth)
    (tmp_path / ".kaggle" / "access_token").write_text("KGAT_file\n")
    assert kaggle_auth(home=tmp_path).token == "KGAT_file"


def test_no_credentials(monkeypatch, tmp_path):
    for name in ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(KaggleError, match="KAGGLE_API_TOKEN"):
        kaggle_auth(home=tmp_path)


def test_handles():
    assert Handle.parse(DATASET) == Handle("paramaggarwal", "fashion-product-images-dataset", 1)
    assert Handle.parse("a/b") == Handle("a", "b", None)
    assert str(Handle.parse(DATASET)) == DATASET
    with pytest.raises(ValueError):
        Handle.parse("a/b/c")


# ---------- RangeFile ----------


def test_range_file_reads_seeks_and_reads_ahead():
    data = bytes(range(256)) * 40
    calls = []

    def read_range(start, end):
        calls.append((start, end))
        return data[start:end]

    f = RangeFile(read_range, len(data), read_ahead=1000)
    assert f.read(10) == data[:10]
    assert f.read(10) == data[10:20]
    assert calls == [(0, 1000)]
    f.seek(-22, io.SEEK_END)
    assert f.read() == data[-22:]
    assert f.read(5) == b""
    f.seek(5000)
    assert f.read(3000) == data[5000:8000]
    assert calls[-1] == (5000, 8000)
    assert f.tell() == 8000
    with pytest.raises(ValueError):
        f.seek(-1)


# ---------- Real dataset (run with: pytest -m live) ----------


@pytest.mark.live
def test_live_one_photo_and_one_styles_json(tmp_path):
    if not (os.environ.get("KAGGLE_API_TOKEN") or os.environ.get("KAGGLE_KEY")):
        pytest.skip("no Kaggle credentials in the environment")
    archive = KaggleZip(DATASET, index_path=tmp_path / "index.json")

    started = time.perf_counter()
    photo = archive.fetch("fashion-dataset/images/15970.jpg")
    style = json.loads(archive.fetch("fashion-dataset/styles/15970.json"))

    assert photo[:3] == b"\xff\xd8\xff"
    assert style["data"]["brandName"] == "Turtle"
    assert len(archive.members) > 80_000
    assert archive.kaggle_calls == 1
    assert time.perf_counter() - started < 30
