from __future__ import annotations

import pytest
from pydantic import ValidationError

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


@pytest.mark.parametrize("token", ["²", "\uff11", "00", "01", "-1", "+1", "-", "", "9" * 5000])
def test_invalid_array_indexes_are_unresolved_not_exceptions(token):
    assert resolve_json_pointer({"values": ["first"]}, "#/values/" + token) == (False, None)


@pytest.mark.parametrize("token", ["name~", "name~2", "name~9"])
def test_malformed_pointer_escapes_do_not_match_literal_keys(token):
    assert resolve_json_pointer({token: "value"}, "#/" + token) == (False, None)


def test_pointer_decoding_preserves_valid_unusual_dictionary_keys():
    document = {"~1": "escaped", "": "empty", "01": "leading zero", "²": "unicode"}
    assert resolve_json_pointer(document, "#/~01") == (True, "escaped")
    assert resolve_json_pointer(document, "#/") == (True, "empty")
    assert resolve_json_pointer(document, "#/01") == (True, "leading zero")
    assert resolve_json_pointer(document, "#/²") == (True, "unicode")


def test_evidence_pointer_length_is_bounded():
    EvidenceReference(pointer="#/" + "x" * 498)
    with pytest.raises(ValidationError):
        EvidenceReference(pointer="#/" + "x" * 499)


def test_unicode_array_index_is_scored_as_missing_evidence():
    result = check_evidence(
        {"values": ["first"]}, EvidenceReference(pointer="#/values/²", quote="first")
    )
    assert not result.exists and not result.quote_matches
