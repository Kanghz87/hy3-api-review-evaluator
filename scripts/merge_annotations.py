"""Validate the frozen genuine-human subset and publish a clean annotation CSV."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from hy3_api_review_evaluator.annotation import (
    load_annotation_protocol,
    validate_complete_annotations,
)
from hy3_api_review_evaluator.rubric import DIMENSION_ORDER

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "datasets" / "manifest.jsonl"
PROTOCOL = ROOT / "datasets" / "annotation_protocol.json"
DEFAULT_INPUT = ROOT / "datasets" / "annotations" / "human_scores.local.csv"
DEFAULT_OUTPUT = ROOT / "datasets" / "annotations" / "human_scores.csv"
PUBLIC_FIELDS = [
    "record_id",
    *DIMENSION_ORDER,
    "manual_total",
    "annotator",
    "notes",
    "annotated_at",
]


def _expected_ids() -> set[str]:
    manifest = [
        json.loads(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    protocol = load_annotation_protocol(PROTOCOL, manifest)
    return set(protocol["selected_record_ids"])


def merge(input_path: Path, output_path: Path) -> int:
    if input_path.resolve() == output_path.resolve():
        raise ValueError("The original annotation file must not be overwritten")
    rows = validate_complete_annotations(input_path, _expected_ids())
    aliases = {
        alias: f"human-{index:02d}"
        for index, alias in enumerate(sorted({row["annotator"] for row in rows}), start=1)
    }
    normalized = [{**row, "annotator": aliases[row["annotator"]]} for row in rows]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=PUBLIC_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(normalized, key=lambda row: row["record_id"]))
    temporary.replace(output_path)
    return len(rows)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    count = merge(args.input.resolve(), args.output.resolve())
    print(f"Wrote {count} validated, pseudonymized annotations locally to {args.output}")
