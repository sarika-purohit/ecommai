"""Build data_prep/pincodes.csv: pincode -> city, district, state and delivery zone.

Source: India Post's All India Pincode Directory, published by the Department of Posts on the
Open Government Data Platform India (data.gov.in) under the Government Open Data License - India
(https://www.data.gov.in/Godl). The default download is the edition data.gov.in serves without an
API key:
  https://www.data.gov.in/sites/default/files/all_india_PO_list_without_APS_offices_ver2_lat_long.csv
The monthly edition (data.gov.in resource 5c2f62fe-5afa-4119-a499-fec9d604d5bd) needs an API key.
Its CSV export names the district column 'district' instead of 'Districtname'; this script reads both.

Run:
  python -m data_prep.build_pincodes                     # download the default source
  python -m data_prep.build_pincodes --source URL        # download another copy of the directory
  python -m data_prep.build_pincodes --input FILE.csv    # use a file you already have
"""

import argparse
import csv
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal, NamedTuple
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
DOWNLOADS = REPO_ROOT / "downloads"
DEFAULT_OUT = REPO_ROOT / "data_prep" / "pincodes.csv"
DEFAULT_SOURCE = (
    "https://www.data.gov.in/sites/default/files/all_india_PO_list_without_APS_offices_ver2_lat_long.csv"
)

Zone = Literal["big_city", "remote", "other"]
COLUMNS = ("pincode", "city", "district", "state", "zone")
PINCODE = re.compile(r"[1-9][0-9]{5}")
MISSING_VALUES = {"", "null", "na", "n a", "none"}


class CityDistricts(NamedTuple):
    states: frozenset[str]
    districts: frozenset[str]


# Only each city's own district(s). Neighbouring districts that mix towns and villages
# (Thane, Bangalore Rural, Howrah, Rangareddy, Kanchipuram, ...) stay "other", so delivery
# promises err late rather than early. All names are in normalise() form.
BIG_CITY_DISTRICTS: dict[str, CityDistricts] = {
    "Mumbai": CityDistricts(
        frozenset({"maharashtra"}), frozenset({"mumbai", "mumbai city", "mumbai suburban", "bombay"})
    ),
    "Bengaluru": CityDistricts(
        frozenset({"karnataka"}), frozenset({"bangalore", "bengaluru", "bangalore urban", "bengaluru urban"})
    ),
    "Kolkata": CityDistricts(frozenset({"west bengal"}), frozenset({"kolkata", "calcutta"})),
    "Chennai": CityDistricts(frozenset({"tamil nadu"}), frozenset({"chennai", "madras"})),
    "Hyderabad": CityDistricts(frozenset({"telangana", "andhra pradesh"}), frozenset({"hyderabad"})),
    "Pune": CityDistricts(frozenset({"maharashtra"}), frozenset({"pune", "poona"})),
    "Ahmedabad": CityDistricts(frozenset({"gujarat"}), frozenset({"ahmedabad", "ahmadabad"})),
}
DELHI_STATES = frozenset({"delhi", "nct of delhi", "national capital territory of delhi"})
BIG_CITIES = ("Mumbai", "Delhi", "Bengaluru", "Kolkata", "Chennai", "Hyderabad", "Pune", "Ahmedabad")

REMOTE_STATES = frozenset(
    {
        "arunachal pradesh",
        "assam",
        "manipur",
        "meghalaya",
        "mizoram",
        "nagaland",
        "tripura",
        "sikkim",
        "jammu and kashmir",
        "ladakh",
        "andaman and nicobar islands",
        "andaman and nicobar",
        "lakshadweep",
    }
)

# Leh and Kargil became the Union Territory of Ladakh in 2019; older editions list them under J&K.
LADAKH_DISTRICTS = frozenset({"leh", "leh ladakh", "kargil"})
STATE_SPELLINGS = {"chattisgarh": "Chhattisgarh"}
LOWERCASE_WORDS = {"and", "of", "the"}


class Office(NamedTuple):
    pincode: str
    district: str
    state: str


class PincodeRow(BaseModel):
    pincode: str
    city: str
    district: str
    state: str
    zone: Zone


def normalise(name: str) -> str:
    """Lower case, '&' as 'and', punctuation as spaces: 'JAMMU & KASHMIR' -> 'jammu and kashmir'."""
    name = name.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name).split())


def display_name(name: str) -> str:
    words = name.replace("&", " and ").split()
    return " ".join(
        word.lower() if i > 0 and word.lower() in LOWERCASE_WORDS else word.title() for i, word in enumerate(words)
    )


def display_state(state: str, district: str) -> str:
    key = normalise(state)
    if key == "jammu and kashmir" and normalise(district) in LADAKH_DISTRICTS:
        return "Ladakh"
    return STATE_SPELLINGS.get(key, display_name(state))


def big_city_for(district: str, state: str) -> str | None:
    district_key, state_key = normalise(district), normalise(state)
    if state_key in DELHI_STATES:
        return "Delhi"
    for city, match in BIG_CITY_DISTRICTS.items():
        if state_key in match.states and district_key in match.districts:
            return city
    return None


def zone_for(district: str, state: str) -> Zone:
    if big_city_for(district, state):
        return "big_city"
    if normalise(state) in REMOTE_STATES:
        return "remote"
    return "other"


def is_missing(value: str) -> bool:
    return normalise(value) in MISSING_VALUES


def read_offices(path: Path) -> Iterator[Office]:
    """One Office per post office row. Reads both the file and the API layouts of the directory."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = {name.strip().lower(): name for name in reader.fieldnames or []}
        district_column = columns.get("districtname") or columns.get("district")
        if not district_column or "pincode" not in columns or "statename" not in columns:
            raise ValueError(f"{path} doesn't look like the India Post pincode directory: {reader.fieldnames}")
        for row in reader:
            yield Office(
                pincode=row[columns["pincode"]].strip(),
                district=row[district_column].strip(),
                state=row[columns["statename"]].strip(),
            )


def most_common(counts: Counter[str]) -> str:
    """Highest count wins; ties go to the alphabetically first name, so every run gives the same file."""
    return min(counts, key=lambda name: (-counts[name], name))


def aggregate(offices: Iterable[Office]) -> list[PincodeRow]:
    """One row per pincode: its most common state, then the most common district within that state."""
    states: dict[str, Counter[str]] = defaultdict(Counter)
    districts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for office in offices:
        if not PINCODE.fullmatch(office.pincode) or is_missing(office.state) or is_missing(office.district):
            continue
        states[office.pincode][office.state] += 1
        districts[office.pincode, office.state][office.district] += 1

    rows = []
    for pincode in sorted(states):
        state = most_common(states[pincode])
        district = most_common(districts[pincode, state])
        big_city = big_city_for(district, state)
        rows.append(
            PincodeRow(
                pincode=pincode,
                city=big_city or display_name(district),
                district=display_name(district),
                state=display_state(state, district),
                zone=zone_for(district, state),
            )
        )
    return rows


def download(url: str, folder: Path = DOWNLOADS) -> Path:
    parsed = urlparse(url)
    name = Path(parsed.path).name or "pincode_directory"
    dest = folder / (name if name.endswith(".csv") else f"{name}.csv")
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {parsed.scheme}://{parsed.netloc}{parsed.path} -> {dest}")
    headers = {"User-Agent": "Mozilla/5.0 (EcommAI data prep)"}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        with dest.open("wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)
    return dest


def write_csv(rows: list[PincodeRow], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(row.model_dump() for row in rows)


def summary(rows: list[PincodeRow], out: Path) -> str:
    zones = Counter(row.zone for row in rows)
    cities = Counter(row.city for row in rows if row.zone == "big_city")
    lines = [
        f"Wrote {len(rows):,} pincodes to {out} ({out.stat().st_size / 1024:,.0f} KB)",
        "Zones: " + ", ".join(f"{zone} {zones[zone]:,}" for zone in ("big_city", "other", "remote")),
        "Big cities: " + ", ".join(f"{city} {cities[city]}" for city in BIG_CITIES),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source", help=f"URL of the directory CSV (default: {DEFAULT_SOURCE})")
    source.add_argument("--input", type=Path, help="a directory CSV already on disk")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write pincodes.csv")
    args = parser.parse_args(argv)

    path = args.input or download(args.source or DEFAULT_SOURCE)
    rows = aggregate(read_offices(path))
    write_csv(rows, args.out)
    print(summary(rows, args.out))


if __name__ == "__main__":
    main()
