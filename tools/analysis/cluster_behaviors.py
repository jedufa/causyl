# tools/analysis/cluster_behaviors.py
import sys
from pathlib import Path
import pandas as pd
import numpy as np

from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans


def main(run_dir: str, k: int = 6):
    run_dir = Path(run_dir)
    path = run_dir / "behaviors.csv"
    if not path.exists():
        raise SystemExit(f"Missing: {path}")

    df = pd.read_csv(path)

    # numeric + generation int
    df["generation"] = pd.to_numeric(df["generation"], errors="coerce").fillna(0).astype(int)

    # action cols
    action_cols = ["move_n", "move_s", "move_e", "move_w", "gather", "rest", "attack"]
    for c in action_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # action fractions
    df["total_actions"] = df[action_cols].sum(axis=1).clip(lower=1.0)
    for c in action_cols:
        df[c + "_frac"] = df[c] / df["total_actions"]

    # merge moves
    df["move_frac"] = df[["move_n_frac", "move_s_frac", "move_e_frac", "move_w_frac"]].sum(axis=1)

    # outcomes (now includes fitness if present)
    outcome_cols = ["lifespan_steps", "energy_collected", "offspring", "attacks_made", "fitness"]
    for c in outcome_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # features
    feat_cols = [
        "gather_frac", "rest_frac", "attack_frac", "move_frac",
        "lifespan_steps", "energy_collected", "offspring", "attacks_made",
    ]
    if "fitness" in df.columns:
        feat_cols.append("fitness")

    feat_cols = [c for c in feat_cols if c in df.columns]
    if not feat_cols:
        raise SystemExit(f"No feature columns found. Available columns: {list(df.columns)}")

    X = df[feat_cols].to_numpy(dtype=np.float32)

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)

    km = KMeans(n_clusters=k, n_init=10, random_state=0)
    df["cluster"] = km.fit_predict(Xs)

    # cluster centers in original units
    centers = scaler.inverse_transform(km.cluster_centers_)
    centers_df = pd.DataFrame(centers, columns=feat_cols)

    print("\nCluster centers (approx phenotype means):")
    print(centers_df.round(3).to_string(index=True))

    # extra: per-cluster mean outcomes (especially fitness)
    summary_cols = ["lifespan_steps", "energy_collected", "offspring", "attacks_made"]
    if "fitness" in df.columns:
        summary_cols.append("fitness")
    summary_cols = [c for c in summary_cols if c in df.columns]

    if summary_cols:
        cl_summary = df.groupby("cluster")[summary_cols].mean().reset_index()
        if "fitness" in cl_summary.columns:
            cl_summary = cl_summary.sort_values("fitness", ascending=False)

        print("\nCluster outcome means (higher fitness = selected):")
        print(cl_summary.round(3).to_string(index=False))

    # phenotype frequencies over generations
    freq = (
        df.groupby(["generation", "cluster"])
          .size()
          .rename("count")
          .reset_index()
    )
    total = freq.groupby("generation")["count"].transform("sum")
    freq["pct"] = (freq["count"] / total * 100.0).round(2)

    gmin = int(df["generation"].min())
    print("\nPhenotype frequencies (first 5 generations):")
    print(freq[freq["generation"] < gmin + 5].to_string(index=False))

    last_gen = int(df["generation"].max())
    print(f"\nPhenotype frequencies (last generation = {last_gen}):")
    print(
        freq[freq["generation"] == last_gen]
        .sort_values("pct", ascending=False)
        .to_string(index=False)
    )

    # save for later plotting
    out = run_dir / "behaviors_clustered.csv"
    df.to_csv(out, index=False)
    print(f"\nWrote: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python tools/analysis/cluster_behaviors.py runs/<run_id> [k]")
    run_dir = sys.argv[1]
    k = int(sys.argv[2]) if len(sys.argv) >= 3 else 6
    main(run_dir, k=k)
