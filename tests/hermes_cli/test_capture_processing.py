"""Real canonical-store lifecycle: bounded local index and retained failure receipts."""
import base64

import pytest

from agent.project_context import project_access
from hermes_cli import capture_processing as processing, project_sources as sources
from hermes_state_runtime import RuntimeStoreError
from tests.hermes_cli.test_project_sources import source_runtime  # noqa: F401

pytestmark = pytest.mark.platforms("linux")


def test_bounded_unicode_index_search_is_read_only_and_preserves_source(source_runtime):
    fixture = source_runtime
    text = "# Garden\n" + "orchid 🌺 " * 9000 + "TAIL_SENTINEL"
    original = fixture.publish(text)
    captured = sources.create_capture(fixture.context, fixture.db, project_id=fixture.project, original_ref=original)
    result = processing.process_capture(fixture.context, fixture.db, captured["capture_id"], expected_extraction_sequence=0)
    assert result["processing"]["truncated"] and result["processing"]["indexed_characters"] < len(text)
    before = fixture.db._conn.total_changes
    found = processing.search_captures(fixture.context, fixture.db, fixture.project, query="orchd")
    assert found["matches"][0]["capture"]["original_ref"] == original
    assert processing.search_captures(fixture.context, fixture.db, fixture.project, query="TAIL_SENTINEL")["matches"] == []
    assert fixture.db._conn.total_changes == before
    data, offset = bytearray(), 0
    while True:
        chunk = sources.read_capture(fixture.context, fixture.db, captured["capture_id"], offset=offset)
        data.extend(base64.b64decode(chunk["data_base64"]))
        offset = chunk["next_offset"]
        if chunk["eof"]:
            break
    assert data.decode() == text


def test_processing_missing_original_records_failure_and_never_erases_capture(source_runtime):
    fixture = source_runtime
    original = fixture.publish()
    captured = sources.create_capture(fixture.context, fixture.db, project_id=fixture.project, original_ref=original,
                                      annotation="Retain provenance even when bytes are unavailable")
    row = fixture.db.read_artifact_version(original["artifact_id"], original["version"], fixture.actor,
                                           access=project_access(fixture.context))
    path = fixture.home / row["descriptor"]["locator"]
    # The original path is verified from the authoritative descriptor, never accepted from a caller.
    original_bytes = path.read_bytes()
    retained = path.with_name(path.name + ".retained")
    path.rename(retained)
    result = processing.process_capture(fixture.context, fixture.db, captured["capture_id"], expected_extraction_sequence=0)
    assert result["processing"]["failure_code"] == "source_bytes_unavailable"
    assert result["capture"]["original_ref"] == original and result["capture"]["annotation"] == captured["annotation"]
    assert not path.exists() and retained.read_bytes() == original_bytes
    retained.rename(path)
    restored = processing.process_capture(fixture.context, fixture.db, captured["capture_id"], expected_extraction_sequence=1)
    assert restored["processing"]["status"] == "indexed" and len(restored["capture"]["extractions"]) == 2
    with pytest.raises(RuntimeStoreError, match="changed"):
        processing.process_capture(fixture.context, fixture.db, captured["capture_id"], expected_extraction_sequence=1)


def test_schema43_reopen_adds_projection_tables_without_altering_originals(source_runtime):
    import sqlite3
    from hermes_state import SessionDB
    from hermes_state_common import SCHEMA_VERSION
    fixture = source_runtime
    original = fixture.publish()
    captured = sources.create_capture(fixture.context, fixture.db, project_id=fixture.project, original_ref=original,
                                      annotation="Prior schema capture")
    fixture.db.close()
    with sqlite3.connect(fixture.home / "state.db") as conn:
        for table in ("artifact_capture_indexes", "artifact_capture_consolidations", "artifact_capture_batches"):
            conn.execute("DROP TABLE " + table)
        conn.execute("UPDATE schema_version SET version=43")
    reopened = SessionDB(fixture.home / "state.db")
    try:
        assert reopened._conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
        inspected = processing.inspect_capture(fixture.context, reopened, captured["capture_id"])
        assert inspected["capture"]["original_ref"] == original
        assert inspected["capture"]["annotation"] == "Prior schema capture"
        assert inspected["processing"]["status"] == "not_indexed"
        assert processing.process_capture(fixture.context, reopened, captured["capture_id"],
                                          expected_extraction_sequence=0)["processing"]["status"] == "indexed"
    finally:
        reopened.close()
