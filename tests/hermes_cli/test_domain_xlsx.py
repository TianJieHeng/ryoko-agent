"""Bounded OOXML roundtrip and hostile real-byte fixtures, without external engines."""
import io
import zipfile
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from hermes_cli.domain_xlsx import (
    DOCREL, MAIN, REL, XlsxCell, XlsxError, XlsxSheet, XlsxWorkbook,
    read_xlsx, validate_xlsx, write_xlsx,
)


def workbook():
    return XlsxWorkbook((XlsxSheet("Values", (
        (XlsxCell("text"), XlsxCell("number"), XlsxCell("formula"), XlsxCell("day")),
        (XlsxCell("α<&"), XlsxCell(Decimal("12.50")), XlsxCell(Decimal(25), "$B$2*2"), XlsxCell(date(2026, 1, 2))),
        (XlsxCell("=inert"), XlsxCell(True), XlsxCell(None), XlsxCell()),
    )),))


def mutate(payload, name, update):
    with zipfile.ZipFile(io.BytesIO(payload)) as source:
        parts = {member: source.read(member) for member in source.namelist()}
    parts[name] = update(parts.get(name, b""))
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as target:
        for member, content in parts.items():
            target.writestr(member, content)
    return result.getvalue()


def test_typed_cells_formula_cache_and_complete_package_roundtrip_deterministically():
    source = workbook()
    payload = write_xlsx(source)
    assert payload == write_xlsx(source)
    assert read_xlsx(payload) == source
    receipt = validate_xlsx(payload)
    assert receipt["formula_count"] == 1
    assert receipt["formula_status"] == "preserved_not_recalculated"
    assert receipt["warnings"]


@pytest.mark.parametrize("name,data", [
    ("xl/vbaProject.bin", b"macro"), ("xl/externalLinks/externalLink1.xml", b"external"),
    ("../outside.xml", b"unsafe"), ("/absolute.xml", b"unsafe"),
    ("xl/worksheets/_rels/sheet1.xml.rels", b"hyperlink"),
])
def test_unsupported_package_parts_and_paths_are_rejected(name, data):
    with pytest.raises(XlsxError):
        read_xlsx(mutate(write_xlsx(workbook()), name, lambda _: data))


@pytest.mark.parametrize("transform", [
    lambda data: data + b"trailing",
    lambda data: b"preamble" + data,
    lambda data: data[:-4],
    lambda data: b"PK\x03\x04not a workbook",
])
def test_zip_complete_byte_boundaries_are_checked(transform):
    with pytest.raises(XlsxError):
        read_xlsx(transform(write_xlsx(workbook())))


@pytest.mark.parametrize("xml", [
    b'<!DOCTYPE worksheet [<!ENTITY x "expanded">]><worksheet xmlns="' + MAIN.encode() + b'"><sheetData/></worksheet>',
    ('<?xml version="1.0" encoding="UTF-16"?><worksheet xmlns="' + MAIN + '"><sheetData/></worksheet>').encode(),
    ('<worksheet xmlns="' + MAIN + '"><sheetData/><drawing/></worksheet>').encode(),
    ('<worksheet xmlns="' + MAIN + '"><sheetData><row r="10001"/></sheetData></worksheet>').encode(),
    ('<worksheet xmlns="' + MAIN + '"><sheetData><row r="1"><c r="XFD1"><v>1</v></c></row></sheetData></worksheet>').encode(),
])
def test_xml_bombs_external_features_and_sparse_bounds_fail_closed(xml):
    with pytest.raises(XlsxError):
        read_xlsx(mutate(write_xlsx(workbook()), "xl/worksheets/sheet1.xml", lambda _: xml))


def test_external_relationship_and_duplicate_zip_member_are_rejected():
    data = write_xlsx(workbook())
    rels = f'<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{DOCREL}/worksheet" Target="https://attacker.invalid/data" TargetMode="External"/></Relationships>'.encode()
    with pytest.raises(XlsxError, match="external"):
        read_xlsx(mutate(data, "xl/_rels/workbook.xml.rels", lambda _: rels))
    duplicate = io.BytesIO(data)
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(duplicate, "a") as archive:
            archive.writestr("xl/workbook.xml", b"duplicate")
    with pytest.raises(XlsxError, match="duplicate"):
        read_xlsx(duplicate.getvalue())


def test_compression_bomb_and_archive_comment_are_rejected():
    original = write_xlsx(workbook())
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        parts = {member: source.read(member) for member in source.namelist()}
    parts["xl/sharedStrings.xml"] = b" " * 500_000
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, content in parts.items():
            archive.writestr(member, content)
    with pytest.raises(XlsxError, match="expansion"):
        read_xlsx(result.getvalue())
    result = io.BytesIO(original)
    with zipfile.ZipFile(result, "a") as archive:
        archive.comment = b"hidden"
    with pytest.raises(XlsxError, match="comment"):
        read_xlsx(result.getvalue())


@pytest.mark.parametrize("formula", ['WEBSERVICE("https://example.invalid")', "[other.xlsx]Sheet1!A1", "SUM(A1:A2);cmd", "NOW()"])
def test_external_or_unsupported_formula_grammar_is_not_exported(formula):
    source = XlsxWorkbook((XlsxSheet("Data", ((XlsxCell(Decimal(1), formula),),)),))
    with pytest.raises(XlsxError, match="formula"):
        write_xlsx(source)


def test_reordered_duplicate_and_missing_cell_references_are_rejected():
    def update(data):
        root = ET.fromstring(data)
        rows = root.find("{" + MAIN + "}sheetData")
        row = rows[1]
        row[1].set("r", "A2")
        return ET.tostring(root)
    with pytest.raises(XlsxError, match="duplicate"):
        read_xlsx(mutate(write_xlsx(workbook()), "xl/worksheets/sheet1.xml", update))
