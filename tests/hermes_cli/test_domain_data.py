"""Real known-answer data transformations, immutable source traces and complete exports."""
import base64
import hashlib
import json
from dataclasses import replace
from decimal import Decimal, Inexact, ROUND_UP, localcontext
from pathlib import Path

import pytest

from hermes_cli.artifact_formats import validate_artifact
from hermes_cli.domain_data import (
    DataError, ManualMappingRequired, aggregate_table, build_data_package, chart_table,
    export_chart_png, export_csv, export_notebook, export_xlsx, ingest_csv, ingest_xlsx, join_tables,
)
from hermes_cli.domain_xlsx import XlsxCell, XlsxSheet, XlsxWorkbook, read_xlsx, write_xlsx

FIXTURES = Path(__file__).resolve().parents[2] / "hermes_cli" / "domain_fixtures"


def options(**changes):
    result = {"encoding": "utf-8", "delimiter": ",", "date_format": "YYYY-MM-DD", "currency": "USD",
              "null_values": [""], "units": {}, "duplicate_keys": "allow"}
    result.update(changes)
    return result


def orders(payload=None):
    return ingest_csv(payload or (FIXTURES / "data_v1_orders.csv").read_bytes(), source_id="orders:v1",
                      options=options(column_types={"amount": "currency", "day": "date"}, units={"amount": "USD"}))


def regions(payload=None):
    return ingest_csv(payload or (FIXTURES / "data_v1_regions.csv").read_bytes(), source_id="regions:v1", options=options())


def calculate(left, right):
    joined = join_tables(left, right, left_on=["region"], right_on=["region"], how="left",
                         cardinality="many_to_one", null_keys="reject")
    return aggregate_table(joined, group_by=["right.manager"], aggregations={"total": {"operation": "sum", "column": "amount"}}, nulls="reject")


def test_join_aggregate_changed_input_and_every_result_traces_to_source_rows():
    left, right = orders(), regions()
    result = calculate(left, right)
    assert result.records() == [{"right.manager": "Ada", "total": Decimal("15.00")}, {"right.manager": "Bo", "total": Decimal("20.00")}]
    assert {(ref.source_id, ref.row) for ref in result.rows[0].lineage} == {("orders:v1", 2), ("orders:v1", 4), ("regions:v1", 2)}
    assert all(ref.sha256 == next(s.sha256 for s in result.sources if s.source_id == ref.source_id) for row in result.rows for ref in row.lineage)
    changed_bytes = left.sources[0].content_bytes.replace(b"10.25", b"100.25")
    changed = calculate(orders(changed_bytes), right)
    assert changed.rows[0].values[1] - result.rows[0].values[1] == Decimal("90")
    assert changed.rows[1].values == result.rows[1].values
    assert changed.sources[0].sha256 != result.sources[0].sha256
    assert [step["operation"] for step in result.recipe] == ["ingest", "ingest", "join", "aggregate"]


def test_complete_workbook_csv_and_notebook_roundtrip_retain_recipe_and_originals():
    table = calculate(orders(), regions())
    csv_bytes, xlsx_bytes, notebook_bytes = export_csv(table), export_xlsx(table), export_notebook(table)
    for content, mime in [(csv_bytes, "text/csv"), (xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), (notebook_bytes, "application/x-ipynb+json")]:
        assert validate_artifact(content, mime)["status"] == "passed"
    reopened_csv = ingest_csv(csv_bytes, source_id="csv-export", options=options(column_types={"total": "decimal"}))
    reopened_xlsx = ingest_xlsx(xlsx_bytes, source_id="xlsx-export", options=options())
    assert reopened_csv.records() == reopened_xlsx.records() == table.records()
    content = json.loads(json.loads(notebook_bytes)["cells"][1]["source"][0])
    assert content["recipe"] == list(table.recipe)
    assert content["rows"] == [["Ada", "15.00"], ["Bo", "20.00"]]
    assert content["lineage"][0] == [item.to_dict() for item in table.rows[0].lineage]
    for original in content["inputs"]:
        raw = base64.b64decode(original["bytes_base64"])
        assert hashlib.sha256(raw).hexdigest() == original["sha256"]
        assert raw == next(source.content_bytes for source in table.sources if source.source_id == original["source_id"])


def test_manual_mapping_resolves_colliding_headers_without_guessing():
    data = b"Amount, amount ,Amount\n10,20,30\n"
    with pytest.raises(ManualMappingRequired):
        ingest_csv(data, source_id="ambiguous", options=options())
    with pytest.raises(ManualMappingRequired):
        ingest_csv(data, source_id="ambiguous", options=options(mapping={"net": "Amount"}))
    resolved = ingest_csv(data, source_id="ambiguous", options=options(mapping={"net": 0, "gross": 2}, column_types={"net": "decimal", "gross": "decimal"}))
    assert resolved.records() == [{"net": Decimal(10), "gross": Decimal(30)}]
    assert resolved.recipe[0]["resolved_mapping"] == {"net": 0, "gross": 2}


def test_explicit_locale_null_units_dates_duplicates_and_digest():
    data = b"id;amount;day\na;EUR 1.234,50;02/03/2026\na;NA;03/04/2026\n"
    opts = options(delimiter=";", date_format="DD/MM/YYYY", currency="EUR", null_values=["NA"],
                   units={"amount": "EUR"}, column_types={"amount": "currency", "day": "date"},
                   decimal_separator=",", grouping_separator=".", keys=["id"], duplicate_keys="reject")
    with pytest.raises(DataError, match="duplicate key"):
        ingest_csv(data, source_id="locale", options=opts)
    retained = ingest_csv(data, source_id="locale", options={**opts, "duplicate_keys": "keep_first"})
    assert retained.rows[0].values[1] == Decimal("1234.50")
    assert str(retained.rows[0].values[2]) == "2026-03-02"
    assert [ref.row for ref in retained.rows[0].lineage] == [2, 3]
    with pytest.raises(DataError, match="digest"):
        ingest_csv(data, source_id="locale", options=opts, expected_sha256="0" * 64)
    with pytest.raises(DataError, match="explicit"):
        ingest_csv(data, source_id="locale", options={})


def test_cardinality_nulls_unknown_units_and_missing_numeric_inputs_are_fail_closed():
    with pytest.raises(DataError, match="cardinality"):
        calculate(orders(), regions(b"region,manager\nnorth,Ada\nnorth,Other\n"))
    with pytest.raises(DataError, match="units"):
        calculate(orders(), replace(regions(), units=(("region", "geography"),)))
    missing = orders(b"order_id,region,amount,day\no1,north,,2026-01-01\n")
    with pytest.raises(DataError, match="null"):
        calculate(missing, regions())
    skipped = aggregate_table(missing, group_by=["region"], aggregations={"total": {"operation": "sum", "column": "amount"}}, nulls="skip")
    assert skipped.rows[0].values[1] is None


def formula_workbook():
    return write_xlsx(XlsxWorkbook((XlsxSheet("Data", (
        (XlsxCell("amount"), XlsxCell("double")),
        (XlsxCell(Decimal(5)), XlsxCell(Decimal(999), "A2*2")),
    )),)))


def test_formula_literals_and_stale_caches_survive_but_cannot_drive_calculations():
    data = formula_workbook()
    table = ingest_xlsx(data, source_id="formulas", options=options())
    assert table.rows[0].values[1].formula == "A2*2"
    assert table.rows[0].values[1].value == Decimal(999)
    reopened = read_xlsx(export_xlsx(table))
    assert reopened.sheets[0].rows[1][1] == table.rows[0].values[1]
    with pytest.raises(DataError, match="recalculation"):
        aggregate_table(table, group_by=[], aggregations={"total": {"column": "double", "operation": "sum"}}, nulls="reject")
    with pytest.raises(DataError, match="preserve"):
        export_csv(table)
    with pytest.raises(DataError, match="rewrite"):
        ingest_xlsx(data, source_id="formulas", options=options(mapping={"double": 1, "amount": 0}))
    notebook = json.loads(json.loads(export_notebook(table))["cells"][1]["source"][0])
    assert notebook["rows"][0][1] == {"formula": "A2*2", "cached_value": "999", "cache_status": "unverified_not_recalculated", "style": 0}


def test_complete_package_consumer_recipe_and_chart_values_are_deterministic():
    source = (FIXTURES / "data_v1_orders.csv").read_bytes()
    inputs = [{"source_id": "orders", "content_bytes": source, "mime": "text/csv",
               "expected_sha256": hashlib.sha256(source).hexdigest(),
               "options": options(column_types={"amount": "currency"}, units={"amount": "USD"})}]
    recipe = {"base": "orders", "aggregate": {"group_by": ["region"],
              "aggregations": {"total": {"operation": "sum", "column": "amount"}}, "nulls": "reject"},
              "exports": ["csv", "xlsx", "ipynb"], "charts": [{"kind": "bar", "category": "region", "value": "total"}]}
    first = build_data_package(inputs, recipe)
    assert first == build_data_package(inputs, recipe)
    assert first["metadata"]["charts"][0]["points"][0]["value"] == "15.00"
    assert first["metadata"]["charts"][0]["points"][0]["lineage"] == first["metadata"]["lineage"][0]
    for artifact in first["outputs"]:
        assert validate_artifact(artifact["content_bytes"], artifact["mime"])["status"] == "passed"
    line = chart_table(calculate(orders(), regions()), kind="line", category="right.manager", value="total")
    assert '<polyline' in line["svg_source"]
    assert validate_artifact(export_chart_png(line), "image/png")["status"] == "passed"
    assert export_chart_png(line) != first["outputs"][-1]["content_bytes"]


@pytest.mark.parametrize("data", [b"a,b\n1\n", b"a\n\xff\n", b'a\n"unterminated\n', b"a\n\0\n"])
def test_malformed_csv_is_not_guessed_or_repaired(data):
    with pytest.raises(DataError):
        ingest_csv(data, source_id="bad", options=options())


@pytest.mark.parametrize("value", ["=1+1", "+SUM(A1)", "@DDE", "-command", "\ttext", "\t=1"])
def test_csv_formula_injection_is_rejected_without_mutating_original_text(value):
    table = ingest_csv(("header\n" + value + "\n").encode(), source_id="untrusted", options=options())
    with pytest.raises(DataError, match="spreadsheet-like"):
        export_csv(table)
    assert read_xlsx(export_xlsx(table)).sheets[0].rows[1][0].value == value


def test_csv_header_hazards_refuse_and_typed_negative_numbers_remain_numbers():
    table = ingest_csv(b"amount\n-12.50\n", source_id="negative", options=options(column_types={"amount": "decimal"}))
    assert export_csv(table) == b"amount\n-12.50\n"
    with pytest.raises(DataError, match="spreadsheet-like"):
        export_csv(replace(table, columns=("=danger",)))


def test_caller_decimal_context_cannot_change_aggregations_or_chart_bytes():
    table = orders()
    args = {"group_by": ["region"], "aggregations": {"total": {"operation": "mean", "column": "amount"}}, "nulls": "reject"}
    expected = aggregate_table(table, **args)
    chart = chart_table(expected, kind="line", category="region", value="total")
    png = export_chart_png(chart)
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        actual = aggregate_table(table, **args)
        assert actual == expected
        assert chart_table(actual, kind="line", category="region", value="total") == chart
        assert export_chart_png(chart) == png
