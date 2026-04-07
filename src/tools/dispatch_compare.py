"""
Tool: compare_dispatch
========================
Side-by-side comparison between historical and perfect-foresight scenarios.
Highlights intervals where dispatch decisions diverge and quantifies the impact.

Returns a structured dict suitable for LLM consumption.
"""

import pandas as pd
import numpy as np
from typing import Optional


def compare_dispatch(
    df: pd.DataFrame,
    schedule_type: str = "cleared",
    metric: str = "all",
) -> dict:
    """
    Compare dispatch behaviour between historical and perfect scenarios.

    Parameters
    ----------
    df : pd.DataFrame
        The full battery interval dataset.
    schedule_type : str
        One of 'expected' or 'cleared'. Default 'cleared'.
    metric : str
        'charge', 'discharge', 'revenue', or 'all'.

    Returns
    -------
    dict
        {
            "schedule_type": str,
            "intervals_compared": int,
            "energy_summary": {
                "historical_total_charge_MWh": float,
                "perfect_total_charge_MWh": float,
                "historical_total_discharge_MWh": float,
                "perfect_total_discharge_MWh": float,
                "charge_gap_MWh": float,
                "discharge_gap_MWh": float,
            },
            "revenue_summary": {
                "historical_total": float,
                "perfect_total": float,
                "absolute_gap": float,
                "relative_gap_pct": float,
            },
            "dispatch_alignment": {
                "aligned_intervals": int,          # both dispatch or both idle
                "historical_idle_perfect_active": int,
                "historical_active_perfect_idle": int,
                "both_idle": int,
            },
            "largest_divergence_intervals": list[dict],  # top 10 by revenue diff
            "hourly_comparison": list[dict],
        }
    """
    st = schedule_type.lower()

    hist = df[
        (df["SCENARIO_NAME"].str.lower() == "historical") &
        (df["SCHEDULE_TYPE"].str.lower() == st)
    ].copy()

    perf = df[
        (df["SCENARIO_NAME"].str.lower() == "perfect") &
        (df["SCHEDULE_TYPE"].str.lower() == st)
    ].copy()

    if hist.empty or perf.empty:
        return {
            "schedule_type": schedule_type,
            "error": "Insufficient data for one or both scenarios.",
        }

    # ── Merge on START_DATETIME ------------------------------------------
    hist = hist.set_index("START_DATETIME")
    perf = perf.set_index("START_DATETIME")

    common_idx = hist.index.intersection(perf.index)
    hist_c = hist.loc[common_idx].copy()
    perf_c = perf.loc[common_idx].copy()

    n_intervals = len(common_idx)

    # ── Energy totals ----------------------------------------------------
    hist_charge = float(hist_c["CHARGE_ENERGY"].fillna(0).sum())
    perf_charge = float(perf_c["CHARGE_ENERGY"].fillna(0).sum())
    hist_disch = float(hist_c["DISCHARGE_ENERGY"].fillna(0).sum())
    perf_disch = float(perf_c["DISCHARGE_ENERGY"].fillna(0).sum())

    energy_summary = {
        "historical_total_charge_MWh": round(hist_charge, 4),
        "perfect_total_charge_MWh": round(perf_charge, 4),
        "historical_total_discharge_MWh": round(hist_disch, 4),
        "perfect_total_discharge_MWh": round(perf_disch, 4),
        "charge_gap_MWh": round(perf_charge - hist_charge, 4),
        "discharge_gap_MWh": round(perf_disch - hist_disch, 4),
    }

    # ── Revenue totals ---------------------------------------------------
    hist_rev = float(hist_c["REVENUE_ENERGY"].fillna(0).sum())
    perf_rev = float(perf_c["REVENUE_ENERGY"].fillna(0).sum())
    abs_gap = perf_rev - hist_rev
    rel_gap = (abs_gap / abs(perf_rev) * 100.0) if perf_rev != 0 else float("nan")

    revenue_summary = {
        "historical_total": round(hist_rev, 4),
        "perfect_total": round(perf_rev, 4),
        "absolute_gap": round(abs_gap, 4),
        "relative_gap_pct": round(rel_gap, 2),
    }

    # ── Dispatch alignment -----------------------------------------------
    hist_active = (hist_c["DISCHARGE_ENERGY"].fillna(0) > 0) | (hist_c["CHARGE_ENERGY"].fillna(0) > 0)
    perf_active = (perf_c["DISCHARGE_ENERGY"].fillna(0) > 0) | (perf_c["CHARGE_ENERGY"].fillna(0) > 0)

    aligned = int((hist_active == perf_active).sum())
    hist_idle_perf_act = int((~hist_active & perf_active).sum())
    hist_act_perf_idle = int((hist_active & ~perf_active).sum())
    both_idle = int((~hist_active & ~perf_active).sum())

    dispatch_alignment = {
        "aligned_intervals": aligned,
        "historical_idle_perfect_active": hist_idle_perf_act,
        "historical_active_perfect_idle": hist_act_perf_idle,
        "both_idle": both_idle,
    }

    # ── Largest divergence intervals -------------------------------------
    merged = pd.DataFrame({
        "START_DATETIME": common_idx,
        "hist_revenue": hist_c["REVENUE_ENERGY"].fillna(0).values,
        "perf_revenue": perf_c["REVENUE_ENERGY"].fillna(0).values,
        "hist_discharge": hist_c["DISCHARGE_ENERGY"].fillna(0).values,
        "perf_discharge": perf_c["DISCHARGE_ENERGY"].fillna(0).values,
        "hist_charge": hist_c["CHARGE_ENERGY"].fillna(0).values,
        "perf_charge": perf_c["CHARGE_ENERGY"].fillna(0).values,
        "price": hist_c["PRICE_ENERGY"].fillna(0).values,
    })
    merged["revenue_diff"] = merged["perf_revenue"] - merged["hist_revenue"]
    merged["START_DATETIME"] = merged["START_DATETIME"].astype(str)

    top_divergence = (
        merged.nlargest(10, "revenue_diff")
        .to_dict(orient="records")
    )

    # ── Hourly comparison -----------------------------------------------
    hist_c2 = hist_c.copy()
    hist_c2["_hour"] = pd.to_datetime(hist_c2.index).floor("h")
    perf_c2 = perf_c.copy()
    perf_c2["_hour"] = pd.to_datetime(perf_c2.index).floor("h")

    h_hourly = hist_c2.groupby("_hour")["REVENUE_ENERGY"].sum().rename("hist_revenue")
    p_hourly = perf_c2.groupby("_hour")["REVENUE_ENERGY"].sum().rename("perf_revenue")

    hourly = pd.concat([h_hourly, p_hourly], axis=1).fillna(0)
    hourly["gap"] = hourly["perf_revenue"] - hourly["hist_revenue"]
    hourly.index = hourly.index.astype(str)
    hourly_list = hourly.reset_index().rename(columns={"_hour": "hour"}).to_dict(orient="records")

    return {
        "schedule_type": schedule_type,
        "intervals_compared": n_intervals,
        "energy_summary": energy_summary,
        "revenue_summary": revenue_summary,
        "dispatch_alignment": dispatch_alignment,
        "largest_divergence_intervals": top_divergence,
        "hourly_comparison": hourly_list,
    }
