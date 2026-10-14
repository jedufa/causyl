#!/usr/bin/env python3
import sys
from pathlib import Path
import pandas as pd


def main(run_dir: str):
    run_dir = Path(run_dir)
    clustered = run_dir / "behaviors_clustered.csv"
    raw = run_dir / "behaviors.csv"

    if clustered.exists():
        df = pd.read_csv(clustered)
    elif raw.exists():
        raise SystemExit(
            f"Missing {clustered}. Run: python tools/analysis/cluster_behaviors.py {run_dir} 6"
        )
    else:
        raise SystemExit(f"Missing: {raw}")

    required = {"generation", "agent_id", "cluster"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Missing columns in {clustered}: {sorted(missing)}")

    # Normalize dtypes
    df["generation"] = pd.to_numeric(df["generation"], errors="coerce").fillna(0).astype(int)
    df["agent_id"] = pd.to_numeric(df["agent_id"], errors="coerce").fillna(-1).astype(int)
    df["cluster"] = pd.to_numeric(df["cluster"], errors="coerce").fillna(-1).astype(int)

    df = df[(df["agent_id"] >= 0) & (df["cluster"] >= 0)]
    if df.empty:
        raise SystemExit("No usable rows after parsing agent_id/cluster.")

    # Build presence sets per generation: who is present at gen g?
    gens = sorted(df["generation"].unique().tolist())
    by_gen = {g: set(df.loc[df["generation"] == g, "agent_id"].tolist()) for g in gens}

    # For each agent row at gen g, mark if they appear at gen g+1
    # (This measures "survived across the boundary" i.e. not culled and not dead.)
    survived_rows = []
    for g in gens:
        if (g + 1) not in by_gen:
            continue
        alive_next = by_gen[g + 1]
        cur = df[df["generation"] == g][["generation", "agent_id", "cluster"]].copy()
        cur["survive_next"] = cur["agent_id"].isin(alive_next).astype(int)
        survived_rows.append(cur)

    if not survived_rows:
        raise SystemExit("Not enough generations to compute survival (need at least 2).")

    surv = pd.concat(survived_rows, ignore_index=True)

    # Survival rate per cluster per generation
    rates = (
        surv.groupby(["generation", "cluster"])["survive_next"]
            .agg(n="size", survive="sum", p_survive_next="mean")
            .reset_index()
    )
    rates["p_survive_next"] = (rates["p_survive_next"] * 100.0).round(2)

    # Overall per-cluster survival rate
    overall = (
        surv.groupby("cluster")["survive_next"]
            .agg(n="size", survive="sum", p_survive_next="mean")
            .reset_index()
            .sort_values("p_survive_next", ascending=False)
    )
    overall["p_survive_next"] = (overall["p_survive_next"] * 100.0).round(2)

    print("\nPer-cluster survival rate to next generation (overall):")
    print(overall.to_string(index=False))

    # Optional: join with fitness means if present
    if "fitness" in df.columns:
        df["fitness"] = pd.to_numeric(df["fitness"], errors="coerce").fillna(0.0)
        fit = df.groupby("cluster")["fitness"].mean().reset_index().rename(columns={"fitness": "mean_fitness"})
        merged = overall.merge(fit, on="cluster", how="left").sort_values("p_survive_next", ascending=False)
        merged["mean_fitness"] = merged["mean_fitness"].round(3)
        print("\nSurvival rate with mean fitness by cluster:")
        print(merged.to_string(index=False))

    out = run_dir / "cluster_survival.csv"
    rates.to_csv(out, index=False)
    print(f"\nWrote: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python tools/analysis/cluster_survival.py runs/<run_id>")
    main(sys.argv[1])
