"""Allowlisted host verification actions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


_GRADLE_TASK_RE = re.compile(r"^:[A-Za-z0-9:_-]{3,128}$")
_JAVA_SELECTOR_RE = re.compile(r"^[A-Za-z0-9_.$*]+$")
_PLAYWRIGHT_SPEC_RE = re.compile(r"^[A-Za-z0-9_./@:-]{1,256}$")
_PLAYWRIGHT_PROJECT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _normalize_java_selectors(params: Dict[str, Any]) -> List[str]:
    selectors: List[str] = []
    single_selector = str(params.get("test_selector") or "").strip()
    if single_selector:
        selectors.append(single_selector)
    raw_multi = params.get("test_selectors")
    if raw_multi not in (None, ""):
        if not isinstance(raw_multi, list):
            raise ValueError("android_unit_test test_selectors must be a list")
        for raw in raw_multi:
            selector = str(raw or "").strip()
            if selector:
                selectors.append(selector)
    deduped: List[str] = []
    seen = set()
    for selector in selectors:
        if not _JAVA_SELECTOR_RE.match(selector):
            raise ValueError("invalid android_unit_test test selector")
        if selector in seen:
            continue
        seen.add(selector)
        deduped.append(selector)
    if not deduped:
        raise ValueError("android_unit_test requires test_selector or test_selectors")
    return deduped


@dataclass(frozen=True)
class VerificationActionSpec:
    """Resolved execution plan for one allowlisted action."""

    action_id: str
    cwd_rel: str
    timeout_s_default: int
    artifact_globs: List[str]

    def build_argv(self, params: Dict[str, Any]) -> List[str]:
        raise NotImplementedError

    def cwd(self, repo_root: Path) -> Path:
        return (Path(repo_root).resolve() / self.cwd_rel).resolve()


@dataclass(frozen=True)
class AndroidUnitTestSpec(VerificationActionSpec):
    """Allowlisted Gradle Android unit test runner."""

    def build_argv(self, params: Dict[str, Any]) -> List[str]:
        gradle_task = str(params.get("gradle_task") or "").strip()
        if not _GRADLE_TASK_RE.match(gradle_task):
            raise ValueError("android_unit_test requires gradle_task like :app:testDebugUnitTest")
        # Force single-shot execution in the host runner path instead of
        # relying on a long-lived daemon from a previous shell environment.
        argv = ["./gradlew", "--no-daemon", "--console=plain", "--stacktrace", gradle_task]
        for selector in _normalize_java_selectors(params):
            argv.extend(["--tests", selector])
        return argv


@dataclass(frozen=True)
class PlaywrightSpec(VerificationActionSpec):
    """Allowlisted Playwright spec runner."""

    def build_argv(self, params: Dict[str, Any]) -> List[str]:
        spec = str(params.get("spec") or "").strip()
        if not _PLAYWRIGHT_SPEC_RE.match(spec):
            raise ValueError("playwright_spec requires a relative spec path")
        argv = ["npx", "playwright", "test", spec]
        project = str(params.get("project") or "").strip()
        if project:
            if not _PLAYWRIGHT_PROJECT_RE.match(project):
                raise ValueError("invalid playwright project")
            argv.extend(["--project", project])
        return argv


_ACTIONS: Dict[str, VerificationActionSpec] = {
    "android_unit_test": AndroidUnitTestSpec(
        action_id="android_unit_test",
        cwd_rel="infra/mobile/android",
        timeout_s_default=3600,
        artifact_globs=["**/TEST-*.xml"],
    ),
    "playwright_spec": PlaywrightSpec(
        action_id="playwright_spec",
        cwd_rel="mobile/web",
        timeout_s_default=600,
        artifact_globs=["playwright-report/**", "test-results/**"],
    ),
}


def allowed_action_ids() -> List[str]:
    """Return stable allowlisted action ids."""
    return sorted(_ACTIONS.keys())


def get_action_spec(action_id: str) -> VerificationActionSpec:
    """Resolve one action spec or raise."""
    aid = str(action_id or "").strip()
    spec = _ACTIONS.get(aid)
    if spec is None:
        raise ValueError(f"unknown verification action: {aid}")
    return spec
