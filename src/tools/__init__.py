"""
Battery Performance Analysis Tools
====================================
Custom tool functions used by the LLM agent to analyze battery trading data.
Each tool returns a structured dict that feeds the agent's reasoning pipeline.
"""

from .revenue_summary import compute_revenue_summary
from .price_analysis import identify_high_price_intervals
from .dispatch_compare import compare_dispatch
from .soc_analysis import analyze_state_of_charge

__all__ = [
    "compute_revenue_summary",
    "identify_high_price_intervals",
    "compare_dispatch",
    "analyze_state_of_charge",
]
