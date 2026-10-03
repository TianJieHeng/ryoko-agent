"""BE10's bounded, offline rational-arithmetic tutor and Markdown lesson adapter.

Learner records are immutable project artifacts, never process-global history.
Only catalog bytes can resume a record; every recorded transition is replayed and
its arithmetic reverified. Correctness is about submitted answers, not inferred
ability, mastery, disability, or any other learner characteristic.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import time

from agent.result_artifacts import ArtifactConflict, read_project_artifact
from hermes_cli.artifact_store import _sections, _section, _locks
from hermes_cli.project_sources import source_authority

_FIXTURE = Path(__file__).with_name("data") / "rational_arithmetic_v1.json"
_STATE_MARKER = "<!-- education-state-v1 -->\n"
_MAX_EVENTS = 120
_REVISIT_DAYS = (1, 3, 7, 14)
_ANSWER = re.compile(r"[+-]?(?:\d{1,9}(?:/\d{1,9})?|\d{1,9}\.\d{1,9})\Z")


class EducationError(ValueError):
    """A bounded input, immutable state, or alignment requirement was not met."""


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _text(value, name, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise EducationError(f"{name} must be nonempty bounded plain text")
    return value


def _fraction(value):
    if not isinstance(value, str) or not _ANSWER.fullmatch(value.strip()):
        raise EducationError("Answer must be a bounded integer, fraction, or finite decimal")
    try:
        return Fraction(value.strip())
    except (ZeroDivisionError, ValueError) as exc:
        raise EducationError("Answer must have a nonzero denominator") from exc


def _calculate(node):
    if isinstance(node, str):
        return _fraction(node)
    left, right = _calculate(node["left"]), _calculate(node["right"])
    operations = {"add": lambda: left + right, "subtract": lambda: left - right,
                  "multiply": lambda: left * right, "divide": lambda: left / right}
    return operations[node["op"]]()


def _integer_verify(node):
    """A second implementation verifies fixture answers without Fraction arithmetic."""
    if isinstance(node, str):
        parts = node.split("/")
        return int(parts[0]), int(parts[1]) if len(parts) == 2 else 1
    a, b = _integer_verify(node["left"])
    c, d = _integer_verify(node["right"])
    operations = {"add": lambda: (a * d + c * b, b * d),
                  "subtract": lambda: (a * d - c * b, b * d),
                  "multiply": lambda: (a * c, b * d), "divide": lambda: (a * d, b * c)}
    n, denominator = operations[node["op"]]()
    if denominator == 0:
        raise EducationError("Fixture contains division by zero")
    divisor = math.gcd(n, denominator) * (-1 if denominator < 0 else 1)
    return n // divisor, denominator // divisor


def load_arithmetic_fixture():
    """Return a fresh verified versioned fixture; callers cannot mutate a cache."""
    raw = _FIXTURE.read_bytes()
    fixture = json.loads(raw)
    if fixture["schema_version"] != 1 or fixture["domain"] != "rational_arithmetic":
        raise EducationError("Unsupported education fixture")
    practice, transfer = fixture["practice"], fixture["transfer"]
    ids = [item["id"] for item in practice + transfer]
    if len(ids) != len(set(ids)) or not practice or not transfer:
        raise EducationError("Practice and held-out fixture IDs must be disjoint")
    for item in practice + transfer:
        expected = _fraction(item["answer"])
        if _calculate(item["expression"]) != expected or _integer_verify(item["expression"]) != (expected.numerator, expected.denominator):
            raise EducationError("Fixture answer failed independent rational verification")
        if type(item["difficulty"]) is not int or not 1 <= item["difficulty"] <= 3:
            raise EducationError("Fixture difficulty exceeds its supported bound")
    fixture["sha256"] = _sha(raw)
    return fixture


def _scope(context, db, project_id):
    from agent.project_context import authorize_project
    source_authority(context, db)
    authorize_project(context, project_id, "read")
    return {"project_id": project_id, "principal_id": context.identity.principal_id,
            "profile_id": context.identity.profile_id}


def _read_ref(context, db, project_id, ref):
    if not isinstance(ref, dict) or set(ref) - {"artifact_id", "version", "sha256"} or not {"artifact_id", "version"} <= set(ref):
        raise EducationError("An exact immutable artifact/version reference is required")
    if type(ref["version"]) is not int or ref["version"] < 1:
        raise EducationError("An immutable positive artifact version is required")
    raw = read_project_artifact(context, db, project_id, ref["artifact_id"], ref["version"])
    digest = _sha(raw)
    if "sha256" in ref and ref["sha256"] != digest:
        raise EducationError("Source digest differs from the reopened artifact")
    return {"artifact_id": ref["artifact_id"], "version": ref["version"], "sha256": digest}, raw


def _sources(context, db, project_id, refs):
    if not isinstance(refs, (list, tuple)) or len(refs) > 12:
        raise EducationError("At most twelve exact source artifacts are supported")
    result = []
    for ref in refs:
        verified, raw = _read_ref(context, db, project_id, ref)
        if verified not in result:
            result.append(verified)
    return result


def _load_record(context, db, project_id, prior_ref, kind, scope, fixture):
    ref, raw = _read_ref(context, db, project_id, prior_ref)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EducationError("Education state must be a complete UTF-8 Markdown artifact") from exc
    if text.count(_STATE_MARKER) != 1:
        raise EducationError("Artifact does not contain one education state record")
    suffix = text.split(_STATE_MARKER, 1)[1]
    try:
        state = json.loads(suffix)
    except ValueError as exc:
        raise EducationError("Education state must be complete JSON at the end of the artifact") from exc
    if not isinstance(state, dict) or state.get("kind") != kind or state.get("scope") != scope or state.get("fixture") != {"version": fixture["version"], "sha256": fixture["sha256"]}:
        raise EducationError("Education state kind, owner, project, or fixture version changed")
    return ref, text, state


def _fixture_record(fixture):
    return {"version": fixture["version"], "sha256": fixture["sha256"]}


def _initial_tutor(difficulty):
    if type(difficulty) is not int or not 1 <= difficulty <= 3:
        raise EducationError("Difficulty must be an integer from 1 to 3")
    return {"difficulty": difficulty, "active": {"exercise_id": "p1", "mode": "diagnostic", "hints": 0},
            "history": [], "revisits": [], "transfer_exposed": [], "assessment_epoch": 0}


def _next_practice(state, fixture):
    seen = {entry["exercise_id"] for entry in state["history"] if entry["mode"] != "transfer" and entry["epoch"] == state["assessment_epoch"]}
    candidates = [item for item in fixture["practice"] if item["difficulty"] == state["difficulty"] and item["id"] not in seen]
    return {"exercise_id": candidates[0]["id"], "mode": "practice", "hints": 0} if candidates else None


def _exercise(fixture, exercise_id, transfer=False):
    items = fixture["transfer"] if transfer else fixture["practice"]
    matches = [item for item in items if item["id"] == exercise_id]
    if not matches:
        raise EducationError("Exercise is outside the selected practice or transfer partition")
    return matches[0]


def _record_answer(state, fixture, action, timestamp, transfer=False):
    active = state["active"]
    if not active or (active["mode"] == "transfer") != transfer:
        raise EducationError("Answer requires the current exercise in the matching assessment mode")
    exercise = _exercise(fixture, active["exercise_id"], transfer)
    correct = _fraction(action["answer"]) == _fraction(exercise["answer"])
    before = state["difficulty"]
    if not transfer:
        state["difficulty"] = max(1, min(3, before + (1 if correct else -1)))
    state["history"].append({"exercise_id": exercise["id"], "mode": active["mode"], "answer": action["answer"].strip(),
        "correct": correct, "expected_answer": exercise["answer"], "hint_count": active["hints"],
        "timestamp": timestamp, "epoch": state["assessment_epoch"], "difficulty_before": before,
        "difficulty_after": state["difficulty"]})
    if active["mode"] == "revisit":
        state["revisits"] = [item for item in state["revisits"] if item["exercise_id"] != exercise["id"]]
    state["active"] = _next_practice(state, fixture)


def _hint(state, fixture, action, timestamp):
    active = state["active"]
    if not active or active["mode"] == "transfer":
        raise EducationError("Held-out transfer assessments do not provide practice hints")
    item = _exercise(fixture, active["exercise_id"])
    if active["hints"] >= len(item["hints"]):
        raise EducationError("All staged hints have already been revealed")
    active["hints"] += 1


def _difficulty(state, fixture, action, timestamp):
    value = action["difficulty"]
    if type(value) is not int or not 1 <= value <= 3:
        raise EducationError("Difficulty must be an integer from 1 to 3")
    if state["active"] and state["active"]["mode"] == "transfer":
        raise EducationError("Finish the transfer assessment before changing practice difficulty")
    state["difficulty"] = value
    state["active"] = _next_practice(state, fixture)


def _reset(state, fixture, action, timestamp):
    if action["confirmed"] is not True:
        raise EducationError("Reset assessment needs an explicit confirmed=true choice")
    state["assessment_epoch"] += 1
    state["difficulty"] = 1
    state["active"] = {"exercise_id": "p1", "mode": "diagnostic", "hints": 0}
    state["revisits"] = []


def _choose_revisit(state, fixture, action, timestamp):
    exercise_id, days = action["exercise_id"], action["days"]
    _exercise(fixture, exercise_id)
    if type(days) is not int or days not in _REVISIT_DAYS:
        raise EducationError("Choose a revisit after 1, 3, 7, or 14 days")
    if not any(item["exercise_id"] == exercise_id and item["mode"] != "transfer" for item in state["history"]):
        raise EducationError("Only an exercise in learner-visible practice history can be revisited")
    state["revisits"] = [item for item in state["revisits"] if item["exercise_id"] != exercise_id]
    state["revisits"].append({"exercise_id": exercise_id, "days": days, "chosen_at": timestamp,
                              "due_at": timestamp + days * 86400})


def _start_revisit(state, fixture, action, timestamp):
    if state["active"] and state["active"]["mode"] == "transfer":
        raise EducationError("Finish the transfer assessment before revisiting practice")
    matching = [item for item in state["revisits"] if item["exercise_id"] == action["exercise_id"] and item["due_at"] <= timestamp]
    if not matching:
        raise EducationError("The learner has not selected a due revisit for this exercise")
    state["active"] = {"exercise_id": action["exercise_id"], "mode": "revisit", "hints": 0}


def _start_transfer(state, fixture, action, timestamp):
    if not any(item["mode"] in {"practice", "diagnostic"} for item in state["history"]):
        raise EducationError("Complete a practice answer before requesting held-out transfer")
    available = [item for item in fixture["transfer"] if item["id"] not in state["transfer_exposed"]]
    if not available:
        raise EducationError("The held-out transfer fixture was already exposed; repeating it is not held-out evaluation")
    exercise_id = available[0]["id"]
    state["transfer_exposed"].append(exercise_id)
    state["active"] = {"exercise_id": exercise_id, "mode": "transfer", "hints": 0}


_ACTIONS = {
    "hint": (set(), _hint), "answer": ({"answer"}, _record_answer),
    "set_difficulty": ({"difficulty"}, _difficulty), "reset": ({"confirmed"}, _reset),
    "choose_revisit": ({"exercise_id", "days"}, _choose_revisit),
    "start_revisit": ({"exercise_id"}, _start_revisit), "start_transfer": (set(), _start_transfer),
    "answer_transfer": ({"answer"}, lambda s, f, a, t: _record_answer(s, f, a, t, True)),
}


def _transition(state, fixture, action, timestamp):
    if not isinstance(action, dict) or not isinstance(action.get("type"), str) or action["type"] not in _ACTIONS:
        raise EducationError("Unsupported tutor action")
    fields, handler = _ACTIONS[action["type"]]
    if set(action) != fields | {"type"}:
        raise EducationError("Tutor action fields must exactly match its action type")
    handler(state, fixture, action, timestamp)


def _timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise EducationError("Tutor event time must be a finite UTC timestamp")
    return float(value)


def _replay(record, fixture):
    try:
        if set(record) != {"kind", "scope", "fixture", "goal", "initial_difficulty", "events", "state", "sources"}:
            raise EducationError("Tutor record contains unsupported fields")
        _text(record["goal"], "Learning goal")
        state = _initial_tutor(record["initial_difficulty"])
        events = record["events"]
        if not isinstance(events, list) or len(events) > _MAX_EVENTS:
            raise EducationError("Tutor history exceeds the bounded event limit")
        previous = -1.0
        for event in events:
            if not isinstance(event, dict) or set(event) != {"action", "timestamp"}:
                raise EducationError("Tutor events need exact action and timestamp fields")
            stamp = _timestamp(event["timestamp"])
            if stamp < previous:
                raise EducationError("Tutor events must be chronological")
            _transition(state, fixture, event["action"], stamp)
            previous = stamp
        if _json(state) != _json(record["state"]):
            raise EducationError("Stored tutor correctness or adaptation differs from independent event replay")
        return state
    except (KeyError, TypeError) as exc:
        raise EducationError("Tutor state is incomplete or has invalid field types") from exc


def _source_lines(sources, fixture):
    lines = [f"Curated exact-arithmetic fixture {fixture['version']}; SHA-256 {fixture['sha256']}",
             "All numeric answers checked with Fraction and independent integer cross-products."]
    lines += [f"- Artifact {ref['artifact_id']}, immutable version {ref['version']}; SHA-256 {ref['sha256']} (reopened as authorized context; not authority for arithmetic answers)" for ref in sources]
    return "\n".join(lines) + "\n"


def _tutor_markdown(record, fixture):
    state = record["state"]
    lines = ["# Rational arithmetic practice", record["goal"], "", "# Learner controls",
        f"Practice difficulty: {state['difficulty']} of 3. Assessment epoch: {state['assessment_epoch']}.",
        "Choose difficulty 1–3, reveal one hint at a time, or explicitly reset the assessment.",
        "Reset retains visible response history and transfer exposure; it clears the current assessment and revisit choices.",
        "Revisit choices: 1, 3, 7, or 14 days. These are saved choices, not scheduled notifications.",
        "Held-out status is scoped to this learner artifact. A new artifact does not establish that a learner has never seen the transfer exercise.",
        "Only submitted practice answer correctness changes difficulty by at most one level. No learner traits or mastery are inferred.",
        "", "# Current exercise"]
    if state["active"]:
        active = state["active"]
        exercise = _exercise(fixture, active["exercise_id"], active["mode"] == "transfer")
        lines.extend([f"{exercise['id']} ({active['mode']}): {exercise['prompt']}", "Answer criterion: exact numerical equivalence; an integer, fraction or finite decimal is accepted."])
        if active["mode"] != "transfer":
            lines += [f"Hint {i + 1}: {hint}" for i, hint in enumerate(exercise["hints"][:active["hints"]])]
    else:
        lines.append("No unseen practice remains at this difficulty. Choose another difficulty, a due revisit, or the separate transfer assessment.")
    lines.extend(["", "# Exercise history", "Responses below are learner submissions; correctness is deterministically checked, not evidence of general mastery."])
    lines += [f"- {entry['exercise_id']} ({entry['mode']}, epoch {entry['epoch']}): submitted {entry['answer']}; {'correct' if entry['correct'] else 'incorrect'}; verified answer {entry['expected_answer']}; hints {entry['hint_count']}; practice difficulty {entry['difficulty_before']} → {entry['difficulty_after']}; UTC epoch {entry['timestamp']}" for entry in state["history"]]
    if not state["history"]:
        lines.append("No answers submitted yet. Begin with the short diagnostic above.")
    lines.extend(["", "# Chosen spaced revisits"])
    lines += [f"- {item['exercise_id']}: after {item['days']} days; due UTC epoch {item['due_at']}" for item in state["revisits"]]
    if not state["revisits"]:
        lines.append("None selected.")
    lines.extend(["", "# Saved next step", "Complete the current exercise, or use the explicit learner controls. Transfer assessment has no hints and never affects practice selection or difficulty.",
                  "", "# Source provenance", _source_lines(record["sources"], fixture), "# Reopenable learner record",
                  "The following complete record is checked against catalog bytes and independently replayed when reopened.", _STATE_MARKER + _json(record)])
    return "\n".join(lines).encode("utf-8")


def _output(kind, content, record, fixture, prior_ref=None, *, locked_sections=(), changed_sections=()):
    return {"content_bytes": content, "metadata": {"kind": kind, "mime": "text/markdown", "extension": ".md",
        "sha256": _sha(content), "size": len(content), "fixture": _fixture_record(fixture),
        "scope": record["scope"], "source_refs": record["sources"], "prior_ref": prior_ref,
        "inputs": record["sources"] + ([prior_ref] if prior_ref else []),
        "transformations": ["verified_curated_rational_arithmetic", "replay_learner_events" if kind == "tutor" else "objective_assessment_alignment"],
        "validator_manifest": {"status": "passed", "format": "markdown_utf8", "answer_verifiers": ["fractions.Fraction", "integer_cross_products"],
            "alignment_scope": "curated_objective_exercise_answer_key_and_timing" if kind == "educator" else "not_applicable",
            "instructor_edits": "preserved_not_fact_checked" if kind == "educator" else "not_applicable",
            "history_evidence": "learner_submissions_replayed_not_observed_behavior" if kind == "tutor" else "not_applicable",
            "state_replay": "passed" if kind == "tutor" else "not_applicable", "alignment": "passed" if kind == "educator" else "not_applicable",
            "render": "not_supported", "exports": ["markdown"], "docx_pptx": "not_supported"},
        "locked_sections": list(locked_sections), "changed_sections": list(changed_sections),
        "state": record["state"] if kind == "tutor" else record["lesson"]}}


def build_tutor_package(context, db, *, project_id, goal=None, difficulty=1, source_refs=(), prior_ref=None, action=None, now=None):
    """Build complete bytes. Persistence requires the separate exact BE07 approval."""
    scope, fixture = _scope(context, db, project_id), load_arithmetic_fixture()
    if prior_ref is not None:
        prior_ref, baseline, record = _load_record(context, db, project_id, prior_ref, "tutor", scope, fixture)
        _replay(record, fixture)
        if baseline.encode("utf-8") != _tutor_markdown(record, fixture):
            raise EducationError("Visible tutor history differs from its independently replayed record")
        if goal is not None and goal != record["goal"]:
            raise EducationError("An existing learner record has a fixed goal; start a new artifact for another goal")
        if source_refs and _sources(context, db, project_id, source_refs) != record["sources"]:
            raise EducationError("A continuation cannot silently replace its original source context")
        record["sources"] = _sources(context, db, project_id, record["sources"])
    else:
        record = {"kind": "tutor", "scope": scope, "fixture": _fixture_record(fixture),
                  "goal": _text(goal or "Practice exact fraction arithmetic", "Learning goal"), "initial_difficulty": difficulty,
                  "events": [], "state": _initial_tutor(difficulty), "sources": _sources(context, db, project_id, source_refs)}
    if action is not None:
        if len(record["events"]) >= _MAX_EVENTS:
            raise EducationError("This bounded learner artifact is full; retain it and start a new artifact")
        stamp = _timestamp(time.time() if now is None else now)
        if record["events"] and stamp < record["events"][-1]["timestamp"]:
            raise EducationError("Tutor actions cannot precede the previous recorded event")
        _transition(record["state"], fixture, action, stamp)
        record["events"].append({"action": deepcopy(action), "timestamp": stamp})
    _replay(record, fixture)
    return _output("tutor", _tutor_markdown(record, fixture), record, fixture, prior_ref)


def _lesson_anchors(objective_id):
    return [f"Objective {objective_id}", f"Teaching material {objective_id}", f"Activity {objective_id}",
            f"Exercise {objective_id}", f"Answer key {objective_id}"]


def _new_lesson(fixture, audience, duration_minutes, objective_ids, instructor_notes):
    selected = objective_ids if objective_ids is not None else [item["id"] for item in fixture["objectives"]]
    known = {item["id"]: item for item in fixture["objectives"]}
    if not isinstance(selected, (list, tuple)) or not 1 <= len(selected) <= 3 or any(not isinstance(key, str) for key in selected) or len(set(selected)) != len(selected) or any(key not in known for key in selected):
        raise EducationError("Choose one to three distinct curated objective IDs")
    if type(duration_minutes) is not int or not 12 <= duration_minutes <= 120:
        raise EducationError("Lesson duration must be an integer from 12 to 120 minutes")
    remaining = duration_minutes - 6
    minutes, extra = divmod(remaining, len(selected))
    return {"audience": _text(audience, "Audience"), "duration_minutes": duration_minutes,
        "introduction_minutes": 3, "exit_minutes": 3,
        "instructor_notes": _text(instructor_notes, "Instructor notes", 4000),
        "objectives": [{"objective_id": key, "exercise_id": known[key]["exercise_id"],
                        "activity_minutes": minutes + (index < extra)} for index, key in enumerate(selected)]}


def _educator_sections(record, fixture):
    lesson = record["lesson"]
    known = {item["id"]: item for item in fixture["objectives"]}
    sections = {"Lesson overview": f"Audience: {lesson['audience']}\nDuration: {lesson['duration_minutes']} minutes\n\nPrerequisites:\n" +
        "\n".join(f"- {item}" for item in fixture["prerequisites"]) +
        "\n\nIntroduction (3 minutes): ask learners to explain numerator and denominator using equal parts. Check whole-number multiplication before beginning.\n"}
    coverage = ["Each objective below has teaching material, a timed activity, a curated exercise, and a verified answer key.",
                "Section references are Markdown heading anchors; no slide deck or rendered document is claimed."]
    for item in lesson["objectives"]:
        objective_id = item["objective_id"]
        objective, exercise = known[objective_id], _exercise(fixture, item["exercise_id"])
        anchors = _lesson_anchors(objective_id)
        sections[anchors[0]] = objective["text"] + "\n"
        sections[anchors[1]] = objective["teaching"] + "\n\nModel the method with the exercise below, let learners explain each step, then check their numerical answer against the separate key.\n"
        sections[anchors[2]] = f"Duration: {item['activity_minutes']} minutes\nSequence: teacher models the rule; learners work individually; pairs compare equivalent forms; instructor checks the answer key and discusses the first differing step.\nFormative check: ask why the denominator or reciprocal operation is valid, then assess the exact final value.\n"
        sections[anchors[3]] = f"Exercise ID: {exercise['id']}\nQuestion: {exercise['prompt']}\nResponse criterion: an exactly equivalent integer, fraction, or finite decimal.\n"
        sections[anchors[4]] = f"Exercise ID: {exercise['id']}\nAnswer: {exercise['answer']}\nVerification: exact Fraction evaluation and independent integer cross-products agree.\nScoring: 1 point for exact numerical equivalence; 0 points otherwise. Reasoning is discussed separately and is not automatically graded.\n"
        coverage.append(f"- {objective_id} → section [{anchors[1]}](#{anchors[1].lower().replace(' ', '-')}) → {exercise['id']} in [{anchors[3]}](#{anchors[3].lower().replace(' ', '-')}) → [{anchors[4]}](#{anchors[4].lower().replace(' ', '-')}); {item['activity_minutes']} minutes")
    coverage += [f"Pacing total: 3-minute introduction + {sum(item['activity_minutes'] for item in lesson['objectives'])}-minute objective activities + 3-minute exit reflection = {lesson['duration_minutes']} minutes.",
                 "Exit reflection (3 minutes): learners explain one rule in their own words and compare their practice response with the key. This is formative reflection, not the tutor's held-out transfer assessment."]
    sections["Coverage and pacing"] = "\n".join(coverage) + "\n"
    sections["Instructor notes"] = lesson["instructor_notes"] + "\n"
    sections["Source provenance"] = _source_lines(record["sources"], fixture)
    sections["Reopenable lesson record"] = "Structured coverage is validated against the actual exercise and answer-key sections.\n" + _STATE_MARKER + _json(record)
    return {anchor: f"# {anchor}\n{body}" + ("\n" if anchor != "Reopenable lesson record" else "") for anchor, body in sections.items()}


def _validate_lesson(record, content, fixture):
    try:
        if set(record) != {"kind", "scope", "fixture", "sources", "lesson"}:
            raise EducationError("Lesson record contains unsupported fields")
        lesson = record["lesson"]
        if set(lesson) != {"audience", "duration_minutes", "introduction_minutes", "exit_minutes", "instructor_notes", "objectives"}:
            raise EducationError("Lesson state contains unsupported fields")
        known = {item["id"]: item for item in fixture["objectives"]}
        _text(lesson["audience"], "Audience")
        _text(lesson["instructor_notes"], "Instructor notes", 4000)
        objectives = lesson["objectives"]
        if not isinstance(objectives, list) or not 1 <= len(objectives) <= 3:
            raise EducationError("Lesson must cover one to three objectives")
        ids = [item["objective_id"] for item in objectives]
        if len(set(ids)) != len(ids) or any(key not in known for key in ids):
            raise EducationError("Lesson objectives must be distinct curated objectives")
        if lesson["introduction_minutes"] != 3 or lesson["exit_minutes"] != 3:
            raise EducationError("The curated lesson requires introduction and exit reflection")
        minutes = [item["activity_minutes"] for item in objectives]
        if any(type(value) is not int or not 2 <= value <= 114 for value in minutes):
            raise EducationError("Every lesson activity needs a bounded integer duration")
        if type(lesson["duration_minutes"]) is not int or lesson["duration_minutes"] != sum(minutes) + 6 or not 12 <= lesson["duration_minutes"] <= 120:
            raise EducationError("Lesson activity durations must reconcile to the total")
        sections = _sections(content)
        for item in objectives:
            if set(item) != {"objective_id", "exercise_id", "activity_minutes"}:
                raise EducationError("Lesson objective fields are invalid")
            exercise = _exercise(fixture, item["exercise_id"])
            if exercise["objective_id"] != item["objective_id"]:
                raise EducationError("Assessment does not align with its objective")
            anchors = _lesson_anchors(item["objective_id"])
            for anchor in anchors:
                _section(sections, anchor)
            if known[item["objective_id"]]["text"] not in _section(sections, anchors[0])[2]:
                raise EducationError("Actual objective no longer states its curated assessment goal")
            actual = _section(sections, anchors[3])[2]
            key = _section(sections, anchors[4])[2]
            answers = re.findall(r"^Answer: (.+)$", key, re.MULTILINE)
            if len(answers) != 1 or _fraction(answers[0]) != _calculate(exercise["expression"]):
                raise EducationError("The actual answer-key section fails exact arithmetic verification")
            if f"Exercise ID: {exercise['id']}\n" not in actual or f"Question: {exercise['prompt']}\n" not in actual or f"Exercise ID: {exercise['id']}\n" not in key:
                raise EducationError("Actual exercise and answer-key references disagree with lesson coverage")
            if f"Duration: {item['activity_minutes']} minutes\n" not in _section(sections, anchors[2])[2]:
                raise EducationError("Actual activity duration differs from lesson pacing")
        for anchor in ("Lesson overview", "Coverage and pacing", "Instructor notes", "Source provenance", "Reopenable lesson record"):
            _section(sections, anchor)
        expected = _educator_sections(record, fixture)
        coverage = _section(sections, "Coverage and pacing")[2]
        for line in expected["Coverage and pacing"].splitlines():
            if (line.startswith("- ") or line.startswith("Pacing total:")) and line not in coverage.splitlines():
                raise EducationError("Actual objective coverage or pacing references differ from the verified lesson")
        if f"Duration: {lesson['duration_minutes']} minutes\n" not in _section(sections, "Lesson overview")[2]:
            raise EducationError("Actual lesson overview duration differs from the activity total")
    except (KeyError, TypeError) as exc:
        raise EducationError("Lesson state is incomplete or has invalid field types") from exc


def build_educator_package(context, db, *, project_id, audience=None, duration_minutes=None,
        source_refs=(), objective_ids=None, instructor_notes="Instructor: adapt discussion to your group; preserve these notes in revisions.",
        prior_ref=None, changed_objective_ids=(), objective_updates=None):
    """Complete Markdown with dependency-only revisions and inherited BE07 locks."""
    from agent.project_context import project_access
    from agent.result_artifacts import artifact_actor
    scope, fixture = _scope(context, db, project_id), load_arithmetic_fixture()
    inherited_locks, baseline, changed = [], None, []
    if prior_ref is not None:
        prior_ref, baseline, record = _load_record(context, db, project_id, prior_ref, "educator", scope, fixture)
        _validate_lesson(record, baseline, fixture)
        if audience is not None and audience != record["lesson"]["audience"]:
            raise EducationError("Audience changes require a new lesson package")
        if objective_ids is not None and list(objective_ids) != [item["objective_id"] for item in record["lesson"]["objectives"]]:
            raise EducationError("Changing the objective set requires a new lesson package")
        if source_refs and _sources(context, db, project_id, source_refs) != record["sources"]:
            raise EducationError("A targeted revision cannot replace its source context")
        record["sources"] = _sources(context, db, project_id, record["sources"])
        row = db.read_artifact_version(prior_ref["artifact_id"], prior_ref["version"], artifact_actor(context), access=project_access(context))
        inherited_locks = row["metadata"]["locked_sections"]
        updates = objective_updates or {}
        if not isinstance(updates, dict) or len(updates) > 3:
            raise EducationError("Lesson updates must identify at most three objectives")
        if not isinstance(changed_objective_ids, (list, tuple)) or len(changed_objective_ids) > 3 or any(not isinstance(key, str) for key in changed_objective_ids):
            raise EducationError("Changed objectives must be a bounded list of objective IDs")
        if changed_objective_ids and set(changed_objective_ids) != set(updates):
            raise EducationError("Declared changed objectives must exactly match the requested updates")
        objectives = {item["objective_id"]: item for item in record["lesson"]["objectives"]}
        for key, delta in updates.items():
            if key not in objectives or not isinstance(delta, dict) or not delta or set(delta) - {"exercise_id", "activity_minutes"}:
                raise EducationError("Only exercise selection and activity pacing are supported targeted revisions")
            if "activity_minutes" in delta and (type(delta["activity_minutes"]) is not int or not 2 <= delta["activity_minutes"] <= 114):
                raise EducationError("Every lesson activity needs a bounded integer duration")
            objectives[key].update(delta)
            if "exercise_id" in delta:
                changed.extend([f"Exercise {key}", f"Answer key {key}"])
            if "activity_minutes" in delta:
                changed.append(f"Activity {key}")
        record["lesson"]["duration_minutes"] = sum(item["activity_minutes"] for item in objectives.values()) + 6
        if duration_minutes is not None and duration_minutes != record["lesson"]["duration_minutes"]:
            raise EducationError("Explicit total duration must match the revised activities")
        if updates:
            if any("activity_minutes" in delta for delta in updates.values()):
                changed.append("Lesson overview")
            changed += ["Coverage and pacing", "Reopenable lesson record"]
    else:
        if objective_updates or changed_objective_ids:
            raise EducationError("Targeted updates require an immutable prior lesson artifact")
        lesson = _new_lesson(fixture, audience or "Learners who know whole-number arithmetic", 30 if duration_minutes is None else duration_minutes, objective_ids, instructor_notes)
        record = {"kind": "educator", "scope": scope, "fixture": _fixture_record(fixture), "sources": _sources(context, db, project_id, source_refs), "lesson": lesson}
    generated = _educator_sections(record, fixture)
    if baseline is None:
        content = "".join(generated.values())
    elif changed:
        content = baseline
        sections = _sections(baseline)
        edits = sorted([(*_section(sections, anchor)[:2], generated[anchor]) for anchor in changed], reverse=True)
        for start, end, replacement in edits:
            content = content[:start] + replacement + content[end:]
    else:
        content = baseline
    _validate_lesson(record, content, fixture)
    requested_locks = ["Instructor notes"] if baseline is None else []
    locks = _locks(content, requested_locks, inherited_locks)
    return _output("educator", content.encode("utf-8"), record, fixture, prior_ref,
                   locked_sections=[item["anchor"] for item in locks], changed_sections=changed)


def prepare_education_package(run, *, project_id, request_id, package):
    """Bridge to BE07; this only prepares an exact approval, never self-approves."""
    from hermes_cli.artifact_store import prepare_markdown
    metadata = package["metadata"]
    content = package["content_bytes"]
    if not isinstance(content, bytes) or _sha(content) != metadata["sha256"] or len(content) != metadata["size"]:
        raise EducationError("Education package bytes changed after validation")
    if metadata["scope"] != _scope(run.context, run.db, project_id):
        raise EducationError("Education package belongs to another owner or project")
    fixture = load_arithmetic_fixture()
    text = content.decode("utf-8")
    if text.count(_STATE_MARKER) != 1:
        raise EducationError("Package must contain one complete education record")
    record = json.loads(text.split(_STATE_MARKER, 1)[1])
    if record["fixture"] != _fixture_record(fixture) or record["scope"] != metadata["scope"]:
        raise EducationError("Education fixture or ownership changed")
    validators = {"tutor": lambda: _replay(record, fixture), "educator": lambda: _validate_lesson(record, text, fixture)}
    if record.get("kind") not in validators or record["kind"] != metadata["kind"]:
        raise EducationError("Unsupported education package kind")
    validators[record["kind"]]()
    if record["kind"] == "tutor" and content != _tutor_markdown(record, fixture):
        raise EducationError("Visible tutor history differs from its independently replayed record")
    sources = _sources(run.context, run.db, project_id, record["sources"])
    prior = metadata["prior_ref"]
    if prior:
        _read_ref(run.context, run.db, project_id, prior)
    # BE07 source_refs are evidence-anchor IDs. Exact artifact refs belong to
    # derived_from, with their verified hashes retained in the complete bytes.
    derived = [{"artifact_id": item["artifact_id"], "version": item["version"]} for item in sources]
    return prepare_markdown(run, project_id=project_id, request_id=request_id, content=text,
        artifact_id=prior["artifact_id"] if prior else None, parent_version=prior["version"] if prior else None,
        derived_from=derived, locked_sections=metadata["locked_sections"], provenance={"kind": "revision" if prior else "generated"})


def publish_education_package(run, proposal):
    """Consume the caller's exact approval, then reopen and verify complete bytes."""
    from hermes_cli.artifact_store import publish_markdown
    result = publish_markdown(run, proposal)
    raw = read_project_artifact(run.context, run.db, result["project_id"], result["artifact_id"], result["version"])
    if raw != proposal.content_bytes:
        raise ArtifactConflict("Education publication did not reopen as its complete approved bytes")
    return {**result, "reopen_validation": {"status": "passed", "sha256": _sha(raw), "size": len(raw), "format": "markdown_utf8", "render": "not_supported"}}
