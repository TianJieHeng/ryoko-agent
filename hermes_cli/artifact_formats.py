"""Finite, complete-byte artifact validators; no code execution or active previews.

These receipts certify a bounded structural subset, never visual fidelity,
calculation correctness, creative rights, or recording consent. Unsupported
formats/features fail closed rather than receiving an extension-based receipt.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import struct
import zlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable

from agent.result_artifacts import MAX_ARTIFACT_BYTES, ArtifactConflict

MARKDOWN_MIME = "text/markdown"
TEXT_MIME = "text/plain"
CSV_MIME = "text/csv"
JSON_MIME = "application/json"
NOTEBOOK_MIME = "application/x-ipynb+json"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
WAV_MIME = "audio/wav"
PNG_MIME = "image/png"
SRT_MIME = "application/x-subrip"
VTT_MIME = "text/vtt"
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100000
MAX_CSV_ROWS = 10000
MAX_CSV_COLUMNS = 128
MAX_CSV_FIELD = 65536
MAX_NOTEBOOK_CELLS = 1000
MAX_NOTEBOOK_OUTPUTS = 1000
MAX_SUBTITLE_CUES = 10000
MAX_MEDIA_SECONDS = 3600


def _require(condition, message):
    if not condition:
        raise ArtifactConflict(message)


def _utf8(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ArtifactConflict("Artifact text must be complete valid UTF-8") from error
    _require("\0" not in text, "Artifact text must not contain NUL")
    return text


def _text(data):
    text = _utf8(data)
    return {"encoding": "utf-8", "characters": len(text)}


def _csv(data):
    text = _utf8(data)
    count, width = 0, None
    try:
        for row in csv.reader(io.StringIO(text, newline=""), strict=True):
            _require(0 < len(row) <= MAX_CSV_COLUMNS, "CSV column count exceeds the bounded table subset")
            _require(all(len(cell) <= MAX_CSV_FIELD for cell in row), "CSV field exceeds the supported bound")
            width = len(row) if width is None else width
            _require(len(row) == width, "CSV rows must have a consistent column count")
            count += 1
            _require(count <= MAX_CSV_ROWS + 1, "CSV row count exceeds the supported bound")
    except csv.Error as error:
        raise ArtifactConflict("CSV bytes are malformed or exceed the field bound") from error
    _require(count > 0, "CSV must contain at least one row")
    return {"encoding": "utf-8", "rows": count, "columns": width,
            "formula_status": "literal_text_not_executed"}


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "JSON duplicate object keys are unsupported")
        result[key] = value
    return result


def _bad_constant(_value):
    raise ArtifactConflict("JSON must contain only finite standard values")


def _json_value(data):
    try:
        value = json.loads(_utf8(data), object_pairs_hook=_unique_pairs, parse_constant=_bad_constant)
    except (ValueError, RecursionError) as error:
        raise ArtifactConflict("Artifact must be complete finite bounded JSON: " + str(error)) from error
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        _require(depth <= MAX_JSON_DEPTH and count <= MAX_JSON_NODES, "JSON nesting or item count exceeds the supported bound")
        if isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            _require(not any(0xD800 <= ord(char) <= 0xDFFF for char in item), "JSON strings must contain Unicode scalar values")
        elif isinstance(item, float):
            _require(math.isfinite(item), "JSON numbers must be finite")
    return value


def _json(data):
    value = _json_value(data)
    return {"encoding": "utf-8", "root_type": type(value).__name__, "evaluation": "none"}


def _source(value):
    return isinstance(value, str) or (isinstance(value, list) and all(isinstance(line, str) for line in value))


def _execution_count(value):
    return value is None or (type(value) is int and 0 <= value < 2**31)


def _notebook_output(output):
    _require(isinstance(output, dict), "Notebook output must be an object")
    kind = output.get("output_type")
    validators = {
        "stream": lambda: (set(output) == {"output_type", "name", "text"}
                            and isinstance(output["name"], str) and output["name"] in {"stdout", "stderr"} and _source(output["text"])),
        "error": lambda: (set(output) == {"output_type", "ename", "evalue", "traceback"}
                           and isinstance(output["ename"], str) and isinstance(output["evalue"], str)
                           and isinstance(output["traceback"], list)
                           and all(isinstance(line, str) for line in output["traceback"])),
        "display_data": lambda: _notebook_display(output, False),
        "execute_result": lambda: _notebook_display(output, True),
    }
    check = validators.get(kind) if isinstance(kind, str) else None
    _require(check is not None and check(), "Notebook output is malformed or uses unsupported active/media output")


def _notebook_display(output, executed):
    keys = {"output_type", "data", "metadata"} | ({"execution_count"} if executed else set())
    return (set(output) == keys and isinstance(output["metadata"], dict)
            and isinstance(output["data"], dict) and set(output["data"]) == {"text/plain"}
            and _source(output["data"]["text/plain"])
            and (not executed or (type(output["execution_count"]) is int and _execution_count(output["execution_count"]))))


def _notebook_metadata(metadata, kind):
    # Admit only metadata whose standard nbformat types we independently check.
    common = {"tags", "name", "ryoko"}
    allowed = {"root": {"kernelspec", "language_info", "ryoko"},
               "markdown": common, "raw": common | {"format"},
               "code": common | {"collapsed", "scrolled"}}
    _require(isinstance(metadata, dict) and set(metadata) <= allowed[kind],
             "Notebook metadata uses unsupported features")
    if "kernelspec" in metadata:
        kernel = metadata["kernelspec"]
        _require(isinstance(kernel, dict) and {"name", "display_name"} <= set(kernel)
                 <= {"name", "display_name", "language"} and all(isinstance(v, str) for v in kernel.values()),
                 "Notebook kernelspec metadata is invalid")
    if "language_info" in metadata:
        language = metadata["language_info"]
        _require(isinstance(language, dict) and "name" in language and set(language) <= {"name", "version"}
                 and all(isinstance(v, str) for v in language.values()), "Notebook language metadata is invalid")
    if "tags" in metadata:
        tags = metadata["tags"]
        _require(isinstance(tags, list) and all(isinstance(tag, str) for tag in tags)
                 and len(set(tags)) == len(tags), "Notebook tags must be unique strings")
    for key in ("name", "format"):
        if key in metadata:
            _require(isinstance(metadata[key], str), "Notebook text metadata is invalid")
    if "collapsed" in metadata:
        _require(type(metadata["collapsed"]) is bool, "Notebook collapsed metadata must be boolean")
    if "scrolled" in metadata:
        _require(type(metadata["scrolled"]) is bool or metadata["scrolled"] == "auto",
                 "Notebook scrolled metadata is invalid")


def _notebook(data):
    notebook = _json_value(data)
    _require(isinstance(notebook, dict) and set(notebook) == {"nbformat", "nbformat_minor", "metadata", "cells"},
             "Notebook requires the bounded nbformat 4 structure")
    _require(type(notebook["nbformat"]) is int and notebook["nbformat"] == 4
             and type(notebook["nbformat_minor"]) is int and 0 <= notebook["nbformat_minor"] <= 5,
             "Only nbformat 4.0 through 4.5 are supported")
    _require(isinstance(notebook["metadata"], dict) and isinstance(notebook["cells"], list)
             and len(notebook["cells"]) <= MAX_NOTEBOOK_CELLS, "Notebook metadata/cell bound is invalid")
    _notebook_metadata(notebook["metadata"], "root")
    ids, outputs = set(), 0
    for cell in notebook["cells"]:
        _require(isinstance(cell, dict), "Notebook cell must be an object")
        kind = cell.get("cell_type")
        _require(isinstance(kind, str) and kind in {"raw", "markdown", "code"}, "Unsupported notebook cell type")
        required = {"cell_type", "metadata", "source"} | ({"execution_count", "outputs"} if kind == "code" else set())
        if notebook["nbformat_minor"] >= 5:
            required.add("id")
        _require(required <= set(cell) <= required | {"id"}, "Notebook cell is malformed or has unsupported attachments/features")
        _require(isinstance(cell["metadata"], dict) and _source(cell["source"]), "Notebook source/metadata is invalid")
        _notebook_metadata(cell["metadata"], kind)
        if "id" in cell:
            identifier = cell["id"]
            _require(isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier)
                     and identifier not in ids, "Notebook cell ids must be bounded and unique")
            ids.add(identifier)
        if kind == "code":
            _require(_execution_count(cell["execution_count"]) and isinstance(cell["outputs"], list),
                     "Notebook execution fields are invalid")
            outputs += len(cell["outputs"])
            _require(outputs <= MAX_NOTEBOOK_OUTPUTS, "Notebook outputs exceed the supported bound")
            for output in cell["outputs"]:
                _notebook_output(output)
    return {"nbformat": notebook["nbformat"], "nbformat_minor": notebook["nbformat_minor"],
            "cells": len(notebook["cells"]), "outputs": outputs, "execution": "not_executed",
            "output_validation": "structure_only_results_not_verified"}


def _xlsx(data):
    from hermes_cli.domain_xlsx import validate_xlsx
    try:
        return validate_xlsx(data)
    except ValueError as error:
        raise ArtifactConflict("XLSX is malformed or unsupported: " + str(error)) from error


def _wav(data):
    _require(len(data) >= 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE", "Expected a complete RIFF/WAVE file")
    _require(struct.unpack_from("<I", data, 4)[0] == len(data) - 8, "WAV RIFF size does not match complete bytes")
    position, chunks = 12, {}
    while position < len(data):
        _require(position + 8 <= len(data), "WAV chunk header is truncated")
        name, size = struct.unpack_from("<4sI", data, position)
        position += 8
        _require(name in {b"fmt ", b"data"} and name not in chunks, "WAV contains duplicate or unsupported chunks")
        _require(position + size + size % 2 <= len(data), "WAV chunk is truncated")
        _require(name != b"data" or b"fmt " in chunks, "WAV fmt chunk must precede sample data")
        chunks[name] = data[position:position + size]
        position += size + size % 2
    _require(set(chunks) == {b"fmt ", b"data"} and len(chunks[b"fmt "]) == 16, "WAV requires PCM fmt and data chunks")
    encoding, channels, rate, byte_rate, align, bits = struct.unpack("<HHIIHH", chunks[b"fmt "])
    _require(encoding == 1 and channels in (1, 2) and bits in (8, 16, 24, 32)
             and 8000 <= rate <= 192000, "Only bounded mono/stereo integer PCM WAV is supported")
    _require(align == channels * bits // 8 and byte_rate == rate * align, "WAV sample encoding fields disagree")
    size = len(chunks[b"data"])
    _require(size > 0 and size % align == 0, "WAV sample bytes are empty or truncated")
    frames = size // align
    _require(frames <= rate * MAX_MEDIA_SECONDS, "WAV duration exceeds the supported bound")
    return {"encoding": "pcm_integer", "channels": channels, "sample_rate_hz": rate,
            "bits_per_sample": bits, "frames": frames, "duration_seconds": frames / rate,
            "listening_check": "not_performed"}


def _png(data):
    """A small real PNG subset with bounded full decompression and CRC checks."""
    _require(data.startswith(b"\x89PNG\r\n\x1a\n"), "Expected a complete PNG signature")
    position, names, compressed, dimensions = 8, [], bytearray(), None
    while position < len(data):
        _require(len(names) < 4096, "PNG chunk count exceeds the supported bound")
        _require(position + 12 <= len(data), "PNG chunk header is truncated")
        size, name = struct.unpack_from(">I4s", data, position)
        position += 8
        _require(size <= MAX_ARTIFACT_BYTES and position + size + 4 <= len(data), "PNG chunk is truncated")
        chunk = data[position:position + size]
        crc = struct.unpack_from(">I", data, position + size)[0]
        _require(zlib.crc32(name + chunk) & 0xffffffff == crc, "PNG chunk CRC failed")
        _require(name in {b"IHDR", b"IDAT", b"IEND"}, "PNG ancillary/unknown chunks are outside the supported subset")
        if name == b"IHDR":
            _require(not names and size == 13, "PNG needs exactly one first IHDR chunk")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunk)
            _require(0 < width <= 4096 and 0 < height <= 4096 and width * height <= 4_000_000,
                     "PNG dimensions exceed the decompression bound")
            _require(depth == 8 and color in (2, 6) and compression == filtering == interlace == 0,
                     "Only noninterlaced 8-bit RGB/RGBA PNG is supported")
            dimensions = width, height, 3 if color == 2 else 4
        elif name == b"IDAT":
            _require(names and names[-1] in {b"IHDR", b"IDAT"}, "PNG IDAT chunks must follow IHDR contiguously")
            compressed.extend(chunk)
        else:
            _require(size == 0 and names and names[-1] == b"IDAT" and position + 4 == len(data),
                     "PNG requires one final empty IEND chunk")
        names.append(name)
        position += size + 4
    _require(names and names[-1] == b"IEND" and dimensions is not None, "PNG is missing complete image chunks")
    width, height, channels = dimensions
    expected = height * (1 + width * channels)
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(bytes(compressed), expected + 1)
    except zlib.error as error:
        raise ArtifactConflict("PNG compressed image data is invalid") from error
    _require(len(pixels) == expected and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,
             "PNG image data is truncated, oversized or has trailing compressed streams")
    _require(all(pixels[row * (1 + width * channels)] in range(5) for row in range(height)),
             "PNG scanline filter is invalid")
    return {"width": width, "height": height, "channels": channels, "bits_per_sample": 8,
            "interlaced": False, "pixel_check": "complete_crc_and_inflate", "visual_check": "not_performed"}


def _timestamp(value, separator, *, short=False):
    pattern = r"(?:(\d{2,3}):)?([0-5]\d):([0-5]\d)" + re.escape(separator) + r"(\d{3})"
    match = re.fullmatch(pattern, value)
    _require(match is not None and (short or match[1] is not None), "Subtitle timestamp is malformed")
    hours, minutes, seconds, milliseconds = match.groups()
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(milliseconds)


def _subtitles(data, *, webvtt):
    text = _utf8(data).replace("\r\n", "\n")
    _require("\r" not in text, "Subtitle line endings are unsupported")
    blocks = re.split(r"\n[ \t]*\n", text.strip())
    if webvtt:
        _require(blocks and "-->" not in blocks[0], "WebVTT header is malformed")
        _require(blocks and re.fullmatch(r"WEBVTT(?:[^\n]*)", blocks.pop(0)) is not None,
                 "WebVTT requires a standalone WEBVTT header")
    _require(0 < len(blocks) <= MAX_SUBTITLE_CUES, "Subtitle cue count exceeds the supported bound")
    last_end, ids = 0, set()
    for number, block in enumerate(blocks, 1):
        lines = block.split("\n")
        if webvtt and lines and " --> " in lines[0]:
            timing = lines.pop(0)
        else:
            _require(len(lines) >= 3, "Subtitle cue needs an identifier, timing and text")
            identifier, timing = lines.pop(0), lines.pop(0)
            _require(identifier not in ids and "-->" not in identifier
                     and not identifier.startswith(("NOTE", "STYLE", "REGION")), "Subtitle identifier/feature is unsupported")
            _require(webvtt or identifier == str(number), "SRT cue numbering must be sequential")
            ids.add(identifier)
        times = timing.split(" --> ")
        _require(len(times) == 2 and lines and any(lines), "Subtitle cue text/timing is missing")
        start, end = (_timestamp(value, "." if webvtt else ",", short=webvtt) for value in times)
        _require(last_end <= start < end <= MAX_MEDIA_SECONDS * 1000,
                 "Subtitle cues must be ordered, non-overlapping and within the duration bound")
        last_end = end
    return {"encoding": "utf-8", "cues": len(blocks), "end_seconds": last_end / 1000,
            "timing_check": "ordered_nonoverlapping", "synchronization": "not_verified"}


@dataclass(frozen=True)
class ArtifactFormat:
    name: str
    mime: str
    preview_mode: str
    validator: Callable[[bytes], dict]
    limits: tuple[str, ...]

    @property
    def contract(self):
        # Keep the deployed BE07 Markdown contract stable for outstanding approvals.
        return "be07.project-artifact-markdown.v1" if self.mime == MARKDOWN_MIME else f"be10.project-artifact-{self.name}.v1"


_FORMATS = MappingProxyType({item.mime: item for item in (
    ArtifactFormat("markdown", MARKDOWN_MIME, "plain_text", _text, ("UTF-8 source; no visual rendering",)),
    ArtifactFormat("text", TEXT_MIME, "plain_text", _text, ("UTF-8 text only",)),
    ArtifactFormat("csv", CSV_MIME, "plain_text", _csv, ("10000 data rows plus header, 128 columns, 65536 characters per field", "Formula text is preserved and never executed",)),
    ArtifactFormat("json", JSON_MIME, "plain_text", _json, ("Finite JSON, unique keys, depth 64 and 100000 nodes",)),
    ArtifactFormat("notebook", NOTEBOOK_MIME, "plain_text", _notebook, ("nbformat 4.0–4.5; 1000 cells and outputs; text-only outputs and finite metadata subset", "Code is not executed and results are not verified",)),
    ArtifactFormat("xlsx", XLSX_MIME, "download_only", _xlsx, ("Restricted flat table OOXML; no macros, external links, drawings or embedded objects", "Formulas preserved, cached values untrusted; no recalculation or visual rendering",)),
    ArtifactFormat("wav", WAV_MIME, "download_only", _wav, ("Integer PCM mono/stereo 8–32-bit, 8–192 kHz; fmt/data chunks only", "At most one hour; no listening, rights or consent check",)),
    ArtifactFormat("png", PNG_MIME, "download_only", _png, ("Noninterlaced RGB/RGBA8, at most 4096 pixels per axis and 4 million pixels", "IHDR/IDAT/IEND only; complete CRC/inflate check, no visual rendering",)),
    ArtifactFormat("srt", SRT_MIME, "plain_text", lambda data: _subtitles(data, webvtt=False), ("10000 sequential non-overlapping cues, at most one hour", "Synchronization and speaker identity are not verified",)),
    ArtifactFormat("vtt", VTT_MIME, "plain_text", lambda data: _subtitles(data, webvtt=True), ("10000 non-overlapping cues, no settings/styles/regions, at most one hour", "Synchronization and speaker identity are not verified",)),
)})


def artifact_format(mime):
    """Exact MIME lookup, with no arbitrary MIME/extension or active-viewer escape."""
    result = _FORMATS.get(mime) if isinstance(mime, str) else None
    _require(result is not None, "Unsupported artifact format; no certified complete-byte validator for this MIME")
    return result


def supported_artifact_formats():
    return [{"format": item.name, "mime": item.mime, "preview_mode": item.preview_mode,
             "max_bytes": MAX_ARTIFACT_BYTES, "limits": list(item.limits)} for item in _FORMATS.values()]


def validate_artifact(content_bytes, mime):
    """Inspect every byte independently of the producing adapter, without execution."""
    format_ = artifact_format(mime)
    _require(type(content_bytes) is bytes and len(content_bytes) <= MAX_ARTIFACT_BYTES,
             "Artifact bytes exceed the complete-file bound or are not immutable bytes")
    details = format_.validator(content_bytes)
    digest = hashlib.sha256(content_bytes).hexdigest()
    receipt_ref = ("markdown-utf8-sha256:" if mime == MARKDOWN_MIME else f"be10-{format_.name}-v1-sha256:") + digest
    return {"status": "passed", "receipt_ref": receipt_ref, "format": format_.name, "mime": mime,
            "sha256": digest, "size": len(content_bytes), "validator_version": 1,
            "preview_mode": format_.preview_mode, "openability": "bounded_structure_validated",
            "visual_render": "not_performed", "limits": list(format_.limits), "details": details}
