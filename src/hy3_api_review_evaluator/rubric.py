"""Load and validate the canonical machine-readable scoring rubric."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigurationError

DIMENSION_ORDER = (
    "factual_accuracy",
    "location_accuracy",
    "severity_reasonableness",
    "evidence_traceability",
    "actionability",
    "hallucination_control",
)


def _rubric_text() -> str:
    packaged = resources.files("hy3_api_review_evaluator").joinpath("rubric.yaml")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    repository_copy = Path(__file__).parents[2] / "evaluation" / "rubric.yaml"
    try:
        return repository_copy.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError("The evaluation rubric could not be read") from exc


def load_rubric() -> dict[str, Any]:
    try:
        value = yaml.safe_load(_rubric_text())
    except yaml.YAMLError as exc:
        raise ConfigurationError("The evaluation rubric is invalid YAML") from exc
    if not isinstance(value, dict) or value.get("version") != "1.0":
        raise ConfigurationError("The evaluation rubric has an unsupported version")
    dimensions = value.get("dimensions")
    if not isinstance(dimensions, dict) or tuple(dimensions) != DIMENSION_ORDER:
        raise ConfigurationError("The evaluation rubric dimensions are missing or out of order")
    weights = [dimension.get("weight") for dimension in dimensions.values()]
    if any(not isinstance(weight, int) for weight in weights) or sum(weights) != 100:
        raise ConfigurationError("The evaluation rubric weights must be integers summing to 100")
    for name, dimension in dimensions.items():
        criteria = dimension.get("criteria")
        if not isinstance(criteria, dict) or set(criteria) != {0, 1, 2, 3, 4}:
            raise ConfigurationError(f"Rubric dimension {name} must define scores 0 through 4")
    return value
