from __future__ import annotations

from pathlib import Path

from infra.bin import commparse_real_life_eval


def test_collect_invented_prompts_loads_corpus(repo_root: Path = Path("/home/user/causyl")) -> None:
    rows = commparse_real_life_eval.collect_invented_prompts(repo_root, limit=10)
    assert rows
    assert rows[0]["source"] == "invented"
    assert rows[0]["prompt"]


def test_evaluate_commparse_prompts_invented_plan_mode() -> None:
    repo_root = Path("/home/user/causyl")
    payload = commparse_real_life_eval.evaluate_commparse_prompts(repo_root, limit=8, execute=False, prompt_set="invented")
    assert payload["prompt_set"] == "invented"
    assert payload["summary"]["prompt_count"] == 8
    assert payload["summary"]["source_counts"]["invented"] == 8
    assert payload["rows"]


def test_evaluate_prompt_splits_chained_front_door_prompt() -> None:
    repo_root = Path("/home/user/causyl")
    row = commparse_real_life_eval._evaluate_prompt("do commit then archive debug then archive weekly", repo_root=repo_root, execute=False)
    assert row["commparse_shape"] == "sequence_request"
    assert row["supported"] is True
    assert len(row["chain_steps"]) == 3


def test_evaluate_prompt_uses_front_door_help_intercept() -> None:
    repo_root = Path("/home/user/causyl")
    row = commparse_real_life_eval._evaluate_prompt("help archive", repo_root=repo_root, execute=False)
    assert row["supported"] is True
    assert row["front_door_builtin"] is True
