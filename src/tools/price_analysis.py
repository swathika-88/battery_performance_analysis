"""
Tool: identify_high_price_intervals
======================================
Finds intervals where the market price exceeds a given percentile threshold
and examines whether the battery was dispatching (discharging) during those
high-value windows. Also flags low-price intervals where the battery should
have been charging instead.

Returns a structured dict suitable for LLM consumption.
"""

import pandas as pd
import numpy as np
from typing import Optional


def identify_high_price_intervals(
    df: pd.DataFrame,
    threshold_percentile: float = 75.0,
    scenario: str = "historical",
    schedule_type: Optional[str] = "cleared",
) -> dict:
    """
    Identify high/low price intervals and assess battery dispatch alignment.

    Parameters
    ----------
    df : pd.DataFrame
        The full battery interval dataset.
    threshold_percentile : float
        Market-price percentile above which an interval is considered 'high-value'.
    scenario : str
        One of 'historical' or 'perfect'.
    schedule_type : str, optional
        One of 'expected' or 'cleared'. Default 'cleared'.

    Returns
    -------
    dict
        {
            "scenario": str,
            "schedule_type": str,
            "price_threshold": float,
            "high_price_summary": {
                "interval_count": int,
                "mean_price": float,
                "total_missed_discharge_intervals": int,
                "total_idle_intervals": int,
                "total_charging_intervals": int,   # bad timing
                "revenue_in_high_price_windows": float,
            },
            "low_price_summary": { ... },
            "missed_opportunities": list[dict],    # sorted by price desc
            "bad_timing_intervals": list[dict],    # charging when price high
            "price_distribution": {
                "p25": float, "p50": float, "p75": float,
                "p90": float, "p95": float, "p99": float, "max": float,
            },
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

    prices = subset["PRICE_ENERGY"].fillna(0.0)

    # ── Price percentiles ------------------------------------------------
    price_dist = {
        "p25": round(float(prices.quantile(0.25)), 4),
        "p50": round(float(prices.quantile(0.50)), 4),
        "p75": round(float(prices.quantile(0.75)), 4),
        "p90": round(float(prices.quantile(0.90)), 4),
        "p95": round(float(prices.quantile(0.95)), 4),
        "p99": round(float(prices.quantile(0.99)), 4),
        "max": round(float(prices.max()), 4),
        "min": round(float(prices.min()), 4),
        "mean": round(float(prices.mean()), 4),
    }

    price_thresh = float(prices.quantile(threshold_percentile / 100.0))

    # ── High-price window analysis ----------------------------------------
    high_mask = prices >= price_thresh
    high_df = subset[high_mask].copy()
    disch = high_df["DISCHARGE_ENERGY"].fillna(0.0)
    charg = high_df["CHARGE_ENERGY"].fillna(0.0)

    missed_discharge = high_df[disch == 0].copy()  # idle or charging when price high
    bad_timing = high_df[charg > 0].copy()         # actively charging when price high

    rev_high = float(high_df["REVENUE_ENERGY"].fillna(0.0).sum())

    high_summary = {
        "interval_count": int(high_mask.sum()),
        "mean_price": round(float(high_df["PRICE_ENERGY"].mean()), 4),
        "total_missed_discharge_intervals": int(len(missed_discharge)),
        "total_idle_intervals": int(
            ((disch == 0) & (charg == 0)).sum()
        ),
        "total_charging_intervals": int(len(bad_timing)),
        "revenue_in_high_price_windows": round(rev_high, 4),
    }

    # ── Low-price window analysis ----------------------------------------
    low_mask = prices <= float(prices.quantile(0.25))
    low_df = subset[low_mask].copy()
    low_disch = low_df["DISCHARGE_ENERGY"].fillna(0.0)
    low_charg = low_df["CHARGE_ENERGY"].fillna(0.0)

    missed_charge = low_df[low_charg == 0].copy()  # not charging when price low
    discharging_low = low_df[low_disch > 0].copy()  # discharging when price low

    rev_low = float(low_df["REVENUE_ENERGY"].fillna(0.0).sum())

    low_summary = {
        "interval_count": int(low_mask.sum()),
        "mean_price": round(float(low_df["PRICE_ENERGY"].mean()), 4),
        "total_missed_charge_intervals": int(len(missed_charge)),
        "total_discharging_intervals": int(len(discharging_low)),
        "revenue_in_low_price_windows": round(rev_low, 4),
    }

    # ── Missed opportunity details (up to 10 worst) ----------------------
    mo_cols = ["START_DATETIME", "PRICE_ENERGY", "DISCHARGE_ENERGY",
               "CHARGE_ENERGY", "SOC", "REVENUE_ENERGY"]
    missed_opps = (
        missed_discharge.nlargest(10, "PRICE_ENERGY")[mo_cols]
        .assign(START_DATETIME=lambda x: x["START_DATETIME"].astype(str))
        .to_dict(orient="records")
    )
    bad_timing_list = (
        bad_timing.nlargest(10, "PRICE_ENERGY")[mo_cols]
        .assign(START_DATETIME=lambda x: x["START_DATETIME"].astype(str))
        .to_dict(orient="records")
    )

    return {
        "scenario": scenario,
        "schedule_type": schedule_type or "all",
        "price_threshold": round(price_thresh, 4),
        "high_price_summary": high_summary,
        "low_price_summary": low_summary,
        "missed_opportunities": missed_opps,
        "bad_timing_intervals": bad_timing_list,
        "price_distribution": price_dist,
    }
