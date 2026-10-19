"""Reads single files out of a Kaggle dataset's zip with HTTP range requests.

Kaggle rate-limits per-file downloads, and the whole zip (about 25 GB for the fashion dataset) is too big to keep.
One Kaggle API call returns a signed Cloud Storage link to the zip. After that, every file is a range request to
Cloud Storage: the zip's central directory gives each member's offset and size, so one photo costs one small request.

    archive = KaggleZip("paramaggarwal/fashion-product-images-dataset/versions/1")
    photo = archive.fetch("fashion-dataset/images/15970.jpg")
"""

import io
import json
import os
import struct
import threading
import zipfile
import zlib
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

import httpx

KAGGLE_DOWNLOAD = "https://www.kaggle.com/api/v1/datasets/download/{owner}/{slug}"
ZIP_INDEX = Path("downloads/kaggle_zip_index.json")
READ_AHEAD = 2 * 1024 * 1024
MAX_LINK_REQUESTS = 3
TIMEOUT = httpx.Timeout(60.0, connect=20.0)
IST = timezone(timedelta(hours=5, minutes=30), "IST")

LOCAL_HEADER_SIGNATURE = b"PK\x03\x04"
LOCAL_HEADER_SIZE = 30
# The local header's extra field isn't in the central directory; reading this much extra usually gets the header
# and the data in one request.
EXTRA_GUESS = 256


class KaggleError(RuntimeError):
    pass


class KaggleRateLimited(KaggleError):
    def __init__(self, retry_after: str | None):
        self.retry_after = retry_after
        super().__init__(f"Kaggle is rate limiting this account (HTTP 429); {retry_message(retry_after)}")


class RangeNotSupported(KaggleError):
    pass


class Member(NamedTuple):
    header_offset: int
    compress_size: int
    file_size: int
    compress_type: int
    crc: int


def retry_message(retry_after: str | None) -> str:
    if retry_after and retry_after.strip().isdigit():
        when = datetime.now(IST) + timedelta(seconds=int(retry_after))
        return f"retry after {int(retry_after)} seconds, at about {when:%H:%M} India time"
    if retry_after:
        return f"retry after {retry_after}"
    return "Kaggle didn't say when to retry"


class Handle(NamedTuple):
    owner: str
    slug: str
    version: int | None

    @classmethod
    def parse(cls, handle: str) -> "Handle":
        parts = handle.strip("/").split("/")
        if len(parts) == 2:
            return cls(parts[0], parts[1], None)
        if len(parts) == 4 and parts[2] == "versions" and parts[3].isdigit():
            return cls(parts[0], parts[1], int(parts[3]))
        raise ValueError(f"expected owner/dataset or owner/dataset/versions/N, got {handle!r}")

    def __str__(self) -> str:
        base = f"{self.owner}/{self.slug}"
        return f"{base}/versions/{self.version}" if self.version else base


# ---------- Asking Kaggle for the zip's link ----------


class BearerAuth(httpx.Auth):
    def __init__(self, token: str):
        self.token = token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


def kaggle_auth(home: Path | None = None) -> httpx.Auth:
    """Uses the same credentials as the kaggle tools: an API token first, then a username and key."""
    kaggle_dir = (home or Path.home()) / ".kaggle"
    token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    if not token and (kaggle_dir / "access_token").is_file():
        token = (kaggle_dir / "access_token").read_text(encoding="utf-8").strip()
    if token:
        return BearerAuth(token)

    username, key = os.environ.get("KAGGLE_USERNAME"), os.environ.get("KAGGLE_KEY")
    if not (username and key) and (kaggle_dir / "kaggle.json").is_file():
        saved = json.loads((kaggle_dir / "kaggle.json").read_text(encoding="utf-8"))
        username, key = saved.get("username"), saved.get("key")
    if username and key:
        return httpx.BasicAuth(username, key)
    raise KaggleError("no Kaggle credentials: set KAGGLE_API_TOKEN, or KAGGLE_USERNAME and KAGGLE_KEY, in .env")


def download_link(client: httpx.Client, handle: Handle, auth: httpx.Auth, call_number: int = 1) -> str:
    """Returns the signed link Kaggle redirects to, without following it."""
    url = KAGGLE_DOWNLOAD.format(owner=handle.owner, slug=handle.slug)
    params = {"datasetVersionNumber": str(handle.version)} if handle.version else {}
    print(f"Kaggle API call {call_number}: GET {httpx.URL(url, params=params)}")
    # Streamed so that a 200 carrying the zip itself isn't read into memory.
    with client.stream("GET", url, params=params, auth=auth, follow_redirects=False) as response:
        if response.status_code == 429:
            raise KaggleRateLimited(response.headers.get("retry-after"))
        location = response.headers.get("location")
        if response.is_redirect and location:
            print(f"  redirected to {urlsplit(location).hostname}")
            return location
        if response.status_code == 200:
            raise KaggleError("Kaggle sent the zip itself instead of a redirect to it")
        response.read()
        raise KaggleError(f"Kaggle answered HTTP {response.status_code} instead of a redirect: {response.text[:300]}")


# ---------- Reading parts of the zip ----------


class RangeFile(io.RawIOBase):
    """A read-only, seekable file over read_range(start, end), with a read-ahead buffer so small reads share
    requests. Not thread-safe; it's only used to read the central directory once."""

    def __init__(self, read_range: Callable[[int, int], bytes], size: int, read_ahead: int = READ_AHEAD):
        self._read_range = read_range
        self.size = size
        self.read_ahead = read_ahead
        self._pos = 0
        self._buffer = b""
        self._buffer_start = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self.size}[whence]
        if base + offset < 0:
            raise ValueError("negative seek position")
        self._pos = base + offset
        return self._pos

    def readinto(self, buffer) -> int:
        count = min(len(buffer), max(0, self.size - self._pos))
        if count == 0:
            return 0
        start, end = self._pos, self._pos + count
        if not (self._buffer_start <= start and end <= self._buffer_start + len(self._buffer)):
            self._buffer = self._read_range(start, min(self.size, max(end, start + self.read_ahead)))
            self._buffer_start = start
        offset = start - self._buffer_start
        buffer[:count] = self._buffer[offset : offset + count]
        self._pos = end
        return count


class KaggleZip:
    """Single files from a Kaggle dataset's zip. fetch() is safe to call from many threads."""

    def __init__(
        self,
        dataset: str,
        index_path: Path = ZIP_INDEX,
        client: httpx.Client | None = None,
        auth: httpx.Auth | None = None,
        read_ahead: int = READ_AHEAD,
        max_link_requests: int = MAX_LINK_REQUESTS,
    ):
        self.handle = Handle.parse(dataset)
        self.client = client or httpx.Client(timeout=TIMEOUT)
        self.auth = auth or kaggle_auth()
        self.read_ahead = read_ahead
        self.max_link_requests = max_link_requests
        self.kaggle_calls = 0
        self._lock = threading.Lock()
        self._link: str | None = None
        self._rate_limited: KaggleRateLimited | None = None

        self.size = self._archive_size()
        self.members = self._load_index(index_path)

    def files(self, path_pattern: str) -> Callable[[str], bytes]:
        """A downloader for the scripts: product id -> bytes of path_pattern with {id} filled in."""
        return lambda product_id: self.fetch(path_pattern.format(id=product_id))

    def fetch(self, member_path: str) -> bytes:
        member = self.members.get(member_path)
        if member is None:
            raise KeyError(f"{member_path} is not in the dataset zip")
        start = member.header_offset
        guess = LOCAL_HEADER_SIZE + len(member_path.encode("utf-8")) + EXTRA_GUESS + member.compress_size
        chunk = self.read_range(start, min(self.size, start + guess))
        if chunk[:4] != LOCAL_HEADER_SIGNATURE:
            raise zipfile.BadZipFile(f"{member_path}: no local file header at offset {start}")
        name_length, extra_length = struct.unpack_from("<HH", chunk, 26)
        data_start = LOCAL_HEADER_SIZE + name_length + extra_length
        data = chunk[data_start : data_start + member.compress_size]
        if len(data) < member.compress_size:
            data += self.read_range(start + data_start + len(data), start + data_start + member.compress_size)

        content = inflate(data, member.compress_type, member_path)
        if zlib.crc32(content) != member.crc:
            raise zipfile.BadZipFile(f"{member_path}: checksum mismatch")
        return content

    def read_range(self, start: int, end: int) -> bytes:
        """Bytes start..end-1 of the zip, asking Kaggle for a fresh link once if Cloud Storage refuses this one."""
        link = self._current_link()
        for refreshed in (False, True):
            with self.client.stream("GET", link, headers={"Range": f"bytes={start}-{end - 1}"}) as response:
                if response.status_code in (400, 403) and not refreshed:
                    print(f"  Cloud Storage refused the link (HTTP {response.status_code}); asking for a fresh one")
                    link = self._current_link(stale=link)
                    continue
                if response.status_code == 200:
                    raise range_ignored(link)
                response.raise_for_status()
                if response.status_code != 206:
                    raise RangeNotSupported(f"expected HTTP 206 for a range request, got {response.status_code}")
                data = response.read()
                break
        if len(data) != end - start:
            raise httpx.ReadError(f"asked for {end - start} bytes at {start}, got {len(data)}")
        return data

    # ---------- Link, size and index ----------

    def _current_link(self, stale: str | None = None) -> str:
        with self._lock:
            if self._rate_limited:
                raise self._rate_limited
            if self._link is None or self._link == stale:
                if self.kaggle_calls >= self.max_link_requests:
                    raise KaggleError(f"asked Kaggle for a link {self.kaggle_calls} times this run; stopping")
                self.kaggle_calls += 1
                try:
                    self._link = download_link(self.client, self.handle, self.auth, self.kaggle_calls)
                except KaggleRateLimited as error:
                    self._rate_limited = error
                    raise
            return self._link

    def _archive_size(self) -> int:
        link = self._current_link()
        with self.client.stream("GET", link, headers={"Range": "bytes=0-0"}) as response:
            if response.status_code in (400, 403):
                response.read()
                raise KaggleError(
                    f"Cloud Storage refused a fresh link (HTTP {response.status_code}): {response.text[:300]}"
                )
            if response.status_code == 200:
                raise range_ignored(link)
            response.raise_for_status()
            content_range = response.headers.get("content-range", "")
            response.read()
        total = content_range.rpartition("/")[2]
        if response.status_code != 206 or not total.isdigit():
            raise RangeNotSupported(f"no zip size in the range reply (HTTP {response.status_code}, {content_range!r})")
        return int(total)

    def _load_index(self, index_path: Path) -> dict[str, Member]:
        if index_path.is_file():
            cached = json.loads(index_path.read_text(encoding="utf-8"))
            if cached.get("dataset") == str(self.handle) and cached.get("size") == self.size:
                return {name: Member(*values) for name, values in cached["members"].items()}
            print(f"  {index_path} is for a different zip; reading the file list again")

        print(f"Reading the file list of {self.handle} ({self.size / 1e9:.1f} GB zip)")
        with zipfile.ZipFile(RangeFile(self.read_range, self.size, self.read_ahead)) as archive:
            members = {
                info.filename: Member(
                    info.header_offset, info.compress_size, info.file_size, info.compress_type, info.CRC
                )
                for info in archive.infolist()
                if not info.is_dir()
            }
        index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = index_path.with_name(index_path.name + ".tmp")
        temporary.write_text(
            json.dumps({"dataset": str(self.handle), "size": self.size, "members": members}), encoding="utf-8"
        )
        temporary.replace(index_path)
        print(f"  {len(members):,} files; index saved to {index_path}")
        return members


def range_ignored(link: str) -> RangeNotSupported:
    return RangeNotSupported(
        f"{urlsplit(link).hostname} ignored the Range header (HTTP 200 instead of 206), "
        "so single files can't be read out of the zip"
    )


def inflate(data: bytes, compress_type: int, name: str) -> bytes:
    if compress_type == zipfile.ZIP_STORED:
        return data
    if compress_type == zipfile.ZIP_DEFLATED:
        inflater = zlib.decompressobj(-zlib.MAX_WBITS)
        return inflater.decompress(data) + inflater.flush()
    raise zipfile.BadZipFile(f"{name}: unsupported compression method {compress_type}")
