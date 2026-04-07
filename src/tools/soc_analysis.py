"""
Tool: analyze_state_of_charge
================================
Analyzes the battery's State of Charge (SOC) profile to identify
whether energy constraints (too full / too empty) caused missed dispatch
opportunities during high-price windows.

Returns a structured dict suitable for LLM consumption.
"""

import pandas as pd
import numpy as np
from typing import Optional


def analyze_state_of_charge(
    df: pd.DataFrame,
    scenario: str = "historical",
    schedule_type: Optional[str] = "cleared",
    full_threshold: float = 0.90,
    empty_threshold: float = 0.10,
) -> dict:
    """
    Analyze SOC profile and identify energy-constraint missed opportunities.

    Parameters
    ----------
    df : pd.DataFrame
        The full battery interval dataset.
    scenario : str
        One of 'historical' or 'perfect'.
    schedule_type : str, optional
        One of 'expected' or 'cleared'. Default 'cleared'.
    full_threshold : float
        Fraction of max SOC above which battery is considered 'full'. Default 0.90.
    empty_threshold : float
        Fraction of max SOC below which battery is considered 'empty'. Default 0.10.

    Returns
    -------
    dict
        {
            "scenario": str,
            "schedule_type": str,
            "soc_statistics": {
                "min": float, "max": float,
                "mean": float, "std": float,
                "soc_at_start": float,
                "soc_at_end": float,
            },
            "constraint_analysis": {
                "near_full_intervals": int,       # SOC >= full_threshold * max_soc
                "near_empty_intervals": int,      # SOC <= empty_threshold * max_soc
                "near_full_with_high_price": int, # missed discharge due to full
                "near_empty_with_low_price": int, # missed charge due to empty? (inverted)
            },
            "soc_regime_breakdown": {
                "high_soc_pct": float,
                "medium_soc_pct": float,
                "low_soc_pct": float,
            },
            "hourly_soc_profile": list[dict],
            "critical_constraint_intervals": list[dict],  # top 10 most costly
        }
    """
    # ── Filter -----------------------------------------------------------
    mask = df["SCENARIO_NAME"].str.lower() == scenario.lower()
    if schedule_type:
        mask &= df["SCHEDULE_TYPE"].str.lower() == schedule_type.lower()

    subset = df[mask].copy().sort_values("START_DATETIME")

    if subset.empty:
        return {
            "scenario": scenario,
            "schedule_type": schedule_type or "all",
            "error": f"No data for scenario='{scenario}' schedule_type='{schedule_type}'",
        }

    soc = subset["SOC"].fillna(method="ffill").fillna(0.0)
    max_soc = float(soc.max())
    min_soc = float(soc.min())
    mean_soc = float(soc.mean())
    std_soc = float(soc.std())

    soc_start = float(soc.iloc[0])
    soc_end = float(soc.iloc[-1])

    # ── Constraint thresholds (absolute) ---------------------------------
    full_abs = full_threshold * max_soc
    empty_abs = empty_threshold * max_soc if max_soc > 0 else 0.0

    near_full = (soc >= full_abs).sum()
    near_empty = (soc <= empty_abs).sum()

    # ── Price-weighted constraint analysis --------------------------------
    prices = subset["PRICE_ENERGY"].fillna(0.0)
    price_p75 = float(prices.quantile(0.75))
    price_p25 = float(prices.quantile(0.25))

    # Full battery + high price → couldn't discharge more
    near_full_high_price = int(
        ((soc >= full_abs) & (prices >= price_p75)).sum()
    )

    # Empty battery + low price → couldn't charge more
    near_empty_low_price = int(
        ((soc <= empty_abs) & (prices <= price_p25)).sum()
    )

    constraint_analysis = {
        "near_full_threshold": round(full_abs, 4),
        "near_empty_threshold": round(empty_abs, 4),
        "near_full_intervals": int(near_full),
        "near_empty_intervals": int(near_empty),
        "near_full_with_high_price": near_full_high_price,
        "near_empty_with_low_price": near_empty_low_price,
    }

    # ── SOC regime breakdown ---------------------------------------------
    n = len(soc)
    high_soc_pct = round(float((soc >= full_abs).sum() / n * 100), 2)
    low_soc_pct = round(float((soc <= empty_abs).sum() / n * 100), 2)
    med_soc_pct = round(100.0 - high_soc_pct - low_soc_pct, 2)

    soc_regime = {
        "high_soc_pct": high_soc_pct,
        "medium_soc_pct": med_soc_pct,
        "low_soc_pct": low_soc_pct,
    }

    # ── Hourly SOC profile -----------------------------------------------
    subset2 = subset.copy()
    subset2["_hour"] = pd.to_datetime(subset2["START_DATETIME"]).dt.floor("h")
    hourly_soc = (
        subset2.groupby("_hour")["SOC"]
        .agg(["mean", "min", "max"])
        .reset_index()
        .rename(columns={"_hour": "hour", "mean": "mean_soc", "min": "min_soc", "max": "max_soc"})
    )
    hourly_soc["hour"] = hourly_soc["hour"].astype(str)
    hourly_soc_list = hourly_soc.to_dict(orient="records")

    # ── Critical constraint intervals (missed because of SOC) ------------
    # Intervals where battery was full/empty AND price was high/low respectively
    crit_mask = (
        ((soc >= full_abs) & (prices >= price_p75)) |
        ((soc <= empty_abs) & (prices <= price_p25))
    )
    crit_df = subset[crit_mask].copy()
    cols = ["START_DATETIME", "SOC", "PRICE_ENERGY", "CHARGE_ENERGY",
            "DISCHARGE_ENERGY", "REVENUE_ENERGY"]
    # Only include columns that exist
    cols = [c for c in cols if c in crit_df.columns]
    crit_list = (
        crit_df.nlargest(10, "PRICE_ENERGY")[cols]
        .assign(START_DATETIME=lambda x: x["START_DATETIME"].astype(str))
        .to_dict(orient="records")
    )

    return {
        "scenario": scenario,
        "schedule_type": schedule_type or "all",
        "soc_statistics": {
            "min": round(min_soc, 4),
            "max": round(max_soc, 4),
            "mean": round(mean_soc, 4),
            "std": round(std_soc, 4),
            "soc_at_start": round(soc_start, 4),
            "soc_at_end": round(soc_end, 4),
        },
        "constraint_analysis": constraint_analysis,
        "soc_regime_breakdown": soc_regime,
        "hourly_soc_profile": hourly_soc_list,
        "critical_constraint_intervals": crit_list,
    }
