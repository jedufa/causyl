from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.keychain_probe import (
    FakeCredentialAvailabilityBackend,
    KeychainAccessDenied,
    KeychainBackendUnavailable,
    UnavailableCredentialAvailabilityBackend,
    probe_credential_reference,
)


FIXED_NOW = "2026-04-10T07:45:00Z"


def _receipt(result) -> dict:
    return json.loads(Path(result.receipt_path).read_text(encoding="utf-8"))


def test_probe_success_writes_redacted_available_receipt(tmp_path: Path) -> None:
    backend = FakeCredentialAvailabilityBackend(available_refs={"operator/github"})

    result = probe_credential_reference(
        "operator/github",
        backend=backend,
        receipt_dir=tmp_path,
        now_fn=lambda: FIXED_NOW,
    )

    receipt = _receipt(result)
    assert result.success is True
    assert receipt["outcome"] == "available"
    assert receipt["success"] is True
    assert receipt["credential_ref"] == "operator/github"
    assert receipt["backend_class"] == "fake_keychain"
    assert receipt["boundary_attempt"] == {
        "checked_reference_only": True,
        "credential_value_observed": False,
        "credential_value_persisted": False,
        "credential_value_returned": False,
        "source_of_truth": "external_keychain_or_operator_backend",
    }


def test_probe_missing_writes_explicit_non_success_receipt(tmp_path: Path) -> None:
    backend = FakeCredentialAvailabilityBackend(available_refs=())

    result = probe_credential_reference(
        "operator/github",
        backend=backend,
        receipt_dir=tmp_path,
        now_fn=lambda: FIXED_NOW,
    )

    receipt = _receipt(result)
    assert result.success is False
    assert receipt["outcome"] == "missing_credential"
    assert receipt["success"] is False


def test_probe_unavailable_writes_explicit_non_success_receipt(tmp_path: Path) -> None:
    backend = FakeCredentialAvailabilityBackend(unavailable=True)

    result = probe_credential_reference(
        "operator/github",
        backend=backend,
        receipt_dir=tmp_path,
        now_fn=lambda: FIXED_NOW,
    )

    receipt = _receipt(result)
    assert result.success is False
    assert receipt["outcome"] == "backend_unavailable"
    assert receipt["success"] is False


def test_null_backend_reports_unavailable_without_guessing(tmp_path: Path) -> None:
    result = probe_credential_reference(
        "operator/github",
        backend=UnavailableCredentialAvailabilityBackend(),
        receipt_dir=tmp_path,
        now_fn=lambda: FIXED_NOW,
    )

    receipt = _receipt(result)
    assert result.success is False
    assert receipt["outcome"] == "backend_unavailable"
    assert receipt["backend_class"] == "unavailable_keychain"


def test_probe_denied_writes_explicit_non_success_receipt(tmp_path: Path) -> None:
    backend = FakeCredentialAvailabilityBackend(denied_refs={"operator/github"})

    result = probe_credential_reference(
        "operator/github",
        backend=backend,
        receipt_dir=tmp_path,
        now_fn=lambda: FIXED_NOW,
    )

    receipt = _receipt(result)
    assert result.success is False
    assert receipt["outcome"] == "access_denied"
    assert receipt["success"] is False


def test_receipt_ignores_backend_exception_text(tmp_path: Path) -> None:
    class VerboseBackend:
        backend_class = "verbose backend"

        def has_credential_reference(self, credential_ref: str) -> bool:
            raise KeychainBackendUnavailable("backend message must not be persisted")

    result = probe_credential_reference(
        "operator/github",
        backend=VerboseBackend(),
        receipt_dir=tmp_path,
        now_fn=lambda: FIXED_NOW,
    )

    receipt_text = Path(result.receipt_path).read_text(encoding="utf-8")
    assert "backend message must not be persisted" not in receipt_text
    assert "verbose_backend" in receipt_text
    assert _receipt(result)["outcome"] == "backend_unavailable"


def test_receipt_ignores_denied_exception_text(tmp_path: Path) -> None:
    class DeniedBackend:
        backend_class = "denied backend"

        def has_credential_reference(self, credential_ref: str) -> bool:
            raise KeychainAccessDenied("denial details must not be persisted")

    result = probe_credential_reference(
        "operator/github",
        backend=DeniedBackend(),
        receipt_dir=tmp_path,
        now_fn=lambda: FIXED_NOW,
    )

    receipt_text = Path(result.receipt_path).read_text(encoding="utf-8")
    assert "denial details must not be persisted" not in receipt_text
    assert _receipt(result)["outcome"] == "access_denied"


@pytest.mark.parametrize("credential_ref", ["", "../bad", "bad ref", "/absolute"])
def test_invalid_reference_is_rejected_without_receipt(tmp_path: Path, credential_ref: str) -> None:
    backend = FakeCredentialAvailabilityBackend()

    with pytest.raises(ValueError):
        probe_credential_reference(
            credential_ref,
            backend=backend,
            receipt_dir=tmp_path,
            now_fn=lambda: FIXED_NOW,
        )

    assert list(tmp_path.glob("*.json")) == []
