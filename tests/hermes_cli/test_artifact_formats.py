"""Complete-byte format checks and real approved publication/reopen boundaries."""
import base64
import hashlib
import io
import json
import struct
import wave
import zipfile
import zlib
from dataclasses import replace

import pytest

from agent.project_context import project_access
from agent.result_artifacts import ArtifactConflict, artifact_actor, read_project_artifact
from hermes_cli import projects_db as pdb
from hermes_cli.artifact_formats import (
    CSV_MIME, JSON_MIME, MARKDOWN_MIME, NOTEBOOK_MIME, PNG_MIME, SRT_MIME, TEXT_MIME,
    VTT_MIME, WAV_MIME, XLSX_MIME, artifact_format, supported_artifact_formats, validate_artifact,
)
from hermes_cli.artifact_store import (
    prepare_artifact, prepare_markdown, prepare_markdown_edit, publish_artifact, publish_markdown,
    read_artifact, read_artifact_recovery,
)
from hermes_state import SessionDB
from tests.hermes_cli.test_artifact_store import approve, artifact_runtime  # noqa: F401 - shared real fixture
from tools import capability_broker as broker


def _wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b"\x00\x00\x01\x00" * 16)
    return output.getvalue()


def _png_chunk(name, data):
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)


def _png(*, width=2, height=1, pixels=b"\x00\xff\x00\x00\x00\xff\x00", compressed=None):
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(pixels) if compressed is None else compressed)
            + _png_chunk(b"IEND", b""))


def _notebook():
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"id": "source-1", "cell_type": "markdown", "metadata": {}, "source": ["# Source\n", "Never render HTML"]},
        {"id": "calc-1", "cell_type": "code", "metadata": {}, "source": "1+1", "execution_count": 1,
         "outputs": [{"output_type": "execute_result", "execution_count": 1,
                      "metadata": {}, "data": {"text/plain": "2"}}]},
    ]}


def _xlsx():
    from decimal import Decimal
    from hermes_cli.domain_xlsx import XlsxCell, XlsxSheet, XlsxWorkbook, write_xlsx
    return write_xlsx(XlsxWorkbook((XlsxSheet("Data", (
        (XlsxCell("units"), XlsxCell("doubled")),
        (XlsxCell(Decimal("3")), XlsxCell(Decimal("6"), formula="A2*2")),
    )),)))


def _samples():
    return [
        (MARKDOWN_MIME, b"# Source\n<script>inert source</script>\n", "plain_text"),
        (TEXT_MIME, "Complete UTF-8 αβ\n".encode(), "plain_text"),
        (CSV_MIME, b'product,amount\n"alpha, beta",2\n=1+1,3\n', "plain_text"),
        (JSON_MIME, b'{"sources":[1,2],"valid":true}', "plain_text"),
        (NOTEBOOK_MIME, json.dumps(_notebook()).encode(), "plain_text"),
        (XLSX_MIME, _xlsx(), "download_only"),
        (WAV_MIME, _wav(), "download_only"),
        (PNG_MIME, _png(), "download_only"),
        (SRT_MIME, b"1\n00:00:00,000 --> 00:00:01,000\nSpeaker uncertain\n", "plain_text"),
        (VTT_MIME, b"WEBVTT\n\n00:00.000 --> 00:01.000\nSpeaker uncertain\n", "plain_text"),
    ]


@pytest.mark.parametrize("mime,payload,preview", _samples())
def test_complete_supported_format_receipts_describe_actual_bytes_and_limits(mime, payload, preview):
    receipt = validate_artifact(payload, mime)
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()
    assert receipt["size"] == len(payload)
    assert receipt["status"] == "passed" and receipt["limits"]
    assert receipt["preview_mode"] == preview
    assert receipt["visual_render"] == "not_performed"
    assert receipt["openability"] == "bounded_structure_validated"
    assert artifact_format(mime).mime == mime
    assert any(record["mime"] == mime and record["preview_mode"] == preview for record in supported_artifact_formats())


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("mime,payload,preview", _samples())
def test_real_approved_publish_reopens_every_complete_format(artifact_runtime, mime, payload, preview):
    runtime = artifact_runtime
    with runtime.scope() as run:
        proposal = prepare_artifact(run, project_id=runtime.project, request_id="format", content_bytes=payload, mime=mime)
        repeat = prepare_artifact(run, project_id=runtime.project, request_id="format", content_bytes=payload, mime=mime)
        assert repeat.public_record() == proposal.public_record()
        with pytest.raises(broker.CapabilityDenied, match="approved"):
            publish_artifact(run, proposal)
        approve(proposal)
        result = publish_artifact(run, proposal)
        assert publish_artifact(run, repeat) == result
        assert result["validation_status"] == "passed" and result["mime"] == mime
        assert len(runtime.db.list_effects(run.session_id, artifact_actor(run.context))) == 1
        reopened = SessionDB(runtime.home / "state.db")
        try:
            pieces, offset = [], 0
            while True:
                part = read_artifact(run.context, reopened, runtime.project, result["artifact_id"], offset=offset, limit=103)
                assert part["preview_mode"] == preview
                pieces.append(base64.b64decode(part["data_base64"]))
                if part["eof"]:
                    break
                offset = part["next_offset"]
            assert b"".join(pieces) == payload
            row = reopened.read_artifact_version(result["artifact_id"], result["version"],
                artifact_actor(run.context), access=project_access(run.context))
            assert row["metadata"]["validation"]["receipt_ref"] == validate_artifact(payload, mime)["receipt_ref"]
        finally:
            reopened.close()


@pytest.mark.parametrize("mime", [
    "application/octet-stream", "text/html", "image/svg+xml", "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain; charset=utf-8", "TEXT/PLAIN", "made/up", None, ["text/plain"],
])
def test_unknown_and_uncertified_formats_have_no_mime_escape(mime):
    with pytest.raises(ArtifactConflict, match="Unsupported artifact format"):
        validate_artifact(b"plausible bytes", mime)


@pytest.mark.parametrize("payload", [b"\xff", b"null\0", bytearray(b"text"), "text", b"a" * (8 * 1024 * 1024 + 1)])
def test_text_requires_complete_immutable_bounded_utf8(payload):
    with pytest.raises(ArtifactConflict):
        validate_artifact(payload, TEXT_MIME)


@pytest.mark.parametrize("payload", [
    b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'1e999', b'[] trailing',
    b'{"a":"\\ud800"}', b'[', b'[' * 66 + b'0' + b']' * 66,
    b'[' + b'0,' * 100000 + b'0]',
])
def test_json_rejects_ambiguous_nonfinite_truncated_or_unbounded_bytes(payload):
    with pytest.raises(ArtifactConflict):
        validate_artifact(payload, JSON_MIME)


@pytest.mark.parametrize("payload", [
    b'a,b\n1\n', b'a,b\n"unterminated,2\n', b'a\n' * 10002,
    (b'x,' * 128) + b'x\n', b'header\n' + b'a' * 65537,
    b'', b'\n',
])
def test_csv_rejects_malformed_and_unbounded_tables(payload):
    with pytest.raises(ArtifactConflict):
        validate_artifact(payload, CSV_MIME)


@pytest.mark.parametrize("change", [
    lambda book: book.update(nbformat_minor=6),
    lambda book: book.update(nbformat=True),
    lambda book: book["metadata"].update(kernelspec={"name": "python3"}),
    lambda book: book["metadata"].update(language_info={"name": []}),
    lambda book: book["cells"][0]["metadata"].update(tags=["duplicate", "duplicate"]),
    lambda book: book["cells"][1]["metadata"].update(collapsed="false"),
    lambda book: book["cells"][1].update(id="source-1"),
    lambda book: book["cells"][1].pop("execution_count"),
    lambda book: book["cells"][1].update(execution_count=True),
    lambda book: book["cells"][1].update(attachments={"image.png": {}}),
    lambda book: book["cells"][1]["outputs"][0]["data"].update({"text/html": "<script>run()</script>"}),
    lambda book: book["cells"][1].update(outputs=[{"output_type": "stream", "name": [], "text": "x"}]),
    lambda book: book.update(cells=book["cells"] * 501),
])
def test_notebooks_reject_malformed_active_and_unsupported_features(change):
    notebook = _notebook()
    change(notebook)
    with pytest.raises(ArtifactConflict):
        validate_artifact(json.dumps(notebook).encode(), NOTEBOOK_MIME)


@pytest.mark.parametrize("change", [
    lambda data: data[:-1],
    lambda data: data + b'trailing',
    lambda data: data[:20] + struct.pack('<H', 3) + data[22:],
    lambda data: data[:22] + struct.pack('<H', 8) + data[24:],
    lambda data: data[:28] + struct.pack('<I', 7) + data[32:],
    lambda data: data[:40] + struct.pack('<I', 999999) + data[44:],
    lambda data: data[:12] + b'JUNK' + data[16:],
])
def test_wav_rejects_truncated_misdeclared_and_unsupported_audio(change):
    with pytest.raises(ArtifactConflict):
        validate_artifact(change(_wav()), WAV_MIME)


def test_wav_properties_reopen_with_independent_standard_reader():
    data = _wav()
    receipt = validate_artifact(data, WAV_MIME)
    with wave.open(io.BytesIO(data), "rb") as audio:
        assert receipt["details"]["frames"] == audio.getnframes()
        assert receipt["details"]["sample_rate_hz"] == audio.getframerate()
        assert receipt["details"]["channels"] == audio.getnchannels()
        assert len(audio.readframes(audio.getnframes())) == audio.getnframes() * audio.getsampwidth()


@pytest.mark.parametrize("payload", [
    _png()[:-1], _png() + b'trailing', _png(width=4097),
    _png(pixels=b'\x05' + b'\0' * 6), _png(pixels=b'\0' * 8),
    _png(compressed=zlib.compress(b'\0' * 1000000)),
    _png(compressed=zlib.compress(b'\0' * 7) + zlib.compress(b'\0' * 7)),
    _png()[:29] + b'\xff' + _png()[30:],
])
def test_png_rejects_crc_size_dimension_filter_and_decompression_attacks(payload):
    with pytest.raises(ArtifactConflict):
        validate_artifact(payload, PNG_MIME)


@pytest.mark.parametrize("mime,payload", [
    (SRT_MIME, b'1\n00:00:01,000 --> 00:00:00,000\nWrong\n'),
    (SRT_MIME, b'2\n00:00:00,000 --> 00:00:01,000\nWrong\n'),
    (SRT_MIME, b'1\n00:61:00,000 --> 00:62:00,000\nWrong\n'),
    (SRT_MIME, b'1\n00:00:00,000 --> 00:00:02,000\nA\n\n2\n00:00:01,000 --> 00:00:03,000\nB'),
    (VTT_MIME, b'WEBVTT\n\nSTYLE\n::cue {color:red;}'),
    (VTT_MIME, b'WEBVTT\n\n00:00.000 --> 00:01.000 position:20%\nUnsupported'),
    (VTT_MIME, b'WEBVTT\n\n00:00.000 --> 00:01.000'),
    (VTT_MIME, b'WEBVTT -->\n\n00:00.000 --> 00:01.000\nText'),
])
def test_subtitle_timing_and_supported_feature_boundaries(mime, payload):
    with pytest.raises(ArtifactConflict):
        validate_artifact(payload, mime)


def _zip_with(name, data):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.parametrize("payload", [
    b'not a workbook', b'PK\x03\x04truncated', _zip_with('../escape', b'x'),
    _zip_with('xl/worksheets/sheet1.xml', b'a' * 2_000_000),
])
def test_xlsx_rejects_invalid_unsafe_and_zip_bomb_bytes(payload):
    with pytest.raises(ArtifactConflict):
        validate_artifact(payload, XLSX_MIME)


@pytest.mark.platforms("linux")
def test_format_revision_preserves_original_lineage_and_blocks_relabeling(artifact_runtime):
    runtime = artifact_runtime
    with runtime.scope() as run:
        proposal = prepare_artifact(run, project_id=runtime.project, request_id="source", content_bytes=b'{"value":1}', mime=JSON_MIME)
        approve(proposal)
        first = publish_artifact(run, proposal)
        derivative = prepare_artifact(run, project_id=runtime.project, request_id="derived", content_bytes=b"Summary", mime=TEXT_MIME,
            derived_from=[{"artifact_id": first["artifact_id"], "version": first["version"]}])
        approve(derivative)
        summary = publish_artifact(run, derivative)
        revision = prepare_artifact(run, project_id=runtime.project, request_id="revision", content_bytes=b'{"value":2}', mime=JSON_MIME,
            artifact_id=first["artifact_id"], parent_version=first["version"])
        approve(revision)
        publish_artifact(run, revision)
        assert read_project_artifact(run.context, runtime.db, runtime.project, first["artifact_id"], first["version"]) == b'{"value":1}'
        row = runtime.db.read_artifact_version(summary["artifact_id"], summary["version"], artifact_actor(run.context), access=project_access(run.context))
        assert row["derived_validity"] == "stale"
        with pytest.raises(ArtifactConflict, match="new derived artifact"):
            prepare_artifact(run, project_id=runtime.project, request_id="relabel", content_bytes=b'{"value":3}', mime=TEXT_MIME,
                artifact_id=first["artifact_id"], parent_version=first["version"])
        with pytest.raises(ArtifactConflict, match="Markdown"):
            prepare_markdown_edit(run, project_id=runtime.project, request_id="edit", artifact_id=first["artifact_id"],
                parent_version=first["version"], edits=[])
        with pytest.raises(ArtifactConflict, match="Markdown"):
            publish_markdown(run, revision)


@pytest.mark.platforms("linux")
def test_complete_revalidation_precedes_effect_intent_and_approval_consumption(artifact_runtime):
    runtime = artifact_runtime
    with runtime.scope() as run:
        proposal = prepare_artifact(run, project_id=runtime.project, request_id="audio", content_bytes=_wav(), mime=WAV_MIME)
        approve(proposal)
        for payload in (_wav()[:-1], _wav()[:-2] + b'\xff\xff'):
            with pytest.raises(ArtifactConflict):
                publish_artifact(run, replace(proposal, content_bytes=payload))
        assert not runtime.db.list_effects(run.session_id, artifact_actor(run.context))
        assert not (runtime.home / "runtime-artifacts").exists()
        assert runtime.db.get_effect_approval(proposal.approval_id, artifact_actor(run.context))["status"] == "approved"
        result = publish_artifact(run, proposal)
        with pdb.connect_closing() as conn:
            conn.execute("DELETE FROM project_grants WHERE project_id=?", (runtime.project,))
            conn.commit()
        with pytest.raises(PermissionError, match="grant"):
            read_artifact(run.context, runtime.db, runtime.project, result["artifact_id"])


@pytest.mark.platforms("linux")
def test_broker_direct_dispatch_cannot_bypass_complete_format_validation(artifact_runtime):
    runtime = artifact_runtime
    with runtime.scope() as run:
        proposal = prepare_artifact(run, project_id=runtime.project, request_id="json", content_bytes=b'{}', mime=JSON_MIME)
        approve(proposal)
        action = broker.project_artifact_action(proposal.scope)
        preview = broker.recover_approval_preview(proposal.approval_id, action)
        with pytest.raises(ArtifactConflict):
            broker.invoke_effect_dispatch("project_artifact_publish", run=run, input_ref=proposal.scope["descriptor"],
                payload=b'{', operation_id="bypass", intent_key="bypass", action=action, approval=preview)
        scope = proposal.scope
        scope["descriptor"]["mime"] = "text/html"
        with pytest.raises(ArtifactConflict, match="Unsupported"):
            broker.project_artifact_action(scope)
        assert not runtime.db.list_effects(run.session_id, artifact_actor(run.context))


@pytest.mark.platforms("linux")
def test_confirmed_binary_recovery_is_download_only_without_catalog_claim(artifact_runtime, monkeypatch):
    runtime = artifact_runtime
    with runtime.scope() as run:
        proposal = prepare_artifact(run, project_id=runtime.project, request_id="binary", content_bytes=_png(), mime=PNG_MIME)
        approve(proposal)
        with monkeypatch.context() as patch:
            def lost(*args, **kwargs):
                raise OSError("catalog unavailable")
            patch.setattr(runtime.db, "register_artifact_version", lost)
            with pytest.raises(OSError):
                publish_artifact(run, proposal)
        effect = runtime.db.list_effects(run.session_id, artifact_actor(run.context))[0]
        recovery = read_artifact_recovery(run.context, runtime.db, runtime.project, effect["effect_id"])
        assert recovery["publication_state"] == "published_uncommitted"
        assert recovery["preview_mode"] == "download_only"
        assert base64.b64decode(recovery["data_base64"]) == _png()
        published = publish_artifact(run, proposal)
        assert published["disposition"] == "canonical"
        assert len(runtime.db.list_effects(run.session_id, artifact_actor(run.context))) == 1


@pytest.mark.platforms("linux")
def test_markdown_preserves_existing_approval_contract_and_receipt(artifact_runtime):
    runtime = artifact_runtime
    with runtime.scope() as run:
        content = "# Existing\nImmutable\n"
        proposal = prepare_markdown(run, project_id=runtime.project, request_id="compat", content=content)
        expected = hashlib.sha256(b"be07.project-artifact-markdown.v1").hexdigest()
        assert broker.project_artifact_action(proposal.scope).contract_digest == expected
        assert proposal.scope["metadata"]["validation"]["receipt_ref"] == "markdown-utf8-sha256:" + hashlib.sha256(content.encode()).hexdigest()


def test_png_reopens_with_independent_image_decoder():
    from PIL import Image
    payload = _png()
    receipt = validate_artifact(payload, PNG_MIME)
    with Image.open(io.BytesIO(payload)) as image:
        image.load()
        assert image.size == (receipt["details"]["width"], receipt["details"]["height"])
        assert image.getpixel((0, 0)) == (255, 0, 0)
        assert image.getpixel((1, 0)) == (0, 255, 0)
