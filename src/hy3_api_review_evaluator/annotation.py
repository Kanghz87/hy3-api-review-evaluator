"""Blind annotation helpers; no human score exists until a person submits it."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ReviewReport
from .rubric import DIMENSION_ORDER, load_rubric


def load_annotation_protocol(path: Path, manifest_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Load and validate the frozen human-validation subset without reading scores."""
    if not path.exists():
        raise ValueError(f"Annotation protocol does not exist: {path}")
    protocol = json.loads(path.read_text(encoding="utf-8"))
    selected = protocol.get("selected_record_ids")
    if not isinstance(selected, list) or not selected:
        raise ValueError("Annotation protocol must contain selected_record_ids")
    if any(not isinstance(record_id, str) or not record_id for record_id in selected):
        raise ValueError("Every selected annotation record ID must be a non-empty string")
    if len(selected) != len(set(selected)):
        raise ValueError("Annotation protocol contains duplicate selected record IDs")
    expected_count = protocol.get("target_record_count")
    if expected_count != len(selected):
        raise ValueError(
            f"Annotation protocol target mismatch: expected {expected_count}, got {len(selected)}"
        )
    manifest_ids = {str(record["record_id"]) for record in manifest_records}
    unknown = sorted(set(selected) - manifest_ids)
    if unknown:
        raise ValueError(f"Annotation protocol contains {len(unknown)} unknown record IDs")
    completed_at_freeze = protocol.get("completed_record_ids_at_freeze", [])
    additional = protocol.get("additional_record_ids", [])
    if len(completed_at_freeze) != protocol.get("completed_at_freeze_count"):
        raise ValueError("Annotation protocol completed-at-freeze count does not match")
    if len(additional) != protocol.get("additional_record_count"):
        raise ValueError("Annotation protocol additional record count does not match")
    if set(completed_at_freeze) & set(additional):
        raise ValueError("Completed and additional annotation IDs must be disjoint")
    if set(completed_at_freeze) | set(additional) != set(selected):
        raise ValueError("Completed and additional annotation IDs must form the selected subset")

    selected_records = [
        record for record in manifest_records if str(record["record_id"]) in set(selected)
    ]
    distribution = protocol.get("expected_distribution", {})
    expected_difficulty = distribution.get("difficulty", {})
    actual_difficulty = Counter(str(record["difficulty"]) for record in selected_records)
    if actual_difficulty != Counter(expected_difficulty):
        raise ValueError("Annotation protocol difficulty distribution does not match")
    expected_tier = distribution.get("reference_tier", {})
    actual_tier = Counter(str(record["reference_tier"]) for record in selected_records)
    if actual_tier != Counter(expected_tier):
        raise ValueError("Annotation protocol tier distribution does not match")
    unique_scenarios = {str(record["scenario_id"]) for record in selected_records}
    if len(unique_scenarios) != distribution.get("unique_scenarios"):
        raise ValueError("Annotation protocol scenario coverage does not match")
    adversarial_types = {
        str(record["adversarial_type"])
        for record in selected_records
        if record.get("is_adversarial") and record.get("adversarial_type")
    }
    if len(adversarial_types) != distribution.get("adversarial_types"):
        raise ValueError("Annotation protocol adversarial coverage does not match")
    return protocol


def blinded_order(records: list[dict[str, Any]], annotator_alias: str) -> list[dict[str, Any]]:
    """Stable per-annotator order that does not expose construction tier."""
    alias = annotator_alias.strip() or "anonymous"
    return sorted(
        records,
        key=lambda item: hashlib.sha256(f"{alias}|{item['record_id']}".encode()).hexdigest(),
    )


def display_id(record_id: str, annotator_alias: str) -> str:
    digest = hashlib.sha256(f"display|{annotator_alias.strip()}|{record_id}".encode()).hexdigest()[
        :10
    ]
    return f"sample-{digest}"


def weighted_total(scores: dict[str, int]) -> float:
    rubric = load_rubric()
    if set(scores) != set(DIMENSION_ORDER):
        raise ValueError("All six rubric dimensions are required")
    if any(not isinstance(value, int) or not 0 <= value <= 4 for value in scores.values()):
        raise ValueError("Every rubric score must be an integer from 0 through 4")
    return round(
        sum(scores[name] / 4 * rubric["dimensions"][name]["weight"] for name in DIMENSION_ORDER),
        2,
    )


def build_copy_bundle(sample_id: str, spec_text: str, report: ReviewReport) -> str:
    """Build blind, copy-friendly material without construction tier or automatic scores."""
    rubric = load_rubric()
    score_template = "\n".join(
        f"- {rubric['dimensions'][name]['label_zh']} ({name}): __ / 4" for name in DIMENSION_ORDER
    )
    report_json = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return f"""SAMPLE: {sample_id}

=== OPENAPI DOCUMENT ===
{spec_text.rstrip()}

=== REVIEW REPORT TO ANNOTATE ===
{report_json}

=== HUMAN SCORE TEMPLATE ===
{score_template}
- 备注:
"""


def read_completed(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return {row["record_id"]: row for row in csv.DictReader(stream)}


def validate_complete_annotations(
    path: Path, expected_record_ids: set[str]
) -> list[dict[str, str]]:
    """Validate genuine completed labels before they are copied into the public dataset."""
    if not path.exists():
        raise ValueError(f"Annotation file does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    record_ids = [row.get("record_id", "") for row in rows]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("Annotation file contains duplicate record_id values")
    missing = sorted(expected_record_ids - set(record_ids))
    unknown = sorted(set(record_ids) - expected_record_ids)
    if missing or unknown:
        raise ValueError(
            f"Annotation coverage mismatch: {len(missing)} missing, {len(unknown)} unknown"
        )
    for row in rows:
        try:
            scores = {name: int(row[name]) for name in DIMENSION_ORDER}
            provided_total = float(row["manual_total"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid scores in annotation {row.get('record_id', '?')}") from exc
        expected_total = weighted_total(scores)
        if not math.isclose(provided_total, expected_total, abs_tol=0.005):
            raise ValueError(
                f"manual_total mismatch in {row['record_id']}: "
                f"expected {expected_total}, got {provided_total}"
            )
        if not row.get("annotator", "").strip() or not row.get("annotated_at", "").strip():
            raise ValueError(f"Missing annotator or timestamp in {row['record_id']}")
    return rows


def _safe_cell(value: str) -> str:
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def save_annotation(
    path: Path,
    *,
    record_id: str,
    sample_id: str,
    annotator_alias: str,
    scores: dict[str, int],
    notes: str,
) -> None:
    existing = read_completed(path)
    existing[record_id] = {
        "record_id": record_id,
        "display_id": sample_id,
        **{name: str(scores[name]) for name in DIMENSION_ORDER},
        "manual_total": str(weighted_total(scores)),
        "annotator": annotator_alias.strip(),
        "notes": _safe_cell(notes.strip()),
        "annotated_at": datetime.now(UTC).isoformat(),
    }
    fieldnames = [
        "record_id",
        "display_id",
        *DIMENSION_ORDER,
        "manual_total",
        "annotator",
        "notes",
        "annotated_at",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing[key] for key in sorted(existing))
    temporary.replace(path)
