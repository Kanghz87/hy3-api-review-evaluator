"""Validate dataset structure, provenance, pointers, coverage, and empty human labels."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from hy3_api_review_evaluator.annotation import (
    load_annotation_protocol,
    validate_complete_annotations,
)
from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.evidence import resolve_json_pointer
from hy3_api_review_evaluator.models import ReviewReport
from hy3_api_review_evaluator.spec_loader import load_spec_bytes

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "datasets" / "manifest.jsonl"
PROTOCOL = ROOT / "datasets" / "annotation_protocol.json"
REQUIRED_CATEGORIES = {
    "security",
    "parameter",
    "response",
    "schema",
    "authentication",
    "compatibility",
    "documentation",
}


def _settings() -> Settings:
    return Settings(
        api_key=None,
        base_url="https://tokenhub.tencentmaas.com/v1",
        model="hy3",
        timeout_seconds=10,
        max_retries=0,
        reasoning_effort="high",
        max_file_bytes=2_000_000,
        max_container_nodes=200_000,
        max_nesting_depth=100,
        max_model_chars=120_000,
        max_output_tokens=6_000,
        total_token_budget=850_000,
        default_run_token_budget=150_000,
    )


def validate() -> dict[str, Any]:
    records = [
        json.loads(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(records) != 60:
        raise ValueError(f"Expected 60 records, found {len(records)}")
    if len({item["record_id"] for item in records}) != len(records):
        raise ValueError("record_id values are not unique")

    by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    categories: set[str] = set()
    parsed_specs: dict[str, Any] = {}
    settings = _settings()
    for record in records:
        by_scenario[record["scenario_id"]].append(record)
        categories.update(record["categories"])
        spec_path = ROOT / record["spec_path"]
        report_path = ROOT / record["report_path"]
        if not spec_path.is_file() or not report_path.is_file():
            raise ValueError(f"Missing dataset file for {record['record_id']}")
        if record["spec_path"] not in parsed_specs:
            parsed_specs[record["spec_path"]] = load_spec_bytes(
                spec_path.read_bytes(), spec_path.name, settings
            )
        spec = parsed_specs[record["spec_path"]]
        ReviewReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        for issue in record["expected_issues"]:
            exists, _ = resolve_json_pointer(spec.document, issue["pointer"])
            if not exists:
                raise ValueError(
                    "Expected issue pointer does not exist: "
                    f"{record['record_id']} {issue['pointer']}"
                )
        if any(value is not None for value in record["manual_scores"].values()):
            raise ValueError("Generated dataset must not contain fabricated manual scores")
        for name in ("manual_total", "annotator", "annotated_at"):
            if record[name] is not None:
                raise ValueError(f"Generated dataset field {name} must remain null")

    if len(by_scenario) != 20:
        raise ValueError(f"Expected 20 scenarios, found {len(by_scenario)}")
    for scenario_id, scenario_records in by_scenario.items():
        tiers = {item["reference_tier"] for item in scenario_records}
        if tiers != {"good", "medium", "bad"}:
            raise ValueError(f"Scenario {scenario_id} does not have all three tiers")
    difficulty_counts = Counter(items[0]["difficulty"] for items in by_scenario.values())
    if difficulty_counts != Counter({"easy": 6, "medium": 8, "hard": 6}):
        raise ValueError(f"Unexpected difficulty distribution: {difficulty_counts}")
    missing_categories = REQUIRED_CATEGORIES - categories
    if missing_categories:
        raise ValueError(f"Dataset category coverage is incomplete: {sorted(missing_categories)}")
    adversarial_scenarios = {
        item["scenario_id"] for item in records if item["adversarial_type"] is not None
    }
    if len(adversarial_scenarios) < 6:
        raise ValueError("Dataset must include at least six adversarial scenarios")
    annotation_protocol = load_annotation_protocol(PROTOCOL, records)
    human_path = ROOT / "datasets/annotations/human_scores.csv"
    human_count = (
        len(
            validate_complete_annotations(
                human_path, set(annotation_protocol["selected_record_ids"])
            )
        )
        if human_path.exists()
        else 0
    )
    return {
        "valid": True,
        "scenario_count": len(by_scenario),
        "record_count": len(records),
        "difficulty_counts": dict(sorted(difficulty_counts.items())),
        "category_count": len(categories),
        "adversarial_scenario_count": len(adversarial_scenarios),
        "human_annotation_target_count": annotation_protocol["target_record_count"],
        "human_annotation_additional_count": annotation_protocol["additional_record_count"],
        "manual_annotation_complete": human_count == annotation_protocol["target_record_count"],
        "manual_annotation_scope_count": human_count,
        "full_dataset_manual_annotation_complete": human_count == len(records),
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
