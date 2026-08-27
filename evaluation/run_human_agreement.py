"""Reproduce human agreement from saved files only: no API key, provider call, or score tuning."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from hy3_api_review_evaluator.annotation import (
    load_annotation_protocol,
    validate_complete_annotations,
)
from hy3_api_review_evaluator.human_agreement import summarize_pairs
from hy3_api_review_evaluator.models import EvaluationResult, ReviewReport
from hy3_api_review_evaluator.rubric import DIMENSION_ORDER

ROOT = Path(__file__).parents[1]
ANNOTATIONS = ROOT / "datasets/annotations/human_scores.csv"
MANIFEST = ROOT / "datasets/manifest.jsonl"
PROTOCOL = ROOT / "datasets/annotation_protocol.json"
HYBRID = ROOT / "results/hybrid-records.jsonl"
BASELINE = ROOT / "results/preliminary-local-records.csv"
SUMMARY = ROOT / "results/human-agreement-summary.json"
RECORDS = ROOT / "results/human-agreement-records.csv"


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed = {row["record_id"]: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError("Duplicate record IDs in an agreement input")
    return indexed


def _text_hash(path: Path) -> str:
    # Ignore BOM and platform newline conversion; numeric and textual content stays intact.
    return hashlib.sha256(path.read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest()


def build() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _jsonl(MANIFEST)
    protocol = load_annotation_protocol(PROTOCOL, manifest)
    annotations = validate_complete_annotations(ANNOTATIONS, set(protocol["selected_record_ids"]))
    dates = [datetime.fromisoformat(row["annotated_at"]) for row in annotations]
    if any(value.utcoffset() is None for value in dates):
        raise ValueError("Annotation timestamps must contain a timezone")
    metadata = _index(manifest)
    hybrid = _index(_jsonl(HYBRID))
    with BASELINE.open(encoding="utf-8-sig", newline="") as stream:
        baseline = _index(list(csv.DictReader(stream)))
    if set(hybrid) != set(metadata) or set(baseline) != set(metadata):
        raise ValueError("Both automated inputs must cover the complete manifest exactly")

    rows: list[dict[str, Any]] = []
    for annotation in sorted(annotations, key=lambda row: row["record_id"]):
        record_id = annotation["record_id"]
        meta = metadata[record_id]
        evaluation = EvaluationResult.model_validate(hybrid[record_id]["evaluation"])
        report = ReviewReport.model_validate_json(
            (ROOT / meta["report_path"]).read_text(encoding="utf-8")
        )
        canonical = json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if hashlib.sha256(canonical.encode()).hexdigest() != evaluation.report_sha256:
            raise ValueError(f"Stored report differs from evaluated report: {record_id}")
        dimensions = {
            dimension.name: dimension.final_score for dimension in evaluation.dimension_scores
        }
        if set(dimensions) != set(DIMENSION_ORDER):
            raise ValueError(f"Incomplete or duplicate dimension scores: {record_id}")
        row: dict[str, Any] = {
            "record_id": record_id,
            "scenario_id": meta["scenario_id"],
            "difficulty": meta["difficulty"],
            "reference_tier": meta["reference_tier"],
            "report_adversarial": meta["report_adversarial"],
            "human_total": float(annotation["manual_total"]),
            "hybrid_total": evaluation.total_score,
            "baseline_total": float(baseline[record_id]["total_score"]),
        }
        for mode in ("hybrid", "baseline"):
            row[f"{mode}_signed_error"] = row[f"{mode}_total"] - row["human_total"]
            row[f"{mode}_absolute_error"] = abs(row[f"{mode}_signed_error"])
        for name in DIMENSION_ORDER:
            row[f"human_{name}"] = int(annotation[name])
            row[f"hybrid_{name}"] = dimensions[name]
            row[f"baseline_{name}"] = int(baseline[record_id][f"score_{name}"])
        rows.append(row)

    summary = {
        "analysis_version": "1.0",
        "status": "complete",
        "scope": "frozen_stratified_human_subset",
        "sample_count": len(rows),
        "scenario_count": len({row["scenario_id"] for row in rows}),
        "full_dataset_record_count": len(manifest),
        "full_dataset_human_annotation_complete": len(rows) == len(manifest),
        "annotator_count": len({row["annotator"] for row in annotations}),
        "annotation_date_range": [min(dates).date().isoformat(), max(dates).date().isoformat()],
        "total_score_scale": [0, 100],
        "dimension_score_scale": [0, 4],
        "source_sha256_normalized_utf8": {
            path.relative_to(ROOT).as_posix(): _text_hash(path)
            for path in (ANNOTATIONS, MANIFEST, PROTOCOL, HYBRID, BASELINE)
        },
        **summarize_pairs(rows),
        "limitations": [
            "One maintainer supplied the human labels; no inter-rater reliability was measured.",
            "The UI hid tiers and automatic scores; independence is not externally verified.",
            "This is a stratified subset, not a random population sample or new held-out dataset.",
            "Reports share scenarios: 33 records do not mean 33 independent API documents.",
            "Small tier/difficulty groups are descriptive, not evidence of generalization.",
            "Human scores, model results, rubric, thresholds and selection were not retuned.",
        ],
    }
    return summary, rows


def _records_text(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def run(*, check: bool = False) -> dict[str, Any]:
    summary, rows = build()
    record_text = _records_text(rows)
    if check:
        if json.loads(SUMMARY.read_text(encoding="utf-8")) != summary:
            raise ValueError("Human agreement summary does not match its source files")
        if RECORDS.read_text(encoding="utf-8") != record_text:
            raise ValueError("Human agreement record table does not match its source files")
    else:
        SUMMARY.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        RECORDS.write_text(record_text, encoding="utf-8", newline="")
    return {
        "validated": True,
        "check_only": check,
        "sample_count": summary["sample_count"],
        "annotator_count": summary["annotator_count"],
        "overall": summary["overall"],
        "new_hy3_calls": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Validate saved outputs without writing"
    )
    print(json.dumps(run(check=parser.parse_args().check), ensure_ascii=False, indent=2))
