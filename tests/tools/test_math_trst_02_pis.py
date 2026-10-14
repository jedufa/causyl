from __future__ import annotations

import re
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _pis_path() -> Path:
    return (
        _repo_root()
        / "governance"
        / "ledger"
        / "items"
        / "PIS-20260307-215341Z-4A62__math-trst-02"
        / "pis.md"
    )


def _assert_valid_math_proof_pis(text: str) -> None:
    required_sections = [
        "## 1. Problem Restatement",
        "## 2. Definitions",
        "## 3. Proof Strategy",
        "## 4. Step-by-Step Proof",
        "## 5. Key Logical Claims",
        "## 6. Edge Case Check",
        "## 7. Final Conclusion",
    ]

    assert "**UID:** PIS-20260307-215341Z-4A62" in text

    last_pos = -1
    for section in required_sections:
        pos = text.find(section)
        assert pos >= 0, f"missing section: {section}"
        assert pos > last_pos, f"section out of order: {section}"
        last_pos = pos

    assert "`N = 4(p1p2...pk) - 1`" in text, "proof must construct `N = 4(p1p2...pk) - 1`"
    assert "`N = 4(p1p2...pk) + 1`" not in text, "proof must not use the near-miss `+ 1` construction"
    assert (
        "Assume, for contradiction, that there are only finitely many primes congruent to `3 (mod 4)`." in text
    ), "finite-list contradiction assumption is missing"
    assert (
        "For each `i` with `1 <= i <= k`, the prime `pi` does not divide `N`." in text
    ), "proof must explicitly state that no listed `pi` divides `N`"
    assert (
        "At least one prime divisor of `N` is congruent to `3 (mod 4)`." in text
    ), "proof must explicitly show a prime divisor of N is 3 mod 4"
    assert (
        "Choose `q` to be a prime divisor of `N` such that `q ≡ 3 (mod 4)`." in text
    ), "proof must explicitly choose q with q ≡ 3 (mod 4)"
    assert "`q` is not equal to any of `p1, p2, ..., pk`." in text, "proof must show q is outside the original list"
    assert (
        "This contradicts the assumption that `p1, ..., pk` were all primes congruent to `3 (mod 4)`." in text
    ), "proof must state the contradiction explicitly"

    steps = re.findall(
        r"(Step \d+\n\nStatement:\n.*?\n\nJustification:\n.*?)(?=\nStep \d+\n\nStatement:|\n## 5\. Key Logical Claims)",
        text,
        flags=re.DOTALL,
    )
    assert len(steps) == 15, f"expected 15 proof steps, found {len(steps)}"
    for index, step in enumerate(steps, start=1):
        assert step.startswith(f"Step {index}\n\nStatement:\n"), f"malformed step header at step {index}"
        assert "\n\nJustification:\n" in step, f"missing justification at step {index}"


def test_math_trst_02_pis_contains_complete_contradiction_proof() -> None:
    text = _pis_path().read_text(encoding="utf-8")
    _assert_valid_math_proof_pis(text)


def test_math_trst_02_pis_validator_rejects_near_miss_proofs() -> None:
    text = _pis_path().read_text(encoding="utf-8")

    with pytest.raises(AssertionError, match=r"4\(p1p2\.\.\.pk\) - 1"):
        _assert_valid_math_proof_pis(text.replace("`N = 4(p1p2...pk) - 1`", "`N = 4(p1p2...pk) + 1`", 1))

    with pytest.raises(AssertionError, match=r"At least one prime divisor of `N` is congruent to `3 \(mod 4\)`\."):
        _assert_valid_math_proof_pis(
            text.replace(
                "At least one prime divisor of `N` is congruent to `3 (mod 4)`.",
                "At least one prime divisor of `N` exists.",
                1,
            )
        )

    with pytest.raises(AssertionError, match=r"no listed `pi` divides `N`"):
        _assert_valid_math_proof_pis(
            text.replace(
                "For each `i` with `1 <= i <= k`, the prime `pi` does not divide `N`.",
                "For each `i` with `1 <= i <= k`, the prime `pi` appears in the list.",
                1,
            )
        )
