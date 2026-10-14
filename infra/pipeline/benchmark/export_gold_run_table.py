from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_reference_targets(gpt_answers_dir: Path) -> Dict[str, List[str]]:
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required to read gpt answer YAML files") from exc

    out: Dict[str, List[str]] = {}
    for path in sorted(gpt_answers_dir.glob("*__reference_trace.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        uid = str(((data.get("case") or {}).get("id")) or path.stem.replace("__reference_trace", "")).strip()
        impl = []
        for entry in list(((data.get("candidate_files") or {}).get("implementation_targets")) or []):
            if isinstance(entry, Mapping):
                text = str(entry.get("path") or "").strip()
                if text:
                    impl.append(text)
        out[uid] = impl
    return out


def _rows_by_uid(score_json: Path) -> Dict[str, Dict[str, Any]]:
    data = _load_json(score_json)
    return {str(row.get("pis_uid") or ""): dict(row) for row in list(data.get("results") or []) if str(row.get("pis_uid") or "").strip()}


def _fmt_list(values: Iterable[Any]) -> str:
    return " | ".join(str(value or "").strip() for value in values if str(value or "").strip())


def build_rows(
    *,
    gpt_answers_dir: Path,
    run1_json: Path,
    run2_json: Path,
) -> List[Dict[str, str]]:
    refs = _load_reference_targets(gpt_answers_dir)
    run1 = _rows_by_uid(run1_json)
    run2 = _rows_by_uid(run2_json)
    uids = sorted(set(refs) | set(run1) | set(run2))
    rows: List[Dict[str, str]] = []
    for uid in uids:
        run1_row = run1.get(uid, {})
        run2_row = run2.get(uid, {})
        title = str(run2_row.get("title") or run1_row.get("title") or uid).strip()
        run1_score = run1_row.get("score", "")
        run2_score = run2_row.get("score", "")
        try:
            score_delta = round(float(run2_score) - float(run1_score), 2)
        except Exception:
            score_delta = ""
        rows.append(
            {
                "pis_uid": uid,
                "title": title,
                "run1_score": str(run1_score),
                "run2_score": str(run2_score),
                "score_delta": str(score_delta),
                "gpt_targets": _fmt_list(refs.get(uid, [])),
                "run1_targets": _fmt_list(((run1_row.get("observed") or {}).get("implementation_targets") or [])),
                "run1_selected": _fmt_list(((run1_row.get("observed") or {}).get("selected_files") or [])),
                "run2_targets": _fmt_list(((run2_row.get("observed") or {}).get("implementation_targets") or [])),
                "run2_selected": _fmt_list(((run2_row.get("observed") or {}).get("selected_files") or [])),
            }
        )
    return rows


def write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "pis_uid",
        "title",
        "run1_score",
        "run2_score",
        "score_delta",
        "gpt_targets",
        "run1_targets",
        "run1_selected",
        "run2_targets",
        "run2_selected",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_md(path: Path, rows: List[Dict[str, str]], *, run1_label: str, run2_label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Gold Run Comparison Table",
        "",
        f"- Run 1: `{run1_label}`",
        f"- Run 2: `{run2_label}`",
        f"- Cases: `{len(rows)}`",
        "",
        "| PIS | Run1 Score | Run2 Score | Delta | GPT Targets | Run1 | Run2 |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for row in rows:
        run1_cell = row["run1_targets"] or "-"
        run2_cell = row["run2_targets"] or "-"
        lines.append(
            f"| `{row['pis_uid']}` | `{row['run1_score'] or '-'}` | `{row['run2_score'] or '-'}` | `{row['score_delta'] or '-'}` | {row['gpt_targets'] or '-'} | {run1_cell} | {run2_cell} |"
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a per-PIS table comparing GPT targets with two scored runs.")
    parser.add_argument("--gpt-answers-dir", default="tests/benchmarks/pipeline/benchmark-gpt/gold_large/gpt_answers")
    parser.add_argument("--run1-json", required=True)
    parser.add_argument("--run2-json", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-md", required=True)
    args = parser.parse_args(argv)

    rows = build_rows(
        gpt_answers_dir=Path(args.gpt_answers_dir),
        run1_json=Path(args.run1_json),
        run2_json=Path(args.run2_json),
    )
    write_csv(Path(args.out_csv), rows)
    write_md(Path(args.out_md), rows, run1_label=args.run1_json, run2_label=args.run2_json)
    print(json.dumps({"rows": len(rows), "out_csv": args.out_csv, "out_md": args.out_md}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
