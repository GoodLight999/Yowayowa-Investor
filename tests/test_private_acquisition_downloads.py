import hashlib

import pytest

from yowayowa.acquisition.downloads import capture_download, sniff_format
from yowayowa.acquisition.transport import PrivateAcquisitionError

_CSV_BYTES = b"symbol,quantity\n7203,100\n6758,200\n"


def test_sniff_csv_via_content_type() -> None:
    assert sniff_format(_CSV_BYTES, "text/csv; charset=utf-8", None) == "csv"


def test_sniff_csv_via_consistent_commas() -> None:
    assert sniff_format(_CSV_BYTES, None, None) == "csv"


def test_sniff_json() -> None:
    assert sniff_format(b'[{"a": 1}]', None, None) == "json"


def test_sniff_xlsx_magic_with_content_types() -> None:
    blob = b"PK\x03\x04" + b"[Content_Types].xml" + b"\x00" * 64
    assert sniff_format(blob, None, None) == "xlsx"


def test_sniff_xlsx_via_filename() -> None:
    blob = b"PK\x03\x04" + b"\x00" * 64
    assert sniff_format(blob, None, "positions.xlsx") == "xlsx"


def test_sniff_other_for_binary_noise() -> None:
    assert sniff_format(b"\x00\x01\x02", "application/octet-stream", None) == "other"


def test_capture_csv_parses_rows_with_bom() -> None:
    bom_csv = "\ufeffsymbol,quantity\n7203,100\n".encode()
    capture = capture_download(content=bom_csv, content_type="text/csv", filename="positions.csv")
    assert capture.format == "csv"
    assert capture.parsed is True
    assert capture.rows == [{"symbol": "7203", "quantity": "100"}]
    assert capture.sha256 == hashlib.sha256(bom_csv).hexdigest()
    assert capture.size_bytes == len(bom_csv)


def test_capture_csv_row_cap() -> None:
    big_csv = "a,b\n" + "1,2\n" * 10_500
    capture = capture_download(
        content=big_csv.encode(), content_type="text/csv", filename="big.csv"
    )
    assert capture.rows is not None
    assert len(capture.rows) == 10_000
    assert capture.parse_note is not None and "capped" in capture.parse_note


def test_capture_json_list_documents() -> None:
    payload = b'[{"symbol": "7203"}, {"symbol": "6758"}]'
    capture = capture_download(content=payload, content_type=None, filename=None)
    assert capture.format == "json"
    assert capture.parsed is True
    assert capture.documents == [{"symbol": "7203"}, {"symbol": "6758"}]


def test_capture_json_non_list_keeps_note() -> None:
    capture = capture_download(content=b'{"object": true}', content_type=None, filename=None)
    assert capture.format == "json"
    assert capture.parsed is False
    assert capture.parse_note is not None


def test_capture_xlsx_raw_with_note() -> None:
    blob = b"PK\x03\x04" + b"[Content_Types].xml" + b"\x00" * 32
    capture = capture_download(content=blob, content_type=None, filename="report.xlsx")
    assert capture.format == "xlsx"
    assert capture.parsed is False
    assert capture.parse_note == "xlsx captured raw; parsing not available"


def test_capture_oversize_raises_failed() -> None:
    with pytest.raises(PrivateAcquisitionError) as excinfo:
        capture_download(content=b"x" * 11, content_type=None, filename=None, max_bytes=10)
    assert excinfo.value.state.value == "failed"
    assert "too large" in excinfo.value.reason


def test_capture_other_format_note() -> None:
    capture = capture_download(content=b"\x00\x01", content_type=None, filename=None)
    assert capture.format == "other"
    assert capture.parsed is False
    assert capture.parse_note == "unrecognized format"


def test_sha256_matches_hashlib() -> None:
    content = b"deterministic"
    capture = capture_download(content=content, content_type=None, filename=None)
    assert capture.sha256 == hashlib.sha256(content).hexdigest()
