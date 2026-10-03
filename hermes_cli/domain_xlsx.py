"""Bounded, inert OOXML tables. Formula caches are preserved, never trusted/recalculated.

This is deliberately not a general Excel editor. Unsupported package parts or sheet
features fail closed rather than silently disappearing during a round trip.
"""
from __future__ import annotations

import io
import posixpath
import re
import struct
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DOCREL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MAX_BYTES = 8_000_000
MAX_UNCOMPRESSED = 16_000_000
MAX_ROWS = 10_000
MAX_COLUMNS = 128
MAX_CELLS = 100_000
MAX_TEXT = 32_767
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class XlsxError(ValueError):
    """Malformed, unsafe or unsupported workbook."""


@dataclass(frozen=True)
class XlsxCell:
    value: str | Decimal | bool | date | None = None
    formula: str | None = None
    style: int = 0


@dataclass(frozen=True)
class XlsxSheet:
    name: str
    rows: tuple[tuple[XlsxCell, ...], ...]


@dataclass(frozen=True)
class XlsxWorkbook:
    sheets: tuple[XlsxSheet, ...]
    styles_xml: bytes | None = None
    date1904: bool = False


def _tag(name: str) -> str:
    return "{" + MAIN + "}" + name


def _xml(data: bytes, expected: str) -> ET.Element:
    # UTF-16 would bypass a raw ASCII DTD scan. The admitted subset is UTF-8 only.
    if b"\x00" in data or re.search(br"<!\s*(?:DOCTYPE|ENTITY)", data, re.I):
        raise XlsxError("DTD/entities and non-UTF-8 XML are unsupported")
    try:
        text = data.decode("utf-8-sig")
        declaration = re.match(r"<\?xml\s+[^?]*encoding\s*=\s*['\"]([^'\"]+)", text)
        if declaration and declaration[1].lower() not in {"utf-8", "utf8"}:
            raise XlsxError("only UTF-8 XML is supported")
        root = ET.fromstring(text)
    except (ValueError, ET.ParseError) as exc:
        raise XlsxError("invalid UTF-8 XML") from exc
    if root.tag != expected:
        raise XlsxError("unexpected XML root")
    stack = [(root, 0)]
    count = 0
    while stack:
        element, depth = stack.pop()
        count += 1
        if depth > 24 or count > 400_000 or len(element.attrib) > 32:
            raise XlsxError("XML structure exceeds bounded subset")
        if any(len(v) > MAX_TEXT for v in element.attrib.values()):
            raise XlsxError("oversized XML attribute")
        stack.extend((child, depth + 1) for child in element)
    return root


def _package(data: bytes) -> dict[str, bytes]:
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise XlsxError("workbook exceeds byte limit")
    if len(data) < 22 or not data.startswith(b"PK\x03\x04") or data[-22:-18] != b"PK\x05\x06":
        raise XlsxError("ZIP preamble, suffix, comment or missing directory")
    disk, directory_disk, disk_count, total_count, directory_size, directory_offset, comment_size = struct.unpack("<HHHHIIH", data[-18:])
    if disk or directory_disk or disk_count != total_count or comment_size or directory_offset + directory_size != len(data) - 22:
        raise XlsxError("unsupported split, ZIP64 or noncontiguous ZIP directory")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 64 or len(set(names)) != len(names):
                raise XlsxError("too many or duplicate ZIP members")
            total = 0
            parts = {}
            next_offset = 0
            for entry in entries:
                name = entry.filename
                if (name.startswith("/") or "\\" in name or ":" in name
                        or any(p in {"", ".", ".."} for p in name.split("/"))):
                    raise XlsxError("unsafe ZIP member path")
                if entry.flag_bits & 1 or entry.compress_type not in {0, 8}:
                    raise XlsxError("encrypted/unsupported ZIP compression")
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise XlsxError("ZIP symlinks are unsupported")
                if entry.extra or entry.comment or entry.flag_bits & 8 or entry.header_offset != next_offset:
                    raise XlsxError("ZIP extras, descriptors or orphan records are unsupported")
                if data[next_offset:next_offset + 4] != b"PK\x03\x04" or next_offset + 30 > directory_offset:
                    raise XlsxError("invalid local ZIP record")
                name_size, extra_size = struct.unpack("<HH", data[next_offset + 26:next_offset + 30])
                if extra_size:
                    raise XlsxError("local ZIP extras are unsupported")
                next_offset += 30 + name_size + entry.compress_size
                if next_offset > directory_offset:
                    raise XlsxError("overlapping local ZIP records")
                total += entry.file_size
                if (entry.file_size > MAX_BYTES or total > MAX_UNCOMPRESSED
                        or entry.file_size > max(1, entry.compress_size) * 200):
                    raise XlsxError("ZIP expansion exceeds limit")
                parts[name] = archive.read(entry)
            if next_offset != directory_offset or len(entries) != total_count:
                raise XlsxError("orphan ZIP bytes or directory count mismatch")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
        raise XlsxError("invalid ZIP workbook") from exc
    required = {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                "xl/_rels/workbook.xml.rels"}
    if not required <= parts.keys():
        raise XlsxError("missing workbook package parts")
    allowed = re.compile(r"(?:\[Content_Types\]\.xml|_rels/\.rels|xl/workbook\.xml|"
                         r"xl/_rels/workbook\.xml\.rels|xl/(?:styles|sharedStrings)\.xml|"
                         r"xl/worksheets/sheet[1-9][0-9]*\.xml)")
    if any(not allowed.fullmatch(name) for name in parts):
        raise XlsxError("unsupported package part (macros, links, drawings or metadata)")
    return parts


def _relationships(data: bytes, base: str, parts: dict[str, bytes]) -> dict[str, tuple[str, str]]:
    root = _xml(data, "{" + REL + "}Relationships")
    result = {}
    for item in root:
        if item.tag != "{" + REL + "}Relationship":
            raise XlsxError("unsupported relationship")
        identifier, target, kind = (item.get(key, "") for key in ("Id", "Target", "Type"))
        if (not identifier or identifier in result or item.get("TargetMode", "Internal") != "Internal"
                or not target or ":" in target or "\\" in target or target.startswith("//")
                or any(p == ".." for p in target.split("/"))):
            raise XlsxError("external, duplicate or unsafe relationship")
        path = posixpath.normpath(posixpath.join(base, target.lstrip("/"))) if not target.startswith("/") else target[1:]
        if path not in parts:
            raise XlsxError("relationship target is missing")
        result[identifier] = (kind, path)
    return result


def _check_content_types(parts: dict[str, bytes]) -> None:
    root = _xml(parts["[Content_Types].xml"], "{" + CT + "}Types")
    allowed = {"application/xml", "application/vnd.openxmlformats-package.relationships+xml",
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
               "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
               "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"}
    defaults, overrides = {}, {}
    for item in root:
        if item.tag not in {"{" + CT + "}Default", "{" + CT + "}Override"} or item.get("ContentType") not in allowed:
            raise XlsxError("unsupported workbook content type")
        field, target = ("Extension", defaults) if item.tag == "{" + CT + "}Default" else ("PartName", overrides)
        name = item.get(field)
        if not name or name in target or set(item.attrib) != {field, "ContentType"} or len(item):
            raise XlsxError("invalid or duplicate content type declaration")
        target[name] = item.get("ContentType")
    expected = {"xl/workbook.xml": "sheet.main", "xl/styles.xml": "styles", "xl/sharedStrings.xml": "sharedStrings"}
    for name in parts:
        if name == "[Content_Types].xml":
            continue
        kind = expected.get(name, "worksheet" if name.startswith("xl/worksheets/") else None)
        required = ("application/vnd.openxmlformats-officedocument.spreadsheetml." + kind + "+xml" if kind else
                    "application/vnd.openxmlformats-package.relationships+xml")
        if overrides.get("/" + name, defaults.get(name.rsplit(".", 1)[-1])) != required:
            raise XlsxError("missing or mismatched content type")
    if any(name[1:] not in parts for name in overrides):
        raise XlsxError("content type names a missing package part")


def _simple_children(element: ET.Element, allowed: set[str]) -> None:
    if any(child.tag not in {_tag(name) for name in allowed} for child in element):
        raise XlsxError("unsupported worksheet/XML feature")


def _text(element: ET.Element) -> str:
    _simple_children(element, {"t"})
    children = list(element)
    if len(children) > 1:
        raise XlsxError("rich strings are unsupported")
    value = "" if not children else children[0].text or ""
    if len(value) > MAX_TEXT:
        raise XlsxError("cell text exceeds limit")
    return value


def _number(value: str) -> Decimal:
    if len(value) > 128 or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d{1,3})?", value):
        raise XlsxError("invalid or oversized numeric cell")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise XlsxError("invalid numeric cell") from exc
    if not result.is_finite() or abs(result.adjusted()) > 300:
        raise XlsxError("numeric cell outside supported range")
    return result


def _formula(value: str) -> str:
    # Only current-sheet arithmetic and a small inert function vocabulary are
    # preserved. No external/DDE references, names, volatile functions or strings.
    if not value or len(value) > 4096 or not re.fullmatch(r"[A-Za-z0-9$.,:()+*/^% =<>-]+", value):
        raise XlsxError("unsupported formula syntax")
    tokens = re.findall(r"[A-Za-z_][A-Za-z_0-9]*", value.replace("$", ""))
    functions = {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "ROUND", "ABS"}
    for token in tokens:
        if token.upper() not in functions and not re.fullmatch(r"[A-Za-z]{1,3}[1-9][0-9]{0,6}", token):
            raise XlsxError("unsupported formula function or named reference")
    return value


def _cell(element: ET.Element, strings: list[str], style_count: int) -> XlsxCell:
    _simple_children(element, {"f", "v", "is"})
    for kind in ("f", "v", "is"):
        if len(element.findall(_tag(kind))) > 1:
            raise XlsxError("duplicate cell child")
    if any(len(child) for child in element if child.tag in {_tag("f"), _tag("v")}):
        raise XlsxError("formula/value cells cannot contain nested XML")
    formula = element.find(_tag("f"))
    literal = None
    if formula is not None:
        if formula.attrib:
            raise XlsxError("shared/array formulas are unsupported")
        literal = _formula(formula.text or "")
    try:
        style = int(element.get("s", "0"))
    except ValueError as exc:
        raise XlsxError("invalid cell style") from exc
    if style < 0 or style >= style_count:
        raise XlsxError("missing cell style")
    kind = element.get("t", "n")
    value_element = element.find(_tag("v"))
    raw = None if value_element is None else value_element.text
    if kind == "inlineStr":
        inline = element.find(_tag("is"))
        if inline is None or literal is not None or raw is not None:
            raise XlsxError("invalid inline string")
        return XlsxCell(_text(inline), None, style)
    if element.find(_tag("is")) is not None:
        raise XlsxError("unexpected inline string")
    def string_index(value: str) -> str:
        if not value.isdigit() or int(value) >= len(strings):
            raise XlsxError("invalid shared string index")
        return strings[int(value)]
    def boolean(value: str) -> bool:
        if value not in {"0", "1"}:
            raise XlsxError("invalid boolean cell")
        return value == "1"
    parsers = {"n": _number, "s": string_index, "b": boolean, "str": str, "d": date.fromisoformat}
    if kind not in parsers:
        raise XlsxError("unsupported cell type/error value")
    try:
        value = None if raw is None else parsers[kind](raw)
    except ValueError as exc:
        raise XlsxError("invalid cell value") from exc
    if isinstance(value, str) and len(value) > MAX_TEXT:
        raise XlsxError("cell text exceeds limit")
    return XlsxCell(value, literal, style)


def _sheet(name: str, data: bytes, strings: list[str], style_count: int) -> XlsxSheet:
    root = _xml(data, _tag("worksheet"))
    _simple_children(root, {"sheetData", "dimension"})
    if len(root.findall(_tag("sheetData"))) != 1:
        raise XlsxError("missing or duplicate sheet data")
    rows = []
    for row in root.find(_tag("sheetData")):
        if row.tag != _tag("row") or set(row.attrib) - {"r"}:
            raise XlsxError("unsupported row attributes")
        try:
            index = int(row.get("r", "0"))
        except ValueError as exc:
            raise XlsxError("invalid row number") from exc
        if index <= len(rows) or index > MAX_ROWS:
            raise XlsxError("duplicate, unordered or oversized row")
        rows.extend(() for _ in range(index - len(rows) - 1))
        values = []
        for cell in row:
            if cell.tag != _tag("c") or set(cell.attrib) - {"r", "s", "t"}:
                raise XlsxError("unsupported cell attributes")
            match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", cell.get("r", ""))
            if not match or int(match[2]) != index:
                raise XlsxError("invalid cell reference")
            column = 0
            for letter in match[1]:
                column = column * 26 + ord(letter) - 64
            if column <= len(values) or column > MAX_COLUMNS:
                raise XlsxError("duplicate, unordered or oversized column")
            values.extend(XlsxCell() for _ in range(column - len(values) - 1))
            values.append(_cell(cell, strings, style_count))
        rows.append(tuple(values))
    width = max((len(row) for row in rows), default=0)
    if len(rows) * width > MAX_CELLS:
        raise XlsxError("worksheet cell limit exceeded")
    return XlsxSheet(name, tuple(row + (XlsxCell(),) * (width - len(row)) for row in rows))


def read_xlsx(data: bytes) -> XlsxWorkbook:
    parts = _package(data)
    _check_content_types(parts)
    root_rel = _relationships(parts["_rels/.rels"], "", parts)
    if list(root_rel.values()) != [(DOCREL + "/officeDocument", "xl/workbook.xml")]:
        raise XlsxError("unsupported package relationships")
    relations = _relationships(parts["xl/_rels/workbook.xml.rels"], "xl", parts)
    allowed_relations = {DOCREL + "/" + kind for kind in ("worksheet", "styles", "sharedStrings")}
    if any(kind not in allowed_relations for kind, _ in relations.values()):
        raise XlsxError("unsupported workbook relationship")
    strings = []
    if "xl/sharedStrings.xml" in parts:
        root = _xml(parts["xl/sharedStrings.xml"], _tag("sst"))
        _simple_children(root, {"si"})
        strings = [_text(item) for item in root]
        if len(strings) > MAX_CELLS:
            raise XlsxError("too many shared strings")
    styles = parts.get("xl/styles.xml")
    style_count = 1
    if styles is not None:
        root = _xml(styles, _tag("styleSheet"))
        permitted = {"styleSheet", "numFmts", "numFmt", "fonts", "font", "b", "i", "u", "sz", "color",
                     "name", "family", "scheme", "fills", "fill", "patternFill", "fgColor", "bgColor",
                     "borders", "border", "left", "right", "top", "bottom", "diagonal", "cellStyleXfs",
                     "cellXfs", "xf", "alignment", "protection", "cellStyles", "cellStyle"}
        if any(item.tag not in {_tag(tag) for tag in permitted} for item in root.iter()):
            raise XlsxError("unsupported workbook styling")
        cell_xfs = root.find(_tag("cellXfs"))
        style_count = len(cell_xfs) if cell_xfs is not None else 1
        if not 1 <= style_count <= 1024:
            raise XlsxError("invalid style count")
    workbook = _xml(parts["xl/workbook.xml"], _tag("workbook"))
    _simple_children(workbook, {"sheets", "workbookPr", "calcPr"})
    if len(workbook.findall(_tag("sheets"))) != 1:
        raise XlsxError("missing/duplicate workbook sheets")
    properties = workbook.find(_tag("workbookPr"))
    if properties is not None and set(properties.attrib) - {"date1904"}:
        raise XlsxError("unsupported workbook properties")
    date1904 = properties is not None and properties.get("date1904", "0") in {"1", "true"}
    sheets = []
    seen_names = set()
    seen_paths = set()
    seen_ids = set()
    for item in workbook.find(_tag("sheets")):
        if item.tag != _tag("sheet") or set(item.attrib) - {"name", "sheetId", "{" + DOCREL + "}id"}:
            raise XlsxError("unsupported sheet declaration")
        name = item.get("name", "")
        if (not name or len(name) > 31 or re.search(r"[\\/*?:\[\]]", name)
                or name.casefold() in seen_names):
            raise XlsxError("invalid or duplicate sheet name")
        seen_names.add(name.casefold())
        sheet_id = item.get("sheetId", "")
        if not sheet_id.isdigit() or not 1 <= int(sheet_id) <= 65535 or int(sheet_id) in seen_ids:
            raise XlsxError("invalid or duplicate sheet identifier")
        seen_ids.add(int(sheet_id))
        kind, path = relations.get(item.get("{" + DOCREL + "}id"), ("", ""))
        if kind != DOCREL + "/worksheet" or path in seen_paths:
            raise XlsxError("invalid or duplicate sheet relationship")
        seen_paths.add(path)
        sheets.append(_sheet(name, parts[path], strings, style_count))
    if not 1 <= len(sheets) <= 16 or sum(len(s.rows) * max((len(r) for r in s.rows), default=0) for s in sheets) > MAX_CELLS:
        raise XlsxError("workbook sheet/cell bound exceeded")
    referenced = {path for _, path in relations.values()}
    if {path for kind, path in relations.values() if kind == DOCREL + "/worksheet"} != seen_paths:
        raise XlsxError("worksheet relationship is not declared in the workbook")
    if set(parts) - {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"} != referenced:
        raise XlsxError("unreferenced workbook package part")
    return XlsxWorkbook(tuple(sheets), styles, date1904)


def validate_xlsx(data: bytes) -> dict:
    workbook = read_xlsx(data)
    formulas = sum(cell.formula is not None for sheet in workbook.sheets for row in sheet.rows for cell in row)
    return {"format": "xlsx", "sheets": [{"name": sheet.name, "rows": len(sheet.rows),
            "columns": max((len(row) for row in sheet.rows), default=0)} for sheet in workbook.sheets],
            "formula_count": formulas, "formula_status": "preserved_not_recalculated",
            "warnings": ["Formula caches are unverified; no recalculation performed"] if formulas else []}


def column_name(index: int) -> str:
    if not 1 <= index <= MAX_COLUMNS:
        raise XlsxError("column outside supported range")
    result = ""
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


def _write_cell(parent: ET.Element, reference: str, cell: XlsxCell) -> None:
    attrs = {"r": reference}
    if cell.style:
        attrs["s"] = str(cell.style)
    value = cell.value
    if isinstance(value, str):
        attrs["t"] = "str" if cell.formula else "inlineStr"
    if isinstance(value, bool):
        attrs["t"] = "b"
    if isinstance(value, date):
        attrs["t"] = "d"
    element = ET.SubElement(parent, _tag("c"), attrs)
    if cell.formula is not None:
        ET.SubElement(element, _tag("f")).text = _formula(cell.formula)
    if value is None:
        return
    if isinstance(value, str) and not cell.formula:
        text = ET.SubElement(ET.SubElement(element, _tag("is")), _tag("t"), {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
        text.text = value
        return
    if not isinstance(value, (str, Decimal, int, bool, date)) or isinstance(value, float):
        raise XlsxError("unsupported exported cell value")
    text = str(int(value)) if isinstance(value, bool) else str(value)
    ET.SubElement(element, _tag("v")).text = text


def write_xlsx(workbook: XlsxWorkbook) -> bytes:
    """Export complete, deterministic bytes and reopen before returning them."""
    if not isinstance(workbook, XlsxWorkbook) or not 1 <= len(workbook.sheets) <= 16:
        raise XlsxError("invalid workbook")
    content = ET.Element("{" + CT + "}Types")
    ET.SubElement(content, "{" + CT + "}Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    ET.SubElement(content, "{" + CT + "}Default", Extension="xml", ContentType="application/xml")
    root_rel = ET.Element("{" + REL + "}Relationships")
    ET.SubElement(root_rel, "{" + REL + "}Relationship", Id="rId1", Type=DOCREL + "/officeDocument", Target="xl/workbook.xml")
    relations = ET.Element("{" + REL + "}Relationships")
    root = ET.Element(_tag("workbook"))
    ET.SubElement(root, _tag("workbookPr"), date1904="1" if workbook.date1904 else "0")
    sheets = ET.SubElement(root, _tag("sheets"))
    # Full calculation is requested from a future viewer; this is not a claim
    # that this adapter has recalculated anything.
    ET.SubElement(root, _tag("calcPr"), calcMode="auto", fullCalcOnLoad="1", forceFullCalc="1")
    parts = {}
    def override(path: str, kind: str) -> None:
        ET.SubElement(content, "{" + CT + "}Override", PartName="/" + path,
                      ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml." + kind + "+xml")
    override("xl/workbook.xml", "sheet.main")
    for number, sheet in enumerate(workbook.sheets, 1):
        if len(sheet.rows) > MAX_ROWS or sum(len(row) for row in sheet.rows) > MAX_CELLS:
            raise XlsxError("export exceeds row/cell limits")
        path = f"xl/worksheets/sheet{number}.xml"
        identifier = f"rId{number}"
        ET.SubElement(sheets, _tag("sheet"), {"name": sheet.name, "sheetId": str(number), "{" + DOCREL + "}id": identifier})
        ET.SubElement(relations, "{" + REL + "}Relationship", Id=identifier, Type=DOCREL + "/worksheet", Target=path[3:])
        override(path, "worksheet")
        worksheet = ET.Element(_tag("worksheet"))
        data = ET.SubElement(worksheet, _tag("sheetData"))
        for row_number, cells in enumerate(sheet.rows, 1):
            row = ET.SubElement(data, _tag("row"), r=str(row_number))
            for index, cell in enumerate(cells, 1):
                _write_cell(row, column_name(index) + str(row_number), cell)
        parts[path] = ET.tostring(worksheet, encoding="utf-8", xml_declaration=True)
    if workbook.styles_xml is not None:
        parts["xl/styles.xml"] = workbook.styles_xml
        override("xl/styles.xml", "styles")
        ET.SubElement(relations, "{" + REL + "}Relationship", Id="styles", Type=DOCREL + "/styles", Target="styles.xml")
    for path, element in {"[Content_Types].xml": content, "_rels/.rels": root_rel,
                          "xl/workbook.xml": root, "xl/_rels/workbook.xml.rels": relations}.items():
        parts[path] = ET.tostring(element, encoding="utf-8", xml_declaration=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(parts):
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), parts[name])
    result = output.getvalue()
    read_xlsx(result)
    return result
