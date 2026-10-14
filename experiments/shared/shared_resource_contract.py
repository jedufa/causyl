"""Canonical shared-resource vocabulary for concurrency governance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
import re
from typing import Any, Dict, Iterable, List, Mapping


_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class SharedResourceContractError(ValueError):
    """Raised when a resource identifier cannot be normalized safely."""


class SharedResourceClass(str, Enum):
    HOST = "host"
    WORKSPACE = "workspace"
    MODEL_PROVIDER = "model_provider"
    DEVICE = "device"
    JOB_FAMILY = "job_family"
    PROJECT = "project"


class SharedResourceLimitKind(str, Enum):
    MAX_CONCURRENT = "max_concurrent"
    MUTEX = "mutex"


@dataclass(frozen=True)
class SharedResourceKey:
    resource_class: SharedResourceClass
    scope: str
    locator: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "resource_class": self.resource_class.value,
            "scope": self.scope,
            "locator": self.locator,
            "canonical_key": f"{self.resource_class.value}:{self.scope}:{self.locator}",
        }


@dataclass(frozen=True)
class SharedResourceLimit:
    resource: SharedResourceKey
    limit_kind: SharedResourceLimitKind
    value: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "resource": self.resource.as_dict(),
            "limit_kind": self.limit_kind.value,
            "value": self.value,
        }


def _normalize_slug(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        raise SharedResourceContractError(f"{field_name} is required")
    if not _SLUG_PATTERN.match(text):
        raise SharedResourceContractError(
            f"{field_name} must use lowercase letters, digits, hyphen, or underscore"
        )
    return text


def host_resource_key(host_id: str = "default") -> SharedResourceKey:
    return SharedResourceKey(
        resource_class=SharedResourceClass.HOST,
        scope="global",
        locator=_normalize_slug(host_id, field_name="host_id"),
    )


def workspace_resource_key(path_text: str) -> SharedResourceKey:
    raw = str(path_text or "").strip()
    if not raw:
        raise SharedResourceContractError("workspace resource path is required")
    path = PurePosixPath(raw)
    if path.is_absolute():
        raise SharedResourceContractError("workspace resource path must be repo-relative")
    parts = list(path.parts)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise SharedResourceContractError(
            "workspace resource path must not contain empty, dot, or parent segments"
        )
    return SharedResourceKey(
        resource_class=SharedResourceClass.WORKSPACE,
        scope="repo",
        locator=PurePosixPath(*parts).as_posix(),
    )


def model_provider_resource_key(provider: str) -> SharedResourceKey:
    return SharedResourceKey(
        resource_class=SharedResourceClass.MODEL_PROVIDER,
        scope="provider",
        locator=_normalize_slug(provider, field_name="model_provider"),
    )


def device_resource_key(device_class: str, device_id: str) -> SharedResourceKey:
    return SharedResourceKey(
        resource_class=SharedResourceClass.DEVICE,
        scope=_normalize_slug(device_class, field_name="device_class"),
        locator=_normalize_slug(device_id, field_name="device_id"),
    )


def job_family_resource_key(resource_class: str) -> SharedResourceKey:
    return SharedResourceKey(
        resource_class=SharedResourceClass.JOB_FAMILY,
        scope="scheduler",
        locator=_normalize_slug(resource_class, field_name="resource_class"),
    )


def project_resource_key(project_semaphore: str) -> SharedResourceKey:
    raw = str(project_semaphore or "").strip().lower()
    if not raw:
        raise SharedResourceContractError("project_semaphore is required")
    parts = [part.strip() for part in raw.split("/") if part.strip()]
    if len(parts) != 2:
        raise SharedResourceContractError(
            "project_semaphore must use the canonical <scope>/<project_id> shape"
        )
    scope, project_id = parts
    return SharedResourceKey(
        resource_class=SharedResourceClass.PROJECT,
        scope=_normalize_slug(scope, field_name="project_semaphore scope"),
        locator=_normalize_slug(project_id, field_name="project_semaphore project_id"),
    )


def max_concurrent_limit(resource: SharedResourceKey, value: Any) -> SharedResourceLimit:
    try:
        limit = int(value)
    except Exception as exc:  # pragma: no cover - defensive conversion wrapper
        raise SharedResourceContractError("max_concurrent limit must be an integer") from exc
    if limit <= 0:
        raise SharedResourceContractError("max_concurrent limit must be a positive integer")
    return SharedResourceLimit(
        resource=resource,
        limit_kind=SharedResourceLimitKind.MAX_CONCURRENT,
        value=limit,
    )


def mutex_limit(resource: SharedResourceKey) -> SharedResourceLimit:
    return SharedResourceLimit(
        resource=resource,
        limit_kind=SharedResourceLimitKind.MUTEX,
        value=1,
    )


def canonical_limit_semantics() -> List[Dict[str, str]]:
    return [
        {
            "limit_kind": SharedResourceLimitKind.MAX_CONCURRENT.value,
            "value_contract": "positive_integer",
            "conflict_semantics": "counted_claims_block_when_limit_reached",
        },
        {
            "limit_kind": SharedResourceLimitKind.MUTEX.value,
            "value_contract": "fixed_one",
            "conflict_semantics": "mutually_exclusive_claims",
        },
    ]


def map_experiment_resource_policy(resources: Mapping[str, Any]) -> Dict[str, Any]:
    resource_class = job_family_resource_key(resources.get("resource_class"))
    project_key = project_resource_key(resources.get("project_semaphore"))
    project_limit = max_concurrent_limit(project_key, resources.get("max_parallel_runs"))
    return {
        "resource_classification": resource_class.as_dict(),
        "claim_keys": [project_key.as_dict()],
        "limit_contracts": [project_limit.as_dict()],
        "source_fields": {
            "resource_classification": "resources.resource_class",
            "claim_keys": ["resources.project_semaphore"],
            "limit_contracts": ["resources.project_semaphore", "resources.max_parallel_runs"],
        },
    }


def parse_scheduler_resource_class_caps(values: Iterable[str]) -> List[Dict[str, Any]]:
    limits: List[Dict[str, Any]] = []
    for item in values:
        raw = str(item or "").strip()
        if not raw:
            continue
        if ":" in raw:
            name, _, value = raw.partition(":")
        elif "=" in raw:
            name, _, value = raw.partition("=")
        else:
            raise SharedResourceContractError(
                "scheduler.resource_class_caps entries must use <resource_class>:<limit>"
            )
        resource = job_family_resource_key(name)
        limits.append(max_concurrent_limit(resource, value).as_dict())
    return limits


def map_runtime_policy_resource_caps(values: Iterable[str]) -> Dict[str, Any]:
    return {
        "claim_keys": [],
        "limit_contracts": parse_scheduler_resource_class_caps(values),
        "source_fields": {
            "limit_contracts": ["scheduler.resource_class_caps"],
        },
    }


def map_shared_resource_keys(keys: Iterable[str]) -> List[Dict[str, str]]:
    return [workspace_resource_key(item).as_dict() for item in keys]


def map_internal_scheduler_resources(
    *,
    shared_resource_keys: Iterable[str] = (),
    host_ids: Iterable[str] = (),
    model_providers: Iterable[str] = (),
    devices: Iterable[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    claim_keys: List[Dict[str, str]] = []
    limit_contracts: List[Dict[str, Any]] = []

    for host_id in host_ids:
        resource = host_resource_key(str(host_id or ""))
        claim_keys.append(resource.as_dict())
        limit_contracts.append(mutex_limit(resource).as_dict())

    for provider in model_providers:
        resource = model_provider_resource_key(str(provider or ""))
        claim_keys.append(resource.as_dict())
        limit_contracts.append(mutex_limit(resource).as_dict())

    for raw_device in devices:
        device = raw_device if isinstance(raw_device, Mapping) else {}
        resource = device_resource_key(
            str(device.get("device_class") or ""),
            str(device.get("device_id") or ""),
        )
        claim_keys.append(resource.as_dict())
        limit_contracts.append(mutex_limit(resource).as_dict())

    for key in shared_resource_keys:
        resource = workspace_resource_key(str(key or ""))
        claim_keys.append(resource.as_dict())
        limit_contracts.append(mutex_limit(resource).as_dict())

    return {
        "claim_keys": claim_keys,
        "limit_contracts": limit_contracts,
        "limit_semantics": canonical_limit_semantics(),
        "source_fields": {
            "claim_keys": [
                "shared_resource_keys",
                "host_id",
                "model_provider",
                "device_class/device_id",
            ],
            "limit_contracts": [
                "shared_resource_keys",
                "host_id",
                "model_provider",
                "device_class/device_id",
            ],
        },
    }
