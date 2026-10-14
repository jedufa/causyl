from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal, Protocol

from tools.shared.fsio import utc_now_iso, write_json_atomic

CredentialOutcome = Literal[
    "available",
    "missing_credential",
    "backend_unavailable",
    "access_denied",
]

OUTCOME_AVAILABLE: CredentialOutcome = "available"
OUTCOME_MISSING: CredentialOutcome = "missing_credential"
OUTCOME_UNAVAILABLE: CredentialOutcome = "backend_unavailable"
OUTCOME_DENIED: CredentialOutcome = "access_denied"

RECEIPT_SCHEMA_VERSION = "keychain_probe_receipt_v1"
DEFAULT_RECEIPT_DIR = Path("runtime/state/keychain_probe")
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,191}$")


class CredentialReferenceError(ValueError):
    """Raised when a credential reference is not a safe named reference."""


class KeychainBackendUnavailable(RuntimeError):
    """Raised by a backend when the credential source cannot be checked."""


class KeychainAccessDenied(RuntimeError):
    """Raised by a backend when the caller is not allowed to check the reference."""


class CredentialAvailabilityBackend(Protocol):
    """Secret-safe boundary: check availability by reference, never by value."""

    @property
    def backend_class(self) -> str:
        ...

    def has_credential_reference(self, credential_ref: str) -> bool:
        ...


@dataclass(frozen=True)
class CredentialProbeResult:
    credential_ref: str
    backend_class: str
    outcome: CredentialOutcome
    receipt_path: Path
    created_utc: str

    @property
    def success(self) -> bool:
        return self.outcome == OUTCOME_AVAILABLE


class FakeCredentialAvailabilityBackend:
    """Test backend that models availability without storing credential values."""

    def __init__(
        self,
        *,
        available_refs: Iterable[str] = (),
        denied_refs: Iterable[str] = (),
        unavailable: bool = False,
        backend_class: str = "fake_keychain",
    ) -> None:
        self._available_refs = frozenset(_normalize_credential_ref(ref) for ref in available_refs)
        self._denied_refs = frozenset(_normalize_credential_ref(ref) for ref in denied_refs)
        self._unavailable = bool(unavailable)
        self._backend_class = _safe_backend_class(backend_class)

    @property
    def backend_class(self) -> str:
        return self._backend_class

    def has_credential_reference(self, credential_ref: str) -> bool:
        ref = _normalize_credential_ref(credential_ref)
        if self._unavailable:
            raise KeychainBackendUnavailable("credential backend unavailable")
        if ref in self._denied_refs:
            raise KeychainAccessDenied("credential reference check denied")
        return ref in self._available_refs


class UnavailableCredentialAvailabilityBackend:
    """Null adapter for callers that have no configured external backend."""

    backend_class = "unavailable_keychain"

    def has_credential_reference(self, credential_ref: str) -> bool:
        _normalize_credential_ref(credential_ref)
        raise KeychainBackendUnavailable("credential backend unavailable")


def probe_credential_reference(
    credential_ref: str,
    *,
    backend: CredentialAvailabilityBackend,
    receipt_dir: Path | str = DEFAULT_RECEIPT_DIR,
    now_fn: Callable[[], str] = utc_now_iso,
) -> CredentialProbeResult:
    ref = _normalize_credential_ref(credential_ref)
    backend_class = _safe_backend_class(getattr(backend, "backend_class", backend.__class__.__name__))
    created_utc = _safe_timestamp(now_fn())

    try:
        available = bool(backend.has_credential_reference(ref))
    except KeychainAccessDenied:
        outcome: CredentialOutcome = OUTCOME_DENIED
    except KeychainBackendUnavailable:
        outcome = OUTCOME_UNAVAILABLE
    except Exception:
        outcome = OUTCOME_UNAVAILABLE
    else:
        outcome = OUTCOME_AVAILABLE if available else OUTCOME_MISSING

    receipt_path = _write_receipt(
        Path(receipt_dir),
        credential_ref=ref,
        backend_class=backend_class,
        outcome=outcome,
        created_utc=created_utc,
    )
    return CredentialProbeResult(
        credential_ref=ref,
        backend_class=backend_class,
        outcome=outcome,
        receipt_path=receipt_path,
        created_utc=created_utc,
    )


def _write_receipt(
    receipt_dir: Path,
    *,
    credential_ref: str,
    backend_class: str,
    outcome: CredentialOutcome,
    created_utc: str,
) -> Path:
    ref_hash = hashlib.sha256(credential_ref.encode("utf-8")).hexdigest()[:16]
    receipt_path = receipt_dir / f"{created_utc.replace(':', '').replace('-', '')}__{ref_hash}__{uuid.uuid4().hex}.json"
    payload = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "created_utc": created_utc,
        "credential_ref": credential_ref,
        "backend_class": backend_class,
        "outcome": outcome,
        "success": outcome == OUTCOME_AVAILABLE,
        "boundary_attempt": {
            "checked_reference_only": True,
            "credential_value_observed": False,
            "credential_value_persisted": False,
            "credential_value_returned": False,
            "source_of_truth": "external_keychain_or_operator_backend",
        },
    }
    write_json_atomic(receipt_path, payload)
    return receipt_path


def _normalize_credential_ref(value: str) -> str:
    ref = str(value or "").strip()
    if not ref:
        raise CredentialReferenceError("credential_ref is required")
    if not _REFERENCE_RE.fullmatch(ref):
        raise CredentialReferenceError("credential_ref contains unsupported characters")
    if ".." in Path(ref).parts:
        raise CredentialReferenceError("credential_ref must not contain traversal segments")
    return ref


def _safe_backend_class(value: str) -> str:
    text = str(value or "").strip() or "unknown_backend"
    safe = re.sub(r"[^A-Za-z0-9_.:-]+", "_", text)[:96].strip("._:-")
    return safe or "unknown_backend"


def _safe_timestamp(value: str) -> str:
    text = str(value or "").strip() or utc_now_iso()
    return re.sub(r"[^0-9TZ:+.-]+", "_", text)[:64]
