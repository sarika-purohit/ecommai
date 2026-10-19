"""Step 1.2: download the full-size photo of every chosen product and save it in three sizes.

Reads the ids in data/selected.csv, downloads each photo from the full Kaggle dataset, makes
original/, gemini/ and thumb/ JPEG versions, uploads them to Cloud Storage and appends the links
to data/photos.jsonl. Re-running skips products that are already in data/photos.jsonl.

    python -m data_prep.get_photos                      # upload to the GCS_BUCKET bucket
    python -m data_prep.get_photos --local-only out/    # write the files to a folder instead

Photos are read straight out of the dataset's zip with range requests (see data_prep.kaggle_zip), which takes a
single Kaggle API call per run. --source kagglehub downloads them one Kaggle API call per photo instead.
"""

import argparse
import csv
import io
import json
import os
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv
from google.cloud import storage
from PIL import Image, ImageOps

from agent.models import Images
from data_prep.kaggle_zip import ZIP_INDEX, KaggleRateLimited, KaggleZip

# Pinned to a version: without one, kagglehub asks Kaggle for the latest version on every file.
DATASET = "paramaggarwal/fashion-product-images-dataset/versions/1"
IMAGE_PATH = "fashion-dataset/images/{id}.jpg"
KAGGLE_CACHE = Path("downloads/kagglehub")
SOURCES = ("zip", "kagglehub")

ORIGINAL_QUALITY = 95
# name: (long side in pixels, JPEG quality)
RESIZED: dict[str, tuple[int, int]] = {
    "gemini": (1000, 90),
    "thumb": (400, 85),
}
CACHE_CONTROL = "public, max-age=604800"
EXIF_ORIENTATION = 0x0112

Download = Callable[[str], bytes]


class Store(Protocol):
    def save(self, path: str, data: bytes) -> str:
        """Saves one JPEG and returns its link."""
        ...


# ---------- Making the three versions ----------


def make_versions(data: bytes) -> tuple[dict[str, bytes], tuple[int, int]]:
    """Returns the original, gemini and thumb JPEGs, plus the original's width and height."""
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        keep_as_is = is_plain_jpeg(image)
        rgb = to_rgb(image)
    versions = {"original": data if keep_as_is else to_jpeg(rgb, ORIGINAL_QUALITY)}
    for name, (long_side, quality) in RESIZED.items():
        versions[name] = to_jpeg(shrink(rgb, long_side), quality)
    return versions, rgb.size


def is_plain_jpeg(image: Image.Image) -> bool:
    return image.format == "JPEG" and image.mode == "RGB" and image.getexif().get(EXIF_ORIENTATION, 1) == 1


def to_rgb(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        # Product photos sit on white, so transparent areas become white rather than black.
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def shrink(image: Image.Image, long_side: int) -> Image.Image:
    width, height = image.size
    if max(width, height) <= long_side:
        return image
    if width >= height:
        size = (long_side, max(1, round(height * long_side / width)))
    else:
        size = (max(1, round(width * long_side / height)), long_side)
    return image.resize(size, Image.Resampling.LANCZOS)


def to_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


# ---------- Where photos come from and where they go ----------


class KaggleFiles:
    """Downloads single files from the Kaggle dataset, e.g. one photo or one styles JSON."""

    def __init__(self, dataset: str, path_pattern: str, cache_dir: Path = KAGGLE_CACHE):
        # kagglehub reads its cache folder, and tqdm its on/off switch, only from the environment,
        # and tqdm only at import time, so these must be set before kagglehub is imported.
        os.environ["KAGGLEHUB_CACHE"] = str(cache_dir)
        os.environ.setdefault("TQDM_DISABLE", "1")
        os.environ.setdefault("KAGGLEHUB_VERBOSITY", "error")
        import kagglehub

        self._download = kagglehub.dataset_download
        self.dataset = dataset
        self.path_pattern = path_pattern

    def __call__(self, product_id: str) -> bytes:
        return Path(self._download(self.dataset, path=self.path_pattern.format(id=product_id))).read_bytes()


def open_source(source: str, dataset: str, path_pattern: str, kaggle_cache: Path, zip_index: Path) -> Download:
    if source == "kagglehub":
        return KaggleFiles(dataset, path_pattern, kaggle_cache)
    return KaggleZip(dataset, zip_index).files(path_pattern)


class GcsStore:
    def __init__(self, bucket: str, project: str | None = None):
        self.bucket_name = bucket
        self.project = project
        self._local = threading.local()

    def save(self, path: str, data: bytes) -> str:
        blob = self._bucket().blob(path)
        blob.cache_control = CACHE_CONTROL
        blob.upload_from_string(data, content_type="image/jpeg")
        return f"https://storage.googleapis.com/{self.bucket_name}/{path}"

    def _bucket(self) -> storage.Bucket:
        # storage.Client isn't documented as thread-safe, so each worker thread gets its own.
        if not hasattr(self._local, "bucket"):
            self._local.bucket = storage.Client(project=self.project).bucket(self.bucket_name)
        return self._local.bucket


class LocalStore:
    def __init__(self, root: Path):
        self.root = root

    def save(self, path: str, data: bytes) -> str:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target)


# ---------- Running over all products ----------


@dataclass
class Summary:
    done: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    rate_limited: str = ""


def read_ids(selected: Path) -> list[str]:
    with selected.open(newline="", encoding="utf-8") as f:
        ids = [row["id"].strip() for row in csv.DictReader(f)]
    return list(dict.fromkeys(i for i in ids if i))


def finished_ids(out: Path) -> set[str]:
    if not out.exists():
        return set()
    done = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        try:
            done.add(str(json.loads(line)["id"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return done


def with_retries[T](job: Callable[[], T], attempts: int, delay: float) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return job()
        except KaggleRateLimited:
            raise
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(delay * 2 ** (attempt - 1))
    raise ValueError("attempts must be at least 1")


def process(product_id: str, download: Download, store: Store) -> dict:
    versions, (width, height) = make_versions(download(product_id))
    links = {name: store.save(f"{name}/{product_id}.jpg", data) for name, data in versions.items()}
    images = Images(**links)
    return {"id": product_id, **images.model_dump(), "width": width, "height": height}


def run(
    ids: Iterable[str],
    download: Download,
    store: Store,
    out: Path,
    failed_out: Path,
    workers: int = 8,
    attempts: int = 4,
    delay: float = 2.0,
) -> Summary:
    ids = list(ids)
    already = finished_ids(out)
    todo = [i for i in ids if i not in already]
    summary = Summary(skipped=len(ids) - len(todo))
    out.parent.mkdir(parents=True, exist_ok=True)
    start_on_new_line(out)

    with out.open("a", encoding="utf-8") as lines, ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(with_retries, partial(process, i, download, store), attempts, delay): i for i in todo}
        for job in as_completed(jobs):
            product_id = jobs[job]
            try:
                row = job.result()
            except KaggleRateLimited as error:
                if not summary.rate_limited:
                    print(f"  stopping: {error}")
                summary.failed.append(product_id)
                summary.rate_limited = str(error)
                continue
            except Exception as error:
                summary.failed.append(product_id)
                print(f"  failed {product_id}: {type(error).__name__}: {error}")
                continue
            lines.write(json.dumps(row) + "\n")
            lines.flush()
            summary.done += 1
            if summary.done % 100 == 0:
                print(f"  {summary.done:,} of {len(todo):,} done")

    failed_out.parent.mkdir(parents=True, exist_ok=True)
    failed_out.write_text("".join(f"{i}\n" for i in sorted(summary.failed)), encoding="utf-8")
    return summary


def start_on_new_line(out: Path) -> None:
    """A run that was killed mid-write can leave a partial last line; keep the next row off it."""
    if out.exists() and out.stat().st_size:
        with out.open("rb+") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")


# ---------- Command line ----------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selected", type=Path, default=Path("data/selected.csv"))
    parser.add_argument(
        "--out", type=Path, help="progress file (default: data/photos.jsonl, or DIR/photos.jsonl with --local-only)"
    )
    parser.add_argument("--failed", type=Path, help="failed ids (default: next to --out, photos_failed.txt)")
    parser.add_argument("--bucket", default=os.environ.get("GCS_BUCKET"), help="default: GCS_BUCKET")
    parser.add_argument("--local-only", type=Path, metavar="DIR", help="write the photos here instead of uploading")
    parser.add_argument("--dataset", default=DATASET, help="Kaggle dataset handle")
    parser.add_argument("--image-path", default=IMAGE_PATH, help="photo path inside the dataset; {id} is replaced")
    add_source_args(parser)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--attempts", type=int, default=4, help="tries per product before giving up")
    parser.add_argument("--limit", type=int, help="only the first N products, for a trial run")
    args = parser.parse_args(argv)
    if args.out is None:
        args.out = args.local_only / "photos.jsonl" if args.local_only else Path("data/photos.jsonl")
    if args.failed is None:
        args.failed = args.out.with_name("photos_failed.txt")
    if not args.local_only and not args.bucket:
        parser.error("set GCS_BUCKET in .env, pass --bucket, or use --local-only DIR")
    return args


def add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        choices=SOURCES,
        default="zip",
        help="zip: range requests into the dataset zip, one Kaggle API call per run (default); "
        "kagglehub: one Kaggle API call per file",
    )
    parser.add_argument("--zip-index", type=Path, default=ZIP_INDEX, help="cached file list of the dataset zip")
    parser.add_argument("--kaggle-cache", type=Path, default=KAGGLE_CACHE, help="where kagglehub keeps downloads")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parse_args(argv)
    ids = read_ids(args.selected)[: args.limit]
    try:
        download = open_source(args.source, args.dataset, args.image_path, args.kaggle_cache, args.zip_index)
    except KaggleRateLimited as error:
        raise SystemExit(str(error))
    if args.local_only:
        store: Store = LocalStore(args.local_only)
    else:
        store = GcsStore(args.bucket, os.environ.get("GOOGLE_CLOUD_PROJECT") or None)

    summary = run(ids, download, store, args.out, args.failed, workers=args.workers, attempts=args.attempts)
    print(f"done {summary.done:,}, skipped {summary.skipped:,} (already finished), failed {len(summary.failed):,}")
    print(f"links: {args.out}")
    if summary.failed:
        print(f"failed ids: {args.failed} (re-run to retry them)")
    if summary.rate_limited:
        print(summary.rate_limited)


if __name__ == "__main__":
    main()
