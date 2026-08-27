from __future__ import annotations

from hy3_api_review_evaluator.evidence import (
    check_evidence,
    operation_pointer,
    resolve_json_pointer,
)
from hy3_api_review_evaluator.models import EvidenceReference


def test_json_pointer_handles_paths_and_arrays() -> None:
    document = {"paths": {"/pets/{id}": {"get": {"parameters": [{"name": "id"}]}}}}
    pointer = operation_pointer("/pets/{id}", "GET") + "/parameters/0/name"
    assert resolve_json_pointer(document, pointer) == (True, "id")


def test_evidence_requires_existing_pointer_and_matching_quote() -> None:
    document = {"info": {"title": "Pets"}}
    good = check_evidence(document, EvidenceReference(pointer="#/info/title", quote="Pets"))
    fake = check_evidence(document, EvidenceReference(pointer="#/paths/~1fake/get", quote="fake"))
    assert good.exists and good.quote_matches
    assert not fake.exists and not fake.quote_matches
