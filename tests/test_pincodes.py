import csv
import re
from collections import Counter
from pathlib import Path

import pytest

from agent.delivery import PINCODES_CSV, PincodeInfo, lookup
from data_prep.build_pincodes import BIG_CITIES, COLUMNS, Office, aggregate, main, read_offices

REMOTE_STATES = [
    "Arunachal Pradesh",
    "Assam",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Tripura",
    "Sikkim",
    "Jammu and Kashmir",
    "Ladakh",
    "Andaman and Nicobar Islands",
    "Lakshadweep",
]


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    with PINCODES_CSV.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------- The committed pincodes.csv ----------


def test_file_has_exactly_the_expected_columns():
    with PINCODES_CSV.open(newline="", encoding="utf-8") as f:
        assert next(csv.reader(f)) == list(COLUMNS)


def test_at_least_18000_unique_pincodes(rows):
    pincodes = [row["pincode"] for row in rows]
    assert len(pincodes) >= 18_000
    assert len(set(pincodes)) == len(pincodes)


def test_every_row_has_a_valid_pincode_zone_and_names(rows):
    for row in rows:
        assert re.fullmatch(r"[1-9][0-9]{5}", row["pincode"]), row
        assert row["zone"] in {"big_city", "remote", "other"}, row
        assert row["city"] and row["district"] and row["state"], row


def test_city_is_the_big_city_name_or_else_the_district(rows):
    for row in rows:
        if row["zone"] == "big_city":
            assert row["city"] in BIG_CITIES, row
        else:
            assert row["city"] == row["district"], row


def test_each_big_city_has_many_pincodes(rows):
    counts = Counter(row["city"] for row in rows if row["zone"] == "big_city")
    for city in BIG_CITIES:
        assert counts[city] >= 50, (city, counts[city])


def test_all_of_delhi_is_one_big_city(rows):
    delhi = [row for row in rows if row["state"] == "Delhi"]
    assert delhi
    assert all(row["city"] == "Delhi" and row["zone"] == "big_city" for row in delhi)


@pytest.mark.parametrize("state", REMOTE_STATES)
def test_every_remote_state_is_flagged_remote(rows, state):
    in_state = [row for row in rows if row["state"] == state]
    assert in_state, f"no pincodes for {state}"
    assert all(row["zone"] == "remote" for row in in_state)


def test_only_remote_states_are_remote(rows):
    assert {row["state"] for row in rows if row["zone"] == "remote"} == set(REMOTE_STATES)


@pytest.mark.parametrize(
    "pincode, city, state, zone",
    [
        ("400001", "Mumbai", "Maharashtra", "big_city"),
        ("110001", "Delhi", "Delhi", "big_city"),
        ("560001", "Bengaluru", "Karnataka", "big_city"),
        ("700001", "Kolkata", "West Bengal", "big_city"),
        ("600001", "Chennai", "Tamil Nadu", "big_city"),
        ("500001", "Hyderabad", "Telangana", "big_city"),
        ("411001", "Pune", "Maharashtra", "big_city"),
        ("380001", "Ahmedabad", "Gujarat", "big_city"),
        pytest.param("781001", "Kamrup", "Assam", "remote", id="Guwahati"),
        pytest.param("194101", "Leh", "Ladakh", "remote", id="Leh"),
        pytest.param("744101", "South Andaman", "Andaman and Nicobar Islands", "remote", id="Port Blair"),
        pytest.param("682555", "Lakshadweep", "Lakshadweep", "remote", id="Lakshadweep"),
        pytest.param("302001", "Jaipur", "Rajasthan", "other", id="Jaipur"),
        pytest.param("421301", "Thane", "Maharashtra", "other", id="Kalyan, next to Mumbai"),
        pytest.param("562110", "Bangalore Rural", "Karnataka", "other", id="Bangalore Rural"),
    ],
)
def test_known_pincodes(pincode, city, state, zone):
    assert lookup(pincode) == PincodeInfo(pincode=pincode, city=city, state=state, zone=zone)


# ---------- How build_pincodes turns the directory into rows ----------


def build_one(district: str, state: str) -> dict[str, str]:
    [row] = aggregate([Office("400050", district, state)])
    return row.model_dump()


@pytest.mark.parametrize(
    "district, state, city",
    [
        ("Mumbai", "MAHARASHTRA", "Mumbai"),
        ("MUMBAI SUBURBAN", "MAHARASHTRA", "Mumbai"),
        ("Bangalore", "KARNATAKA", "Bengaluru"),
        ("BENGALURU URBAN", "KARNATAKA", "Bengaluru"),
        ("Kolkata", "WEST BENGAL", "Kolkata"),
        ("CHENNAI", "TAMIL NADU", "Chennai"),
        ("Hyderabad", "TELANGANA", "Hyderabad"),
        ("Hyderabad", "ANDHRA PRADESH", "Hyderabad"),
        ("PUNE", "MAHARASHTRA", "Pune"),
        ("AHMADABAD", "GUJARAT", "Ahmedabad"),
        ("South West Delhi", "DELHI", "Delhi"),
        ("CENTRAL", "DELHI", "Delhi"),
        ("New Delhi", "NCT of Delhi", "Delhi"),
    ],
)
def test_big_city_district_variants(district, state, city):
    row = build_one(district, state)
    assert (row["city"], row["zone"]) == (city, "big_city")


@pytest.mark.parametrize(
    "district, state",
    [
        ("Thane", "MAHARASHTRA"),
        ("Bangalore Rural", "KARNATAKA"),
        ("BENGALURU RURAL", "KARNATAKA"),
        ("K.V.Rangareddy", "TELANGANA"),
        ("Howrah", "WEST BENGAL"),
        ("Kanchipuram", "TAMIL NADU"),
        ("Hyderabad", "SINDH"),
    ],
)
def test_neighbouring_districts_are_not_big_cities(district, state):
    row = build_one(district, state)
    assert row["zone"] == "other"
    assert row["city"] == row["district"]


@pytest.mark.parametrize(
    "district, state, shown_state",
    [
        ("Kamrup", "ASSAM", "Assam"),
        ("South Andaman", "ANDAMAN & NICOBAR ISLANDS", "Andaman and Nicobar Islands"),
        ("Srinagar", "JAMMU & KASHMIR", "Jammu and Kashmir"),
        ("Leh", "JAMMU & KASHMIR", "Ladakh"),
        ("Kargil", "Jammu and Kashmir", "Ladakh"),
        ("LEH LADAKH", "LADAKH", "Ladakh"),
        ("Lakshadweep", "LAKSHADWEEP", "Lakshadweep"),
        ("East Sikkim", "Sikkim", "Sikkim"),
        ("Tawang", "arunachal pradesh", "Arunachal Pradesh"),
    ],
)
def test_remote_state_variants(district, state, shown_state):
    row = build_one(district, state)
    assert (row["state"], row["zone"]) == (shown_state, "remote")


def test_names_are_title_cased_and_known_misspellings_fixed():
    row = build_one("BILASPUR", "CHATTISGARH")
    assert (row["city"], row["district"], row["state"]) == ("Bilaspur", "Bilaspur", "Chhattisgarh")


def test_one_row_per_pincode_from_its_most_common_state_then_district():
    rows = aggregate(
        [
            Office("421301", "Thane", "MAHARASHTRA"),
            Office("421301", "Mumbai", "MAHARASHTRA"),
            Office("421301", "Thane", "MAHARASHTRA"),
            Office("421301", "Valsad", "GUJARAT"),
            Office("421301", "NULL", "NULL"),
        ]
    )
    assert [(r.pincode, r.district, r.state) for r in rows] == [("421301", "Thane", "Maharashtra")]


def test_ties_are_broken_the_same_way_every_time():
    offices = [Office("607403", "Pondicherry", "PONDICHERRY"), Office("607403", "Cuddalore", "TAMIL NADU")]
    assert aggregate(offices) == aggregate(list(reversed(offices)))
    assert aggregate(offices)[0].district == "Pondicherry"


def test_pincodes_without_a_known_place_or_with_a_bad_code_are_dropped():
    offices = [
        Office("509133", "NULL", "NULL"),
        Office("509134", "", "TELANGANA"),
        Office("509135", "Hyderabad", "NA"),
        Office("012345", "Mumbai", "MAHARASHTRA"),
        Office("40005", "Mumbai", "MAHARASHTRA"),
        Office("400005", "Mumbai", "MAHARASHTRA"),
    ]
    assert [row.pincode for row in aggregate(offices)] == ["400005"]


FILE_LAYOUT = """officename,pincode,officeType,Deliverystatus,divisionname,regionname,circlename,Taluk,Districtname,statename
Colaba S.O,400005,S.O,Delivery,Mumbai South,Mumbai,Maharashtra,Mumbai,Mumbai,MAHARASHTRA
Kavaratti S.O,682555,S.O,Delivery,Calicut,Calicut,Kerala,Kavaratti,Lakshadweep,LAKSHADWEEP
"""

API_LAYOUT = """circlename,regionname,divisionname,officename,pincode,officetype,delivery,district,statename,latitude,longitude
"Maharashtra Circle","Mumbai Region","Mumbai South","Colaba S.O",400005,PO,Delivery,"MUMBAI",MAHARASHTRA,NA,NA
"Kerala Circle","Calicut Region","Calicut","Kavaratti S.O",682555,PO,Delivery,"LAKSHADWEEP",LAKSHADWEEP,NA,NA
"""


@pytest.mark.parametrize("layout", [FILE_LAYOUT, API_LAYOUT], ids=["file", "api"])
def test_reads_both_layouts_of_the_directory(tmp_path: Path, layout: str):
    source = tmp_path / "directory.csv"
    source.write_text(layout, encoding="utf-8")
    offices = list(read_offices(source))
    assert [(o.pincode, o.district.title(), o.state) for o in offices] == [
        ("400005", "Mumbai", "MAHARASHTRA"),
        ("682555", "Lakshadweep", "LAKSHADWEEP"),
    ]


def test_rejects_a_file_that_is_not_the_directory(tmp_path: Path):
    source = tmp_path / "other.csv"
    source.write_text("name,value\na,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="pincode directory"):
        list(read_offices(source))


def test_main_builds_the_csv_from_a_local_file(tmp_path: Path, capsys):
    source = tmp_path / "directory.csv"
    source.write_text(FILE_LAYOUT, encoding="utf-8")
    out = tmp_path / "pincodes.csv"
    main(["--input", str(source), "--out", str(out)])
    assert out.read_text(encoding="utf-8") == (
        "pincode,city,district,state,zone\n"
        "400005,Mumbai,Mumbai,Maharashtra,big_city\n"
        "682555,Lakshadweep,Lakshadweep,Lakshadweep,remote\n"
    )
    assert "Wrote 2 pincodes" in capsys.readouterr().out
