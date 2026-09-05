from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from cashflow_audit.errors import AuditError
from cashflow_audit.parse import parse_workbook
from tests.helpers.xlsx import write_zip


def test_empty_file_rejected(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "empty.xlsx"
    source.write_bytes(b"")
    with pytest.raises(AuditError) as ei:
        parse_workbook(source, dest)
    assert ei.value.code == "empty_file"


def test_encrypted_workbook_rejected(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "enc.xlsx"
    write_zip(
        source,
        {
            "EncryptionInfo": b"ole-header",
            "EncryptedPackage": b"ciphertext",
        },
    )
    with pytest.raises(AuditError) as ei:
        parse_workbook(source, dest)
    assert ei.value.code == "encrypted_workbook"


def test_zip_slip_rejected(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "slip.xlsx"
    write_zip(
        source,
        {
            "[Content_Types].xml": "<Types/>",
            "../../tmp/evil.txt": "pwn",
        },
    )
    with pytest.raises(AuditError) as ei:
        parse_workbook(source, dest)
    assert ei.value.code == "zip_rejected"


def test_zip_bomb_ratio_rejected(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "bomb.xlsx"
    zeros = b"\x00" * (100 * 1024)
    write_zip(
        source,
        {
            "[Content_Types].xml": "<Types/>",
            "xl/workbook.xml": zeros.decode("latin1"),
        },
    )
    with pytest.raises(AuditError) as ei:
        parse_workbook(source, dest)
    assert ei.value.code == "zip_rejected"


def test_member_size_cap() -> None:
    from cashflow_audit.parse.zip_guard import check_zip_info

    info = zipfile.ZipInfo("xl/workbook.xml")
    info.file_size = 512 * 1024 * 1024 + 1
    info.compress_size = 512 * 1024 * 1024 + 1
    with pytest.raises(AuditError) as ei:
        check_zip_info(info, running_total=0)
    assert ei.value.code == "zip_rejected"
