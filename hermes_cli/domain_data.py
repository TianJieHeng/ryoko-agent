"""Finite data transformations with original bytes, explicit assumptions and row lineage.

Inputs are bytes supplied by the authorized artifact owner. This module has no file,
network, shell, Python-evaluation or credential access.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import struct
import zlib
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from html import escape

from hermes_cli.domain_xlsx import (
    MAX_BYTES, MAX_CELLS, MAX_COLUMNS, MAX_ROWS, XLSX_MIME,
    XlsxCell, XlsxSheet, XlsxWorkbook, read_xlsx, write_xlsx,
)


class DataError(ValueError):
    """Invalid or unsupported explicit data recipe."""


class ManualMappingRequired(DataError):
    """Column ambiguity must be resolved by a human-supplied positional map."""


@dataclass(frozen=True, order=True)
class SourceRow:
    source_id: str
    sha256: str
    sheet: str
    row: int

    def to_dict(self) -> dict:
        return {"source_id": self.source_id, "sha256": self.sha256,
                "sheet": self.sheet, "row": self.row}


@dataclass(frozen=True)
class InputBytes:
    source_id: str
    sha256: str
    mime: str
    content_bytes: bytes


@dataclass(frozen=True)
class DataRow:
    values: tuple
    lineage: tuple[SourceRow, ...]


@dataclass(frozen=True)
class DataTable:
    columns: tuple[str, ...]
    rows: tuple[DataRow, ...]
    sources: tuple[InputBytes, ...]
    recipe: tuple[dict, ...]
    units: tuple[tuple[str, str], ...] = ()
    # Source styles and date system survive pure XLSX tabular round trips.
    styles_xml: bytes | None = None
    date1904: bool = False

    def records(self) -> list[dict]:
        return [dict(zip(self.columns, row.values)) for row in self.rows]


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _encoded(value):
    if isinstance(value, XlsxCell):
        return {"formula": value.formula, "cached_value": _encoded(value.value),
                "cache_status": "unverified_not_recalculated", "style": value.style}
    if isinstance(value, (Decimal, date)):
        return str(value)
    return value


def _source(source_id: str, content_bytes: bytes, mime: str, expected_sha256: str | None) -> InputBytes:
    if not isinstance(source_id, str) or not source_id or len(source_id) > 256:
        raise DataError("source_id must be a bounded nonempty string")
    if not isinstance(content_bytes, bytes) or not content_bytes or len(content_bytes) > MAX_BYTES:
        raise DataError("input byte bound exceeded or input empty")
    digest = hashlib.sha256(content_bytes).hexdigest()
    if expected_sha256 is not None and expected_sha256 != digest:
        raise DataError("input digest does not match the selected immutable version")
    return InputBytes(source_id, digest, mime, content_bytes)


def _options(options: dict) -> dict:
    if not isinstance(options, dict):
        raise DataError("explicit ingestion options are required")
    required = {"encoding", "delimiter", "date_format", "currency", "null_values", "units", "duplicate_keys"}
    allowed = required | {"mapping", "column_types", "keys", "decimal_separator", "grouping_separator", "sheet"}
    if required - options.keys() or options.keys() - allowed:
        raise DataError("provide encoding, delimiter, date_format, currency, null_values, units and duplicate_keys explicitly")
    result = dict(options)
    if result["encoding"] not in {"utf-8", "utf-8-sig", "latin-1"}:
        raise DataError("unsupported encoding")
    if result["delimiter"] not in {",", ";", "\t", "|"}:
        raise DataError("unsupported delimiter")
    if result["date_format"] not in {None, "YYYY-MM-DD", "DD/MM/YYYY", "MM/DD/YYYY", "excel_serial"}:
        raise DataError("unsupported or ambiguous date format")
    if result["currency"] is not None and (not isinstance(result["currency"], str) or not re.fullmatch(r"[A-Z]{3}", result["currency"])):
        raise DataError("currency must be an explicit ISO code or null")
    if not isinstance(result["null_values"], list) or len(result["null_values"]) > 32 or any(not isinstance(v, str) or len(v) > 128 for v in result["null_values"]):
        raise DataError("null_values must list exact bounded strings")
    if not isinstance(result["units"], dict) or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 64 for k, v in result["units"].items()):
        raise DataError("units must map columns to explicit units")
    if result["duplicate_keys"] not in {"allow", "reject", "keep_first"}:
        raise DataError("duplicate_keys must be allow, reject or keep_first")
    result.setdefault("decimal_separator", ".")
    result.setdefault("grouping_separator", None)
    if result["decimal_separator"] not in {".", ","} or result["grouping_separator"] not in {None, ".", ","} or result["decimal_separator"] == result["grouping_separator"]:
        raise DataError("decimal and grouping separators must be explicit and distinct")
    result.setdefault("keys", [])
    if not isinstance(result["keys"], list) or any(not isinstance(key, str) for key in result["keys"]):
        raise DataError("keys must list column names")
    result.setdefault("column_types", {})
    if not isinstance(result["column_types"], dict):
        raise DataError("column_types must be a mapping")
    result.setdefault("mapping", None)
    result["formula_policy"] = "preserve_unverified_cache_reject_numeric_use"
    return result


def _mapping(headers: list[str], mapping) -> tuple[tuple[str, ...], tuple[int, ...]]:
    if not 1 <= len(headers) <= MAX_COLUMNS or any(not isinstance(h, str) or not h.strip() or len(h) > 256 for h in headers):
        raise DataError("headers must be nonempty bounded strings")
    normalized = [header.strip().casefold() for header in headers]
    if mapping is None:
        if len(set(normalized)) != len(headers):
            raise ManualMappingRequired("ambiguous column names require an explicit column-name to zero-based-position mapping")
        return tuple(headers), tuple(range(len(headers)))
    if not isinstance(mapping, dict) or not mapping or len(mapping) > MAX_COLUMNS:
        raise DataError("mapping must name selected output columns")
    names, indices = [], []
    for target, source in mapping.items():
        if not isinstance(target, str) or not target.strip() or len(target) > 256 or target.strip().casefold() in [n.strip().casefold() for n in names]:
            raise DataError("mapping has invalid or colliding output names")
        if isinstance(source, str):
            matches = [i for i, name in enumerate(headers) if name == source]
            if len(matches) != 1:
                raise ManualMappingRequired("ambiguous source column needs a zero-based position")
            index = matches[0]
        elif isinstance(source, int) and not isinstance(source, bool):
            index = source
        else:
            raise DataError("mapping source must be a column name or zero-based position")
        if not 0 <= index < len(headers):
            raise DataError("mapped column position is missing")
        names.append(target)
        indices.append(index)
    return tuple(names), tuple(indices)


def _decimal(value, options: dict, currency: bool = False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise DataError("booleans/floats cannot be numeric data")
    if isinstance(value, Decimal):
        result = value
    else:
        value = str(value)
        if currency:
            code = options["currency"]
            if code is None:
                raise DataError("currency columns require an explicit currency assumption")
            if value.startswith(code + " "):
                value = value[len(code) + 1:]
        decimal, grouping = options["decimal_separator"], options["grouping_separator"]
        if grouping and grouping in value:
            expression = r"[+-]?\d{1,3}(?:" + re.escape(grouping) + r"\d{3})+(?:" + re.escape(decimal) + r"\d+)?"
            if not re.fullmatch(expression, value):
                raise DataError("invalid grouping separator placement")
            value = value.replace(grouping, "")
        value = value.replace(decimal, ".")
        if len(value) > 128 or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value):
            raise DataError("invalid numeric value under declared separators")
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise DataError("invalid decimal") from exc
    if (not result.is_finite() or abs(result.adjusted()) > 30
            or len(result.as_tuple().digits) > 38 or abs(result.as_tuple().exponent) > 30):
        raise DataError("numeric value outside supported finite range")
    return result


def _convert(value, kind: str, options: dict, date1904: bool):
    if isinstance(value, XlsxCell):
        if value.formula is not None:
            return value
        value = value.value
    if value is None or isinstance(value, str) and value in options["null_values"]:
        return None
    def parse_date(raw):
        if isinstance(raw, date):
            return raw
        fmt = options["date_format"]
        if fmt == "excel_serial":
            number = _decimal(raw, options)
            if number != number.to_integral_value() or not 0 <= number <= 2_900_000:
                raise DataError("only whole Excel date serials are supported")
            serial = int(number)
            if not date1904 and serial == 60:
                raise DataError("Excel's nonexistent 1900-02-29 must be resolved manually")
            return (date(1904, 1, 1) + timedelta(days=serial) if date1904 else
                    date(1899, 12, 31) + timedelta(days=serial - (serial > 60)))
        formats = {"YYYY-MM-DD": "%Y-%m-%d", "DD/MM/YYYY": "%d/%m/%Y", "MM/DD/YYYY": "%m/%d/%Y"}
        if fmt not in formats:
            raise DataError("date columns require an explicit date format")
        try:
            return datetime.strptime(str(raw), formats[fmt]).date()
        except ValueError as exc:
            raise DataError("date does not match declared format") from exc
    def integer(raw):
        number = _decimal(raw, options)
        if number != number.to_integral_value():
            raise DataError("fractional value in integer column")
        return int(number)
    parsers = {"text": str, "decimal": lambda raw: _decimal(raw, options),
               "integer": integer, "currency": lambda raw: _decimal(raw, options, True),
               "date": parse_date, "native": lambda raw: raw}
    if kind not in parsers:
        raise DataError("unsupported column type")
    return parsers[kind](value)


def _table(source: InputBytes, headers: list[str], data_rows: list, options: dict,
           sheet: str, *, styles_xml=None, date1904=False) -> DataTable:
    columns, indices = _mapping(headers, options["mapping"])
    has_formulas = any(isinstance(value, XlsxCell) and value.formula for row in data_rows for value in row)
    if has_formulas and (indices != tuple(range(len(headers))) or options["duplicate_keys"] == "keep_first"):
        raise DataError("moving formula rows/columns requires an unsupported formula rewrite")
    if set(options["column_types"]) - set(columns) or set(options["units"]) - set(columns) or set(options["keys"]) - set(columns):
        raise DataError("type/unit/key declaration names an unknown mapped column")
    key_indices = tuple(columns.index(key) for key in options["keys"]) or tuple(range(len(columns)))
    rows, seen, positions = [], {}, {}
    for row_number, raw in enumerate(data_rows, 2):
        if len(raw) != len(headers):
            raise DataError(f"row {row_number} has a different field count")
        values = tuple(_convert(raw[index], options["column_types"].get(name, "native" if sheet else "text"), options, date1904)
                       for name, index in zip(columns, indices))
        key = tuple(values[index] for index in key_indices)
        if key in seen and options["duplicate_keys"] == "reject":
            raise DataError(f"duplicate key at source rows {seen[key]} and {row_number}")
        lineage = (SourceRow(source.source_id, source.sha256, sheet, row_number),)
        if key in seen and options["duplicate_keys"] == "keep_first":
            # A discarded duplicate is still part of the derivation of the retained row.
            position = positions[key]
            retained = rows[position]
            rows[position] = DataRow(retained.values, retained.lineage + lineage)
            continue
        seen[key] = row_number
        positions[key] = len(rows)
        rows.append(DataRow(values, lineage))
    recipe = {"operation": "ingest", "source_id": source.source_id, "sha256": source.sha256,
              "mime": source.mime, "sheet": sheet, "original_headers": headers,
              "resolved_mapping": dict(zip(columns, indices)), "options": options,
              "source_rows": len(data_rows), "retained_rows": len(rows)}
    return DataTable(columns, tuple(rows), (source,), (recipe,), tuple(sorted(options["units"].items())), styles_xml, date1904)


def ingest_csv(content_bytes: bytes, *, source_id: str, options: dict, expected_sha256: str | None = None) -> DataTable:
    source = _source(source_id, content_bytes, "text/csv", expected_sha256)
    options = _options(options)
    try:
        text = content_bytes.decode(options["encoding"], errors="strict")
        if "\x00" in text:
            raise DataError("NUL bytes in CSV")
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=options["delimiter"], strict=True)
        headers = next(reader)
        rows = []
        for row in reader:
            if len(rows) >= MAX_ROWS - 1 or (len(rows) + 2) * len(headers) > MAX_CELLS or any(len(value) > 32_767 for value in row):
                raise DataError("CSV row/cell bound exceeded")
            rows.append(row)
    except (UnicodeError, csv.Error, StopIteration) as exc:
        raise DataError("CSV does not match the declared encoding/delimiter") from exc
    return _table(source, headers, rows, options, "")


def ingest_xlsx(content_bytes: bytes, *, source_id: str, options: dict, expected_sha256: str | None = None) -> DataTable:
    source = _source(source_id, content_bytes, XLSX_MIME, expected_sha256)
    options = _options(options)
    workbook = read_xlsx(content_bytes)
    name = options.get("sheet")
    if name is None and len(workbook.sheets) != 1:
        raise ManualMappingRequired("multi-sheet workbook requires an explicit sheet selection")
    matches = [sheet for sheet in workbook.sheets if name is None or sheet.name == name]
    if len(matches) != 1 or not matches[0].rows:
        raise DataError("selected sheet is missing or empty")
    sheet = matches[0]
    if any(cell.formula or not isinstance(cell.value, str) for cell in sheet.rows[0]):
        raise DataError("headers must be literal nonempty text")
    return _table(source, [cell.value for cell in sheet.rows[0]], list(sheet.rows[1:]), options,
                  sheet.name, styles_xml=workbook.styles_xml, date1904=workbook.date1904)


def profile_table(table: DataTable) -> dict:
    profiles = []
    for index, name in enumerate(table.columns):
        values = [row.values[index] for row in table.rows]
        numeric = [value for value in values if isinstance(value, (Decimal, int)) and not isinstance(value, bool)]
        profiles.append({"name": name, "null_count": sum(value is None for value in values),
                         "distinct_count": len(set(values)), "formula_count": sum(isinstance(value, XlsxCell) and value.formula is not None for value in values),
                         "minimum": str(min(numeric)) if numeric else None, "maximum": str(max(numeric)) if numeric else None,
                         "unit": dict(table.units).get(name)})
    return {"rows": len(table.rows), "columns": profiles, "sources": [{"source_id": s.source_id, "sha256": s.sha256} for s in table.sources]}


def _lineage(rows) -> tuple[SourceRow, ...]:
    return tuple(sorted({reference for row in rows for reference in row.lineage}))


def join_tables(left: DataTable, right: DataTable, *, left_on: list[str], right_on: list[str],
                how: str, cardinality: str, null_keys: str, right_prefix: str = "right.") -> DataTable:
    if how not in {"inner", "left"} or cardinality not in {"one_to_one", "many_to_one", "one_to_many", "many_to_many"} or null_keys not in {"reject", "never_match"}:
        raise DataError("join needs explicit supported how, cardinality and null_keys")
    if not left_on or len(left_on) != len(right_on) or len(set(left_on)) != len(left_on) or len(set(right_on)) != len(right_on):
        raise DataError("join keys must be equally sized, unique and nonempty")
    try:
        li, ri = tuple(left.columns.index(name) for name in left_on), tuple(right.columns.index(name) for name in right_on)
    except ValueError as exc:
        raise DataError("join key is missing") from exc
    lu, ru = dict(left.units), dict(right.units)
    if any(isinstance(value, XlsxCell) for table in (left, right) for row in table.rows for value in row.values):
        raise DataError("joining formula cells requires an unsupported formula-reference rewrite")
    if any(lu.get(a) != ru.get(b) for a, b in zip(left_on, right_on)):
        raise DataError("join key units differ; explicit conversion is required")
    def keys(table, indices):
        values = [tuple(row.values[i] for i in indices) for row in table.rows]
        if any(any(isinstance(value, XlsxCell) for value in key) for key in values):
            raise DataError("formula caches cannot be join keys")
        if null_keys == "reject" and any(None in key for key in values):
            raise DataError("null join key")
        return values
    lk, rk = keys(left, li), keys(right, ri)
    unique_left = cardinality in {"one_to_one", "one_to_many"}
    unique_right = cardinality in {"one_to_one", "many_to_one"}
    if (unique_left and any(count > 1 for key, count in Counter(lk).items() if None not in key)
            or unique_right and any(count > 1 for key, count in Counter(rk).items() if None not in key)):
        raise DataError("join violates declared cardinality")
    right_names = tuple(right_prefix + name for name in right.columns)
    columns = left.columns + right_names
    if len(columns) > MAX_COLUMNS or len(set(columns)) != len(columns):
        raise DataError("join output names collide or exceed bounds")
    index = {}
    for key, row in zip(rk, right.rows):
        if None not in key:
            index.setdefault(key, []).append(row)
    result = []
    for key, row in zip(lk, left.rows):
        matches = index.get(key, ()) if None not in key else ()
        if not matches and how == "left":
            matches = (DataRow((None,) * len(right.columns), ()),)
        for other in matches:
            if len(result) >= MAX_ROWS - 1 or (len(result) + 2) * len(columns) > MAX_CELLS:
                raise DataError("join expansion exceeds output bound")
            result.append(DataRow(row.values + other.values, _lineage((row, other))))
    sources = {s.source_id: s for s in left.sources}
    for source in right.sources:
        if source.source_id in sources and sources[source.source_id].sha256 != source.sha256:
            raise DataError("source identity references conflicting versions")
        sources[source.source_id] = source
    recipe = {"operation": "join", "left_on": left_on, "right_on": right_on, "how": how,
              "cardinality": cardinality, "null_keys": null_keys, "right_prefix": right_prefix,
              "left_sources": [s.source_id for s in left.sources], "right_sources": [s.source_id for s in right.sources]}
    return DataTable(columns, tuple(result), tuple(sources.values()), left.recipe + right.recipe + (recipe,),
                     left.units + tuple((right_prefix + name, unit) for name, unit in right.units))


def aggregate_table(table: DataTable, *, group_by: list[str], aggregations: dict, nulls: str) -> DataTable:
    if nulls not in {"reject", "skip"} or not isinstance(aggregations, dict) or not aggregations:
        raise DataError("aggregation needs operations and explicit null handling")
    if len(set(group_by)) != len(group_by) or any(name not in table.columns for name in group_by):
        raise DataError("invalid grouping columns")
    columns = tuple(group_by) + tuple(aggregations)
    if len(columns) > MAX_COLUMNS or len(set(columns)) != len(columns):
        raise DataError("aggregation names collide or exceed bound")
    operations = {"sum", "mean", "min", "max", "count"}
    for name, spec in aggregations.items():
        if not isinstance(name, str) or not name or not isinstance(spec, dict) or set(spec) != {"operation", "column"} or spec["operation"] not in operations or spec["column"] not in table.columns:
            raise DataError("unsupported aggregation specification")
    groups = {}
    for row in table.rows:
        key = tuple(row.values[table.columns.index(name)] for name in group_by)
        if any(isinstance(value, XlsxCell) for value in key):
            raise DataError("formula caches cannot be grouping keys")
        groups.setdefault(key, []).append(row)
    result = []
    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        for key, rows in groups.items():
            values = []
            for spec in aggregations.values():
                selected = [row.values[table.columns.index(spec["column"])] for row in rows]
                if any(isinstance(value, XlsxCell) for value in selected):
                    raise DataError("formula caches cannot be aggregated; recalculation is unavailable")
                if nulls == "reject" and None in selected:
                    raise DataError("null in aggregation input")
                selected = [value for value in selected if value is not None]
                if spec["operation"] == "count":
                    values.append(len(selected))
                    continue
                if any(not isinstance(value, (int, Decimal)) or isinstance(value, bool) for value in selected):
                    raise DataError("numeric aggregation requires explicitly typed numeric columns")
                reducers = {"sum": lambda nums: sum(nums, Decimal(0)), "mean": lambda nums: sum(nums, Decimal(0)) / len(nums), "min": min, "max": max}
                values.append(reducers[spec["operation"]](selected) if selected else None)
            result.append(DataRow(key + tuple(values), _lineage(rows)))
    units = dict(table.units)
    output_units = tuple((name, units[name]) for name in group_by if name in units) + tuple(
        (name, "count" if spec["operation"] == "count" else units[spec["column"]]) for name, spec in aggregations.items()
        if spec["operation"] == "count" or spec["column"] in units)
    step = {"operation": "aggregate", "group_by": group_by, "aggregations": aggregations,
            "nulls": nulls, "decimal_precision": 80, "empty_numeric_group": "null", "order": "first_seen"}
    return DataTable(columns, tuple(result), table.sources, table.recipe + (step,), output_units)


def export_csv(table: DataTable) -> bytes:
    """Formula-bearing inputs require XLSX/notebook; CSV must not flatten formulas."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    def safe(value) -> None:
        if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
            raise DataError("active spreadsheet-like text cannot be safely exported as CSV; choose XLSX")
    for name in table.columns:
        safe(name)
    writer.writerow(table.columns)
    for row in table.rows:
        if any(isinstance(value, XlsxCell) for value in row.values):
            raise DataError("CSV cannot preserve formula/cache pairs; choose XLSX or notebook")
        for value in row.values:
            safe(value)
        writer.writerow(["" if value is None else str(value) for value in row.values])
    return output.getvalue().encode("utf-8")


def export_xlsx(table: DataTable, *, sheet_name: str = "Results") -> bytes:
    rows = [tuple(XlsxCell(name) for name in table.columns)]
    rows.extend(tuple(value if isinstance(value, XlsxCell) else XlsxCell(value) for value in row.values) for row in table.rows)
    return write_xlsx(XlsxWorkbook((XlsxSheet(sheet_name, tuple(rows)),), table.styles_xml, table.date1904))


def chart_table(table: DataTable, *, kind: str, category: str, value: str) -> dict:
    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        return _chart_table(table, kind=kind, category=category, value=value)


def _chart_table(table: DataTable, *, kind: str, category: str, value: str) -> dict:
    if kind not in {"bar", "line"} or category not in table.columns or value not in table.columns or not 1 <= len(table.rows) <= 50:
        raise DataError("charts support bar/line with 1..50 explicit rows")
    xi, yi = table.columns.index(category), table.columns.index(value)
    points = []
    for row in table.rows:
        y = row.values[yi]
        if not isinstance(y, (Decimal, int)) or isinstance(y, bool):
            raise DataError("chart values must be finite numeric values, not formula caches")
        points.append({"category": str(row.values[xi]), "value": str(y), "lineage": [item.to_dict() for item in row.lineage]})
    numeric = [Decimal(point["value"]) for point in points]
    lower, upper = min(min(numeric), Decimal(0)), max(max(numeric), Decimal(0))
    span = upper - lower or Decimal(1)
    baseline = 240 - (Decimal(0) - lower) / span * 200
    shapes = [f'<line x1="40" x2="600" y1="{baseline:.2f}" y2="{baseline:.2f}" stroke="black"/>']
    coordinates = []
    for index, point in enumerate(points):
        x = Decimal(50) + Decimal(index) * 540 / max(len(points) - 1, 1)
        y = Decimal(240) - (Decimal(point["value"]) - lower) / span * 200
        coordinates.append(f"{x:.2f},{y:.2f}")
        if kind == "bar":
            width = min(Decimal(30), Decimal(450) / len(points))
            shapes.append(f'<rect x="{x - width / 2:.2f}" y="{min(y, baseline):.2f}" width="{width:.2f}" height="{abs(baseline - y):.2f}" fill="#2768ad"/>')
        shapes.append(f'<text x="{x:.2f}" y="265" text-anchor="middle" font-size="10">{escape(point["category"][:30])}</text>')
    if kind == "line":
        shapes.insert(1, '<polyline points="' + " ".join(coordinates) + '" fill="none" stroke="#2768ad"/>')
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="300" viewBox="0 0 640 300"><title>' + escape(kind + ": " + value + " by " + category) + '</title>' + "".join(shapes) + '</svg>'
    return {"kind": kind, "category": category, "value": value, "unit": dict(table.units).get(value),
            "points": points, "svg_source": svg, "preview": "inert_source_only"}


_GLYPHS = {
    "A": "01110100011000111111100011000110001", "B": "11110100011000111110100011000111110",
    "C": "01111100001000010000100001000001111", "D": "11110100011000110001100011000111110",
    "E": "11111100001000011110100001000011111", "F": "11111100001000011110100001000010000",
    "G": "01111100001000010111100011000101111", "H": "10001100011000111111100011000110001",
    "I": "11111001000010000100001000010011111", "J": "00111000100001000010100101001001100",
    "K": "10001100101010011000101001001010001", "L": "10000100001000010000100001000011111",
    "M": "10001110111010110101100011000110001", "N": "10001110011010110011100011000110001",
    "O": "01110100011000110001100011000101110", "P": "11110100011000111110100001000010000",
    "Q": "01110100011000110001101011001001101", "R": "11110100011000111110101001001010001",
    "S": "01111100001000001110000010000111110", "T": "11111001000010000100001000010000100",
    "U": "10001100011000110001100011000101110", "V": "10001100011000110001100010101000100",
    "W": "10001100011000110101101011101110001", "X": "10001100010101000100010101000110001",
    "Y": "10001100010101000100001000010000100", "Z": "11111000010001000100010001000011111",
    "0": "01110100011001110101110011000101110", "1": "00100011000010000100001000010001110",
    "2": "01110100010000100010001000100011111", "3": "11110000010000101110000010000111110",
    "4": "00010001100101010010111110001000010", "5": "11111100001000011110000010000111110",
    "6": "01110100001000011110100011000101110", "7": "11111000010001000100010000100001000",
    "8": "01110100011000101110100011000101110", "9": "01110100011000101111000010000101110",
    "-": "00000000000000011111000000000000000", ".": "00000000000000000000000000110001100",
    "?": "01110100010000100010001000000000100", " ": "0" * 35,
}


def export_chart_png(chart: dict) -> bytes:
    """Small deterministic RGB chart; labels and exact points also remain in metadata."""
    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        return _chart_png(chart)


def _chart_png(chart: dict) -> bytes:
    width, height = 640, 320
    pixels = bytearray(b"\xff" * (width * height * 3))
    def pixel(x, y, color):
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3
            pixels[offset:offset + 3] = bytes(color)
    def line(x0, y0, x1, y1, color):
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        error = dx + dy
        while True:
            pixel(x0, y0, color)
            if x0 == x1 and y0 == y1:
                break
            twice = 2 * error
            if twice >= dy:
                error += dy
                x0 += sx
            if twice <= dx:
                error += dx
                y0 += sy
    def label(value, x, y):
        for character in value.upper()[:90]:
            for index, on in enumerate(_GLYPHS.get(character, _GLYPHS["?"])):
                if on == "1":
                    pixel(x + index % 5, y + index // 5, (30, 30, 30))
            x += 6
    points = chart["points"]
    numbers = [Decimal(point["value"]) for point in points]
    lower, upper = min(min(numbers), Decimal(0)), max(max(numbers), Decimal(0))
    span = upper - lower or Decimal(1)
    baseline = int(260 - (Decimal(0) - lower) * 210 / span)
    line(55, 45, 55, 260, (30, 30, 30))
    line(55, baseline, 610, baseline, (30, 30, 30))
    label(chart["value"] + " BY " + chart["category"] + " " + (chart["unit"] or ""), 55, 15)
    label(str(upper)[:8], 2, 43)
    label(str(lower)[:8], 2, 253)
    previous = None
    for index, point in enumerate(points):
        x = int(75 + Decimal(index) * 520 / max(len(points) - 1, 1))
        y = int(260 - (Decimal(point["value"]) - lower) * 210 / span)
        if chart["kind"] == "bar":
            half = max(1, min(15, 225 // len(points)))
            for column in range(x - half, x + half):
                line(column, min(y, baseline), column, max(y, baseline), (39, 104, 173))
        if chart["kind"] == "line" and previous is not None:
            line(*previous, x, y, (39, 104, 173))
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                pixel(x + dx, y + dy, (39, 104, 173))
        # Dense charts use the row index; full labels remain in the paired manifest.
        text = point["category"][:8] if len(points) <= 10 else str(index + 1)
        label(text, max(55, min(600 - len(text) * 6, x - len(text) * 3)), 280)
        previous = (x, y)
    raw = b"".join(b"\0" + pixels[row * width * 3:(row + 1) * width * 3] for row in range(height))
    def chunk(name, content):
        return struct.pack(">I", len(content)) + name + content + struct.pack(">I", zlib.crc32(name + content) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def export_notebook(table: DataTable, *, charts: list[dict] | None = None) -> bytes:
    """A complete, nonexecuting notebook, with original input bytes and full recipe."""
    result = {"columns": list(table.columns), "rows": [[_encoded(value) for value in row.values] for row in table.rows],
              "lineage": [[item.to_dict() for item in row.lineage] for row in table.rows],
              "units": dict(table.units), "recipe": list(table.recipe), "charts": charts or [],
              "inputs": [{"source_id": s.source_id, "sha256": s.sha256, "mime": s.mime,
                          "bytes_base64": base64.b64encode(s.content_bytes).decode("ascii")} for s in table.sources],
              "formula_status": "preserved_not_recalculated"}
    cells = [{"cell_type": "markdown", "metadata": {}, "source": ["# Reproducible data result\n", "Original bytes, explicit transformations, complete results and source-row lineage are preserved below. No code was executed; formula caches are unverified.\n"]},
             {"cell_type": "raw", "metadata": {"format": "application/json"}, "source": [_json(result).decode("utf-8")]}]
    notebook = {"nbformat": 4, "nbformat_minor": 4, "metadata": {"ryoko": {"recipe_version": 1, "execution": "not_executed"}}, "cells": cells}
    data = _json(notebook)
    if len(data) > 24_000_000:
        raise DataError("notebook exceeds bounded output size")
    return data


def build_data_package(inputs: list[dict], recipe: dict) -> dict:
    """Build bytes for DomainJob; caller owns live grants and immutable artifact reads."""
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 8 or not isinstance(recipe, dict):
        raise DataError("provide 1..8 byte inputs and an explicit recipe")
    if set(recipe) - {"base", "joins", "aggregate", "charts", "exports"}:
        raise DataError("unknown recipe operation")
    tables = {}
    parsers = {"text/csv": ingest_csv, XLSX_MIME: ingest_xlsx}
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {"source_id", "content_bytes", "mime", "expected_sha256", "options"}:
            raise DataError("input needs source_id, content_bytes, mime, expected_sha256 and options")
        if item["source_id"] in tables or item["mime"] not in parsers or not isinstance(item["expected_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", item["expected_sha256"]):
            raise DataError("duplicate source, unsupported MIME or missing immutable digest")
        tables[item["source_id"]] = parsers[item["mime"]](item["content_bytes"], source_id=item["source_id"],
                                                           options=item["options"], expected_sha256=item["expected_sha256"])
    base = recipe.get("base")
    if base not in tables:
        raise DataError("recipe base must select an input source_id")
    table = tables[base]
    joins = recipe.get("joins", [])
    if not isinstance(joins, list) or len(joins) > 7:
        raise DataError("join count exceeds bound")
    for step in joins:
        if not isinstance(step, dict) or set(step) - {"right", "left_on", "right_on", "how", "cardinality", "null_keys", "right_prefix"} or step.get("right") not in tables:
            raise DataError("invalid join step")
        required = {"right", "left_on", "right_on", "how", "cardinality", "null_keys"}
        if not required <= step.keys():
            raise DataError("join assumptions must be explicit")
        table = join_tables(table, tables[step["right"]], **{key: value for key, value in step.items() if key != "right"})
    if recipe.get("aggregate") is not None:
        step = recipe["aggregate"]
        if not isinstance(step, dict) or set(step) != {"group_by", "aggregations", "nulls"}:
            raise DataError("invalid aggregate step")
        table = aggregate_table(table, **step)
    chart_specs = recipe.get("charts", [])
    if not isinstance(chart_specs, list) or len(chart_specs) > 4 or any(not isinstance(s, dict) or set(s) != {"kind", "category", "value"} for s in chart_specs):
        raise DataError("provide at most four finite chart specifications")
    charts = [chart_table(table, **spec) for spec in chart_specs]
    exports = recipe.get("exports", ["xlsx", "ipynb"])
    if not isinstance(exports, list) or not exports or len(exports) > 3 or len(set(exports)) != len(exports) or set(exports) - {"csv", "xlsx", "ipynb"}:
        raise DataError("supported complete exports are csv, xlsx and ipynb")
    exporters = {"csv": ("text/csv", lambda: export_csv(table)), "xlsx": (XLSX_MIME, lambda: export_xlsx(table)),
                 "ipynb": ("application/x-ipynb+json", lambda: export_notebook(table, charts=charts))}
    outputs = [{"name": "results." + kind, "mime": exporters[kind][0], "content_bytes": exporters[kind][1]()} for kind in exports]
    outputs.extend({"name": f"chart-{index + 1}.png", "mime": "image/png", "content_bytes": export_chart_png(chart)} for index, chart in enumerate(charts))
    metadata = {"inputs": [{"source_id": s.source_id, "sha256": s.sha256, "mime": s.mime} for s in table.sources],
                "transformations": list(table.recipe), "recipe": recipe, "profile": profile_table(table),
                "lineage": [[item.to_dict() for item in row.lineage] for row in table.rows], "charts": charts,
                "formula_status": "preserved_not_recalculated", "validator_manifest": {"state": "awaiting_artifact_validation"}}
    return {"outputs": outputs, "metadata": metadata}
