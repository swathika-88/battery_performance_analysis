"""
Tool: compute_revenue_summary
==============================
Computes revenue statistics for a given scenario (historical / perfect)
and optional schedule type (expected / cleared).

Returns a structured dict suitable for LLM consumption.
"""

import pandas as pd
import numpy as np
from typing import Optional


def compute_revenue_summary(
    df: pd.DataFrame,
    scenario: str,
    schedule_type: Optional[str] = None,
) -> dict:
    """
    Compute a revenue summary for a given scenario.

    Parameters
    ----------
    df : pd.DataFrame
        The full battery interval dataset.
    scenario : str
        One of 'historical' or 'perfect' (maps to SCENARIO_NAME column).
    schedule_type : str, optional
        One of 'expected' or 'cleared'. If None, all schedule types included.

    Returns
    -------
    dict
        {
            "scenario": str,
            "schedule_type": str | "all",
            "total_revenue": float,
            "mean_interval_revenue": float,
            "std_interval_revenue": float,
            "positive_revenue_intervals": int,
            "negative_revenue_intervals": int,
            "zero_revenue_intervals": int,
            "total_intervals": int,
            "revenue_per_hour": list[dict],   # hourly aggregation
            "top5_revenue_intervals": list[dict],
            "bottom5_revenue_intervals": list[dict],
        }
    """
    # ── Filter -----------------------------------------------------------
    mask = df["SCENARIO_NAME"].str.lower() == scenario.lower()
    if schedule_type:
        mask &= df["SCHEDULE_TYPE"].str.lower() == schedule_type.lower()

    subset = df[mask].copy()

    if subset.empty:
        return {
            "scenario": scenario,
            "schedule_type": schedule_type or "all",
            "error": f"No data found for scenario='{scenario}' schedule_type='{schedule_type}'",
        }

    # ── Basic revenue stats ----------------------------------------------
    revenues = subset["REVENUE_ENERGY"].fillna(0.0)

    total_revenue = float(revenues.sum())
    mean_rev = float(revenues.mean())
    std_rev = float(revenues.std())

    pos_count = int((revenues > 0).sum())
    neg_count = int((revenues < 0).sum())
    zero_count = int((revenues == 0).sum())

    # ── Hourly aggregation -----------------------------------------------
    subset = subset.copy()
    subset["_hour"] = pd.to_datetime(subset["START_DATETIME"]).dt.floor("h")
    hourly = (
        subset.groupby("_hour")["REVENUE_ENERGY"]
        .sum()
        .reset_index()
        .rename(columns={"_hour": "hour", "REVENUE_ENERGY": "revenue"})
    )
    hourly["hour"] = hourly["hour"].astype(str)
    revenue_per_hour = hourly.to_dict(orient="records")

    # ── Top / bottom intervals -------------------------------------------
    top5 = (
        subset.nlargest(5, "REVENUE_ENERGY")[
            ["START_DATETIME", "REVENUE_ENERGY", "PRICE_ENERGY",
             "CHARGE_ENERGY", "DISCHARGE_ENERGY", "SOC"]
        ]
        .assign(START_DATETIME=lambda x: x["START_DATETIME"].astype(str))
        .to_dict(orient="records")
    )
    bottom5 = (
        subset.nsmallest(5, "REVENUE_ENERGY")[
            ["START_DATETIME", "REVENUE_ENERGY", "PRICE_ENERGY",
             "CHARGE_ENERGY", "DISCHARGE_ENERGY", "SOC"]
        ]
        .assign(START_DATETIME=lambda x: x["START_DATETIME"].astype(str))
        .to_dict(orient="records")
    )

    return {
        "scenario": scenario,
        "schedule_type": schedule_type or "all",
        "total_revenue": round(total_revenue, 4),
        "mean_interval_revenue": round(mean_rev, 4),
        "std_interval_revenue": round(std_rev, 4),
        "positive_revenue_intervals": pos_count,
        "negative_revenue_intervals": neg_count,
        "zero_revenue_intervals": zero_count,
        "total_intervals": len(subset),
        "revenue_per_hour": revenue_per_hour,
        "top5_revenue_intervals": top5,
        "bottom5_revenue_intervals": bottom5,
    }
