"""
view_metrics.py

Human-friendly viewer for EvoGrid run outputs.

Features:
- Prints run configuration (run_config.csv or run_config.json if present)
- Prints metrics.csv in a transposed table (metrics as rows, generations as columns)
- Prints behaviors.csv (raw sample + per-generation mean summaries)
- Optionally generates plots into <run_dir>/plots when --plot is provided

Usage:
  # Use latest run under ../runs
  python scripts/view_metrics.py

  # View a specific metrics.csv
  python scripts/view_metrics.py /path/to/runs/20260205-123456/metrics.csv

  # Same, but also generate plots
  python scripts/view_metrics.py --plot /path/to/runs/20260205-123456/metrics.csv
"""

from __future__ import annotations

import sys
import os
import argparse
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

import matplotlib
matplotlib.use("Agg")  # headless save
import matplotlib.pyplot as plt


def _round_if_numeric(df: pd.DataFrame, cols, ndigits=2) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").round(ndigits)
    return df


def _print_df_table(title: str, df: pd.DataFrame, cols=None, head=7, tail=7):
    console = Console()
    if cols is None:
        cols = list(df.columns)

    cols = [c for c in cols if c in df.columns]
    if not cols:
        console.print(f"[yellow]No columns to display for {title}[/yellow]")
        return

    table = Table(title=title, show_header=True, header_style="bold")
    for c in cols:
        table.add_column(c, justify="right")

    show = pd.concat([df.head(head), df.tail(tail)]).drop_duplicates()
    for _, row in show.iterrows():
        table.add_row(*[str(row[c]) for c in cols])

    console.print(table)
    console.print()


def _print_df_table_transposed(
    title: str,
    df: pd.DataFrame,
    index_col: str,
    cols=None,
    head=7,
    tail=7,
    value_ndigits=None,
    max_col_width=14,
):
    console = Console()

    if index_col not in df.columns:
        console.print(f"[yellow]Cannot transpose {title}: missing index_col '{index_col}'[/yellow]")
        console.print()
        return

    if cols is None:
        cols = [c for c in df.columns if c != index_col]
    cols = [c for c in cols if c in df.columns and c != index_col]

    if not cols:
        console.print(f"[yellow]No columns to display for {title}[/yellow]")
        console.print()
        return

    show = pd.concat([df.head(head), df.tail(tail)]).drop_duplicates()

    idx_vals = show[index_col].tolist()
    idx_headers = [str(v) for v in idx_vals]

    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("metric", justify="left", no_wrap=True)

    for h in idx_headers:
        table.add_column(h[:max_col_width], justify="right", no_wrap=True)

    for metric in cols:
        row_vals = []
        for v in show[metric].tolist():
            if value_ndigits is not None:
                try:
                    fv = float(v)
                    if pd.notna(fv):
                        v = round(fv, value_ndigits)
                except Exception:
                    pass
            row_vals.append("" if pd.isna(v) else str(v))
        table.add_row(metric, *row_vals)

    console.print(table)
    console.print()


def print_config(run_dir: str):
    console = Console()

    cfg_csv = os.path.join(run_dir, "run_config.csv")
    if os.path.exists(cfg_csv):
        try:
            cfg = pd.read_csv(cfg_csv)

            table = Table(title="Run configuration", show_header=True, header_style="bold")
            cols = [c.lower() for c in cfg.columns]

            if {"section", "key", "value"}.issubset(set(cols)):
                colmap = {c.lower(): c for c in cfg.columns}
                section_col = colmap["section"]
                key_col = colmap["key"]
                value_col = colmap["value"]

                table.add_column("section", justify="left")
                table.add_column("key", justify="left")
                table.add_column("value", justify="right")

                for _, row in cfg.iterrows():
                    table.add_row(str(row[section_col]), str(row[key_col]), str(row[value_col]))

            else:
                table.add_column("key", justify="left")
                table.add_column("value", justify="right")

                if cfg.shape[1] >= 2:
                    kcol = cfg.columns[0]
                    vcol = cfg.columns[1]
                    for _, row in cfg.iterrows():
                        table.add_row(str(row[kcol]), str(row[vcol]))
                else:
                    for _, row in cfg.iterrows():
                        table.add_row(str(row.iloc[0]), "")

            console.print(table)
            console.print()
            return
        except Exception as e:
            console.print(f"[red]Failed to read run_config.csv:[/red] {e}")
            console.print()

    cfg_json = os.path.join(run_dir, "run_config.json")
    if os.path.exists(cfg_json):
        try:
            import json
            with open(cfg_json, "r", encoding="utf-8") as f:
                data = json.load(f)

            table = Table(title="Run configuration", show_header=True, header_style="bold")
            table.add_column("key", justify="left")
            table.add_column("value", justify="right")
            for k, v in sorted(data.items(), key=lambda kv: kv[0]):
                table.add_row(str(k), str(v))

            console.print(table)
            console.print()
        except Exception as e:
            console.print(f"[red]Failed to read run_config.json:[/red] {e}")
            console.print()


def _resolve_metric_columns(df: pd.DataFrame) -> list[str]:
    core = [
        "generation",
        "alive",
        "cohort_size",
        "cohort_survivors",
        "cohort_survival_rate",
        "shock_active",
        "mean_fitness",
        "std_fitness",
        "p90_fitness",
        "max_fitness",
        "mean_survival_steps_total",
        "deaths",
        "mean_energy_collected_total",
        "std_energy_collected_total",
        "mean_offspring_total",
        "mean_attacks_total",
        "successful_births",
        "failed_births",
        "injury_blocked_births",
        "idle_charge_total",
        "idle_charge_deaths",
        "mean_idle_charge_paid_total",
        "mean_injury",
        "p90_injury",
        "max_injury",
        "peak_injury",
        "mean_scar",
        "p90_scar",
        "max_scar",
        "birth_attempts",
        "birth_fail_no_space",
        "birth_fail_no_mate",
        "birth_fail_buffer",
        "birth_fail_injury_gate",
        "birth_fail_scar_gate",
        "birth_fail_conception",
        "birth_fail_gestation",
    ]

    legacy_map = {
        "mean_lifespan": "mean_survival_steps_total",
        "mean_energy_collected": "mean_energy_collected_total",
        "std_energy_collected": "std_energy_collected_total",
        "mean_offspring": "mean_offspring_total",
        "mean_attacks": "mean_attacks_total",
    }

    for old, new in legacy_map.items():
        if old in df.columns and new not in df.columns:
            df[new] = df[old]

    extra = [
        c for c in df.columns
        if (
            c.startswith("gen_")
            or c.startswith("genome_")
            or c.startswith("diversity_")
            or c.startswith("gru_")
        )
    ]

    cols = [c for c in core if c in df.columns]
    cols.extend(sorted(extra))
    return cols


def print_metrics(path: str):
    df = pd.read_csv(path)

    cols = _resolve_metric_columns(df)
    if not cols:
        raise SystemExit(f"No expected metric columns found in {path}. Found columns: {list(df.columns)}")

    df = df[cols].copy()

    round_cols = [
        "cohort_survival_rate",
        "mean_fitness",
        "std_fitness",
        "p90_fitness",
        "max_fitness",
        "mean_survival_steps_total",
        "mean_energy_collected_total",
        "std_energy_collected_total",
        "mean_offspring_total",
        "mean_attacks_total",
        "idle_charge_total",
        "mean_idle_charge_paid_total",
        "mean_injury",
        "p90_injury",
        "max_injury",
        "peak_injury",
        "mean_scar",
        "p90_scar",
        "max_scar",
    ]
    df = _round_if_numeric(df, round_cols, ndigits=2)

    _print_df_table_transposed(
        title=f"Metrics (transposed): {path}",
        df=df,
        index_col="generation",
        cols=cols,
        head=7,
        tail=7,
        value_ndigits=2,
    )


def _safe_rate(num: float, den: float) -> float:
    den = float(den)
    if den <= 0:
        return 0.0
    return float(num) / den


def print_behaviors(run_dir: str):
    beh_path = os.path.join(run_dir, "behaviors.csv")
    console = Console()

    if not os.path.exists(beh_path):
        console.print(f"[yellow]No behaviors.csv found in {run_dir}[/yellow]")
        console.print()
        return

    df = pd.read_csv(beh_path)

    desired_cols = [
        "generation",
        "agent_id",
        "cohort",
        "move_n",
        "move_s",
        "move_e",
        "move_w",
        "gather",
        "rest",
        "attack",
        "lifespan_steps",
        "energy_collected",
        "offspring",
        "attacks_made",
        "fitness",
        "alive_end",
        "eligible_for_selection",
        "kept_next",
        "injury_end",
        "injury_delta",
    ]
    cols = [c for c in desired_cols if c in df.columns]

    df = _round_if_numeric(df, ["energy_collected", "fitness", "injury_end", "injury_delta"], ndigits=2)
    _print_df_table(title=f"Behaviors (raw): {beh_path}", df=df, cols=cols, head=7, tail=7)

    if "generation" not in df.columns:
        console.print("[yellow]Behaviors summary skipped (missing generation column).[/yellow]")
        console.print()
        return

    tmp = df.copy()
    tmp["generation"] = pd.to_numeric(tmp["generation"], errors="coerce").fillna(0).astype(int)

    action_cols = [c for c in ["move_n", "move_s", "move_e", "move_w", "gather", "rest", "attack"] if c in tmp.columns]
    scalar_cols = [
        c for c in ["lifespan_steps", "energy_collected", "offspring", "attacks_made", "fitness", "injury_end", "injury_delta"]
        if c in tmp.columns
    ]
    flag_cols = [c for c in ["alive_end", "eligible_for_selection", "kept_next"] if c in tmp.columns]

    if action_cols:
        tmp["total_actions"] = tmp[action_cols].sum(axis=1)
        tmp["total_actions"] = tmp["total_actions"].where(tmp["total_actions"] > 0, 1.0)
        for c in action_cols:
            tmp[c + "_frac"] = tmp[c] / tmp["total_actions"]

    tmp["agent_rows"] = 1

    agg_map = {}
    for c in action_cols:
        fc = c + "_frac"
        if fc in tmp.columns:
            agg_map[fc] = "mean"
    for c in scalar_cols:
        agg_map[c] = "mean"
    for c in flag_cols:
        agg_map[c] = "mean"
    agg_map["agent_rows"] = "sum"

    gen_means = tmp.groupby("generation", as_index=False).agg(agg_map)
    gen_means.rename(columns={"agent_rows": "agents_logged"}, inplace=True)

    frac_cols = [c for c in gen_means.columns if c.endswith("_frac")]
    gen_means = _round_if_numeric(gen_means, frac_cols, ndigits=3)
    gen_means = _round_if_numeric(
        gen_means,
        [
            "lifespan_steps",
            "energy_collected",
            "offspring",
            "attacks_made",
            "fitness",
            "injury_end",
            "injury_delta",
            "alive_end",
            "eligible_for_selection",
            "kept_next",
        ],
        ndigits=2,
    )

    display_cols = ["generation", "agents_logged"]
    for c in ["gather_frac", "rest_frac", "attack_frac", "move_n_frac", "move_s_frac", "move_e_frac", "move_w_frac"]:
        if c in gen_means.columns:
            display_cols.append(c)
    for c in [
        "lifespan_steps",
        "energy_collected",
        "offspring",
        "attacks_made",
        "fitness",
        "injury_end",
        "injury_delta",
        "alive_end",
        "eligible_for_selection",
        "kept_next",
    ]:
        if c in gen_means.columns:
            display_cols.append(c)

    _print_df_table_transposed(
        title="Behavior summary (transposed, per generation means)",
        df=gen_means,
        index_col="generation",
        cols=[c for c in display_cols if c != "generation"],
        head=7,
        tail=7,
        value_ndigits=3,
    )


def _plot_line(df: pd.DataFrame, x: str, y: str, title: str, out_path: Path) -> None:
    if x not in df.columns or y not in df.columns:
        return
    xvals = pd.to_numeric(df[x], errors="coerce")
    yvals = pd.to_numeric(df[y], errors="coerce")
    if xvals.isna().all() or yvals.isna().all():
        return

    plt.figure()
    plt.plot(xvals, yvals)
    plt.title(title)
    plt.xlabel(x)
    plt.ylabel(y)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close()


def generate_plots(run_dir: str) -> None:
    console = Console()
    run_path = Path(run_dir)

    metrics_csv = run_path / "metrics.csv"
    if not metrics_csv.exists():
        console.print(f"[yellow]Plotting skipped: metrics.csv not found in {run_dir}[/yellow]")
        console.print()
        return

    try:
        df = pd.read_csv(metrics_csv)
    except Exception as e:
        console.print(f"[red]Plotting failed: cannot read metrics.csv:[/red] {e}")
        console.print()
        return

    plots_dir = run_path / "plots"

    _plot_line(df, "generation", "mean_fitness", "Mean fitness per generation", plots_dir / "fitness_mean.png")
    _plot_line(df, "generation", "max_fitness", "Max fitness per generation", plots_dir / "fitness_max.png")
    _plot_line(df, "generation", "p90_fitness", "P90 fitness per generation", plots_dir / "fitness_p90.png")

    _plot_line(df, "generation", "alive", "Alive (end of generation)", plots_dir / "alive.png")
    _plot_line(df, "generation", "deaths", "Deaths per generation", plots_dir / "deaths.png")
    _plot_line(df, "generation", "mean_survival_steps_total", "Mean survival steps", plots_dir / "survival_steps_mean.png")

    _plot_line(df, "generation", "cohort_size", "Cohort size (start-of-gen)", plots_dir / "cohort_size.png")
    _plot_line(df, "generation", "cohort_survival_rate", "Cohort survival rate", plots_dir / "cohort_survival_rate.png")

    _plot_line(df, "generation", "shock_active", "Shock active (0/1)", plots_dir / "shock_active.png")

    _plot_line(df, "generation", "mean_energy_collected_total", "Mean energy collected", plots_dir / "energy_collected_mean.png")
    _plot_line(df, "generation", "std_energy_collected_total", "Std energy collected", plots_dir / "energy_collected_std.png")

    _plot_line(df, "generation", "successful_births", "Successful births per generation", plots_dir / "births_success.png")
    _plot_line(df, "generation", "failed_births", "Failed births per generation", plots_dir / "births_failed.png")

    _plot_line(df, "generation", "birth_attempts", "Birth attempts per generation", plots_dir / "birth_attempts.png")
    _plot_line(df, "generation", "birth_fail_no_space", "Birth fails: no space", plots_dir / "birth_fail_no_space.png")
    _plot_line(df, "generation", "birth_fail_no_mate", "Birth fails: no mate", plots_dir / "birth_fail_no_mate.png")
    _plot_line(df, "generation", "birth_fail_buffer", "Birth fails: buffer", plots_dir / "birth_fail_buffer.png")
    _plot_line(df, "generation", "birth_fail_injury_gate", "Birth fails: injury gate", plots_dir / "birth_fail_injury_gate.png")
    _plot_line(df, "generation", "birth_fail_scar_gate", "Birth fails: scar gate", plots_dir / "birth_fail_scar_gate.png")
    _plot_line(df, "generation", "birth_fail_conception", "Birth fails: conception", plots_dir / "birth_fail_conception.png")
    _plot_line(df, "generation", "birth_fail_gestation", "Birth fails: gestation", plots_dir / "birth_fail_gestation.png")

    _plot_line(df, "generation", "mean_injury", "Mean injury", plots_dir / "injury_mean.png")
    _plot_line(df, "generation", "p90_injury", "P90 injury", plots_dir / "injury_p90.png")
    _plot_line(df, "generation", "max_injury", "Max injury", plots_dir / "injury_max.png")
    _plot_line(df, "generation", "peak_injury", "Peak injury (any time in gen)", plots_dir / "injury_peak.png")

    _plot_line(df, "generation", "mean_scar", "Mean scar", plots_dir / "scar_mean.png")
    _plot_line(df, "generation", "p90_scar", "P90 scar", plots_dir / "scar_p90.png")
    _plot_line(df, "generation", "max_scar", "Max scar", plots_dir / "scar_max.png")

    console.print(f"[green]Saved plots to:[/green] {plots_dir}")
    console.print()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="View run metrics and behaviors (optionally plot).")
    p.add_argument(
        "metrics_path",
        nargs="?",
        default=None,
        help="Path to metrics.csv. If omitted, uses latest run under ../runs.",
    )
    p.add_argument(
        "--plot",
        action="store_true",
        help="Generate PNG plots into <run_dir>/plots (default: off).",
    )
    return p.parse_args(argv)


def main(metrics_path: str, *, do_plots: bool) -> None:
    run_dir = os.path.dirname(metrics_path)
    print_config(run_dir)
    print_metrics(metrics_path)
    print_behaviors(run_dir)

    if do_plots:
        generate_plots(run_dir)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])

    ROOT = Path(__file__).resolve().parent.parent
    runs_dir = ROOT / "runs"

    if args.metrics_path is not None:
        metrics_path = Path(args.metrics_path)
        if not metrics_path.is_absolute():
            metrics_path = (ROOT / metrics_path).resolve()
        main(str(metrics_path), do_plots=bool(args.plot))
        raise SystemExit(0)

    if not runs_dir.exists():
        raise SystemExit(f"No runs directory found at: {runs_dir}")

    runs = sorted([p for p in runs_dir.iterdir() if p.is_dir()])
    if not runs:
        raise SystemExit("No runs found.")

    latest = runs[-1].name
    path = runs_dir / latest / "metrics.csv"
    main(str(path), do_plots=bool(args.plot))
