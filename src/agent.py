"""
Battery Trading Decision Analyst - LLM Agent
==============================================
Main orchestrator. Implements a multi-step Gemini tool-calling agent using
the modern google-genai SDK.

  1. Calls compute_revenue_summary  → establishes the performance gap
  2. Calls identify_high_price_intervals → finds missed pricing windows
  3. Calls compare_dispatch          → quantifies dispatch misalignment
  4. Calls analyze_state_of_charge  → checks SOC-driven constraints
  5. Synthesises all tool outputs into a structured decision-support report

Usage
-----
  python agent.py --data data/battery_data.csv
  python agent.py --data data/battery_data.csv --output output/report.md
  python agent.py --data data/battery_data.csv --model gemini-2.0-flash
"""

import argparse
import json
import os
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types


# ── Project imports ──────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from tools import (
    compute_revenue_summary,
    identify_high_price_intervals,
    compare_dispatch,
    analyze_state_of_charge,
)

# ── Constants ────────────────────────────────────────────────────────────────
DEFAULT_MODEL = "gemini-2.5-flash"

REQUIRED_COLUMNS = {
    "SCENARIO_NAME", "SCHEDULE_TYPE", "START_DATETIME",
    "SOC", "CHARGE_ENERGY", "DISCHARGE_ENERGY", "PRICE_ENERGY", "REVENUE_ENERGY",
}


# ════════════════════════════════════════════════════════════════════════════
#  Data Loading & Validation
# ════════════════════════════════════════════════════════════════════════════

def load_and_validate_data(csv_path: str) -> pd.DataFrame:
    """Load CSV and perform basic schema validation."""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {csv_path}")

    # Read CSV
    df = pd.read_csv(csv_path, parse_dates=["START_DATETIME"])

    # Handle Rename: some datasets use REVENUE instead of REVENUE_ENERGY
    if "REVENUE" in df.columns and "REVENUE_ENERGY" not in df.columns:
        df = df.rename(columns={"REVENUE": "REVENUE_ENERGY"})

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Normalise scenario / schedule names to lowercase for consistent filtering
    df["SCENARIO_NAME"] = df["SCENARIO_NAME"].str.strip().str.lower()
    df["SCHEDULE_TYPE"] = df["SCHEDULE_TYPE"].str.strip().str.lower()

    return df



# ════════════════════════════════════════════════════════════════════════════
#  Tool Registry (Wrapped for SDK)
# ════════════════════════════════════════════════════════════════════════════

def build_tool_registry(df: pd.DataFrame) -> dict[str, Any]:
    """
    Returns a dict mapping tool name → callable bound to `df`.
    These functions have clear docstrings so google-genai can auto-generate schemas.
    """
    
    def tool_compute_revenue_summary(scenario: str, schedule_type: str = None) -> dict:
        """
        Computes revenue statistics for a given scenario and optional schedule type.
        Returns total revenue, per-interval stats, hourly breakdown, and top/bottom intervals.
        
        Args:
            scenario: One of 'historical' or 'perfect'.
            schedule_type: One of 'expected' or 'cleared'. Optional.
        """
        return compute_revenue_summary(df, scenario, schedule_type)

    def tool_identify_high_price_intervals(scenario: str, threshold_percentile: float = 75.0, schedule_type: str = "cleared") -> dict:
        """
        Identifies high-value and low-value market price intervals and checks whether the battery was dispatching optimally.
        
        Args:
            scenario: One of 'historical' or 'perfect'.
            threshold_percentile: Percentile above which price is considered high. Default 75.
            schedule_type: One of 'expected' or 'cleared'. Default 'cleared'.
        """
        return identify_high_price_intervals(df, threshold_percentile, scenario, schedule_type)

    def tool_compare_dispatch(schedule_type: str = "cleared", metric: str = "all") -> dict:
        """
        Side-by-side comparison of dispatch energy and revenue between historical and perfect scenarios.
        
        Args:
            schedule_type: One of 'expected' or 'cleared'. Default 'cleared'.
            metric: One of 'charge', 'discharge', 'revenue', or 'all'. Default 'all'.
        """
        return compare_dispatch(df, schedule_type, metric)

    def tool_analyze_state_of_charge(scenario: str, schedule_type: str = "cleared", full_threshold: float = 0.90, empty_threshold: float = 0.10) -> dict:
        """
        Analyzes the SOC profile to identify energy-constraint missed opportunities.
        
        Args:
            scenario: One of 'historical' or 'perfect'.
            schedule_type: One of 'expected' or 'cleared'. Default 'cleared'.
            full_threshold: Fraction of max SOC above which battery is near-full. Default 0.90.
            empty_threshold: Fraction of max SOC below which battery is near-empty. Default 0.10.
        """
        return analyze_state_of_charge(df, scenario, schedule_type, full_threshold, empty_threshold)

    return {
        "compute_revenue_summary": tool_compute_revenue_summary,
        "identify_high_price_intervals": tool_identify_high_price_intervals,
        "compare_dispatch": tool_compare_dispatch,
        "analyze_state_of_charge": tool_analyze_state_of_charge,
    }


# ════════════════════════════════════════════════════════════════════════════
#  Prompt Loader
# ════════════════════════════════════════════════════════════════════════════

def load_system_prompt(prompt_path: str = "prompts/system_prompt.txt") -> str:
    """Load the system instruction from a text file."""
    path = Path(__file__).parent / prompt_path
    if not path.exists():
        # Fallback to a minimal instruction if file is missing
        return "You are an assistant. Please follow the instructions provided."
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


# ════════════════════════════════════════════════════════════════════════════
#  Agent Loop
# ════════════════════════════════════════════════════════════════════════════

def run_agent(client: genai.Client, df: pd.DataFrame, model_name: str, verbose: bool = True) -> str:
    """
    Run the multi-step Gemini tool-calling agent using google-genai.
    Returns the final Markdown report as a string.
    """
    # ── Load instructions ──────────────────────────────────────────────────
    system_instruction = load_system_prompt()

    # ── Collect dataset metadata for the initial user prompt ──────────────
    scenarios = df["SCENARIO_NAME"].unique().tolist()
    sched_types = df["SCHEDULE_TYPE"].unique().tolist()
    date_min = str(df["START_DATETIME"].min())
    date_max = str(df["START_DATETIME"].max())
    n_intervals = len(df)

    user_prompt = textwrap.dedent(f"""
        Analyse battery performance data with the following characteristics:
        - Scenarios present: {scenarios}
        - Schedule types present: {sched_types}
        - Time range: {date_min} → {date_max}
        - Total dataset rows: {n_intervals}

        Please follow your mandatory tool sequence and produce the full
        Battery Performance Analysis Report.
    """).strip()

    if verbose:
        print("\n" + "=" * 70)
        print("  BATTERY TRADING DECISION ANALYST (google-genai)")
        print("=" * 70)
        print(f"  Model  : {model_name}")
        print(f"  Data   : {n_intervals} intervals | {date_min[:10]} → {date_max[:10]}")
        print(f"  Tools  : compute_revenue_summary | identify_high_price_intervals")
        print(f"           compare_dispatch | analyze_state_of_charge")
        print("=" * 70 + "\n")

    tool_registry = build_tool_registry(df)
    
    # ── Initialise Chat ───────────────────────────────────────────────────
    # Note: We pass the list of tool functions directly. 
    # google-genai handles schema extraction from docstrings.
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=list(tool_registry.values()),
    )



    # With automatic_function_calling=True, the SDK handles the loop!
    # However, to maintain the verbose logging of steps as requested by the original logic,
    # we can either disable it or wrap the tools to print.
    # Given the requirements for "Step X" logging in the prompt, let's keep it manual
    # or use a verbose wrapper for tools.
    
    # We will use manual loop to keep the pretty "Step X" output and control.
    
    history = [

        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    ]
    
    tool_call_log: list[dict] = []
    step = 0
    max_steps = 20

    while step < max_steps:
        step += 1
        if verbose:
            print(f"[Step {step}] Sending message to model …")

        response = client.models.generate_content(
            model=model_name,
            contents=history,
            config=config
        )
        
        # Add assistant response to history
        history.append(response.candidates[0].content)
        
        content = response.candidates[0].content
        func_calls = [p.function_call for p in content.parts if p.function_call]
        
        if not func_calls:
            # Final answer
            final_text = "".join([p.text for p in content.parts if p.text]).strip()
            if verbose:
                print(f"\n[Step {step}] Model returned final report.\n")
            return final_text, tool_call_log

        # ── Process Function Calls ────────────────────────────────────────
        tool_parts = []
        for fc in func_calls:
            tool_name = fc.name
            tool_args = fc.args
            
            if verbose:
                print(f"  ▶ Tool call: {tool_name}({json.dumps(tool_args, indent=4)})")
            
            if tool_name not in tool_registry:
                result = {"error": f"Unknown tool: {tool_name}"}
            else:
                try:
                    result = tool_registry[tool_name](**tool_args)
                except Exception as exc:
                    result = {"error": str(exc)}

            result_trimmed = _trim_result(result)
            
            tool_call_log.append({
                "step": step,
                "tool": tool_name,
                "args": tool_args,
                "result_summary": _summarise_result(result_trimmed),
            })
            
            if verbose:
                print(f"  ◀ Tool result keys: {list(result_trimmed.keys())}")
            
            tool_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=tool_name,
                        response={"result": result_trimmed}
                    )
                )
            )
            
        # Add tool responses to history
        history.append(types.Content(role="model", parts=tool_parts)) # Note: using role="model" or "tool"?
        # In Google Gen AI API, tool results usually follow a "model" role content or are distinct.
        # Actually in google-genai, parts can be FunctionResponse.
        # Correct role for function response parts is "tool" or specifically handled.
        # Let's fix the history addition:
        history[-1] = types.Content(role="tool", parts=tool_parts)

    return "ERROR: Agent exceeded maximum steps without producing a report.", tool_call_log


def _trim_result(result: dict, max_list_len: int = 10) -> dict:
    """Trim long lists within tool results to keep LLM context manageable."""
    trimmed = {}
    for k, v in result.items():
        if isinstance(v, list) and len(v) > max_list_len:
            trimmed[k] = v[:max_list_len]
        elif isinstance(v, dict):
            trimmed[k] = _trim_result(v, max_list_len)
        else:
            trimmed[k] = v
    return trimmed


def _summarise_result(result: dict) -> str:
    """One-line human-readable summary of a tool result."""
    try:
        keys = list(result.keys())
        return f"{len(keys)} keys returned: {keys[:6]}"
    except Exception:
        return str(result)[:120]


# ════════════════════════════════════════════════════════════════════════════
#  Output Formatting
# ════════════════════════════════════════════════════════════════════════════

def save_report(report: str, tool_log: list[dict], output_path: str):
    """Save the Markdown report and a JSON tool-call trace."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", encoding="utf-8") as f:
        f.write(report)

    log_path = out.with_suffix(".tool_log.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(tool_log, f, indent=2, default=str)

    print(f"\n✅  Report saved  → {out}")
    print(f"📋  Tool log saved → {log_path}")


def print_tool_trace(tool_log: list[dict]):
    """Print a clean tool-call trace to stdout."""
    print("\n" + "─" * 70)
    print("  TOOL CALL TRACE")
    print("─" * 70)
    for entry in tool_log:
        args_str = ", ".join(f"{k}={v!r}" for k, v in entry["args"].items()) or "(defaults)"
        print(f"  Step {entry['step']:>2} │ {entry['tool']}({args_str})")
        print(f"         │ → {entry['result_summary']}")
    print("─" * 70 + "\n")


# ════════════════════════════════════════════════════════════════════════════
#  CLI Entry Point
# ════════════════════════════════════════════════════════════════════════════

def main():
    # ── Load Environment Variables (.env) ────────────────────────────────
    load_dotenv()

    parser = argparse.ArgumentParser(

        description="LLM-powered battery trading decision analyst.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python agent.py --data data/battery_data.csv
              python agent.py --data data/battery_data.csv --output output/report.md
              python agent.py --data data/battery_data.csv --model gemini-2.0-flash
        """),
    )
    parser.add_argument(
        "--data", required=True,
        help="Path to the battery interval CSV file.",
    )
    parser.add_argument(
        "--output", default="output/battery_analysis_report.md",
        help="Path for the output Markdown report (default: output/battery_analysis_report.md).",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Gemini model to use (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--api-key", default=None,
        help="Google AI API key. Falls back to GOOGLE_API_KEY env var.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress verbose step-by-step output.",
    )
    args = parser.parse_args()

    # ── API Key ──────────────────────────────────────────────────────────
    api_key = args.api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit(
            "❌  No API key found. Set GOOGLE_API_KEY or GEMINI_API_KEY in your "
            ".env file or environment, or pass --api-key."
        )

    
    # ── Initialise Client ────────────────────────────────────────────────
    client = genai.Client(api_key=api_key)

    # ── Load data ────────────────────────────────────────────────────────
    print(f"\n📂  Loading data from: {args.data}")
    df = load_and_validate_data(args.data)
    print(f"✅  Loaded {len(df)} rows, {df['SCENARIO_NAME'].nunique()} scenario(s).")

    # ── Run agent ────────────────────────────────────────────────────────
    report, tool_log = run_agent(client, df, model_name=args.model, verbose=not args.quiet)

    # ── Print trace ──────────────────────────────────────────────────────
    if not args.quiet:
        print_tool_trace(tool_log)

    # ── Print report ─────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  FINAL REPORT")
    print("=" * 70)
    print(report)

    # ── Save outputs ──────────────────────────────────────────────────────
    save_report(report, tool_log, args.output)


if __name__ == "__main__":
    main()
