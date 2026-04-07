# Battery Performance Analysis — LLM Decision Analyst

An LLM-powered agent that analyses battery storage performance data (historical vs. perfect-foresight) and produces structured, evidence-backed trading recommendations using the modern **google-genai** SDK.

---

## Project Structure

```
battery_performance_analysis/
├── agent.py                         # Main LLM agent orchestrator (google-genai)
├── tools/
│   ├── __init__.py
│   ├── revenue_summary.py           # Tool: compute_revenue_summary
│   ├── price_analysis.py            # Tool: identify_high_price_intervals
│   ├── dispatch_compare.py          # Tool: compare_dispatch
│   └── soc_analysis.py              # Tool: analyze_state_of_charge
├── data/
│   └── battery_data.csv             # ← Place your CSV file here
├── output/
│   └── battery_analysis_report.md   # Auto-generated report
├── requirements.txt
└── README.md
```

---

## How to Run

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Your API Key (.env)

The agent uses `python-dotenv` to automatically load your API key. 

1. Create a file named `.env` in the root directory.
2. Add your key using either of these variable names:
```env
GOOGLE_API_KEY=your-key-here
# or
GEMINI_API_KEY=your-key-here
```

> Get a free key at https://aistudio.google.com/app/apikey

### 3. Place Your Data File

Copy your CSV file into the `data/` directory.

Download the Blyth Battery dataset from:
https://drive.google.com/file/d/1g0mYdUdt3K1ak266pLA90MCzR27tTbeq/view?usp=sharing

### 4. Run the Agent

Once your `.env` and data are ready, run the agent:

```bash
# Basic usage
python agent.py --data data/battery_data.csv

# Custom output path
python agent.py --data data/battery_data.csv --output output/my_report.md

# Use a different Gemini model
python agent.py --data data/battery_data.csv --model gemini-3.0-pro

# Quiet mode (no step-by-step output)
python agent.py --data data/battery_data.csv --quiet
```


---

## High-Level Approach

This agent uses the **Google Gen AI (google-genai) Python SDK** to implement a structured, multi-step analysis workflow. Instead of dumping raw data into a single prompt, the LLM is given four analytical Python tools and instructed to call them in sequence before producing a final report.

### Why Tools Instead of Raw Data?

- **Scalability** — CSV files can have thousands of rows; tools return compact, structured summaries.
- **Structured reasoning** — Each tool answers one specific question; the LLM combines the answers.
- **Auditability** — A JSON tool-call trace is saved alongside the report.
- **Generalisability** — Works with any dataset matching the column schema.

---

## How the Agent Uses Tools

The agent follows a **mandatory 5-call sequence** enforced by the system prompt, orchestrated via the Gen AI `Client` with a manual control loop for high-fidelity logging:

| Step | Tool | Question Answered |
|------|------|-------------------|
| 1 | `compute_revenue_summary(scenario='historical')` | What was the actual revenue? |
| 2 | `compute_revenue_summary(scenario='perfect')` | What was the theoretical maximum? |
| 3 | `identify_high_price_intervals(scenario='historical')` | Which high-price windows did the battery miss? |
| 4 | `compare_dispatch()` | Where did dispatch decisions diverge? |
| 5 | `analyze_state_of_charge(scenario='historical')` | Were SOC constraints the root cause? |

After all five tools respond, the LLM synthesises their outputs into a structured report with:
- Revenue gap ($/%) 
- Primary driver with numerical evidence
- Secondary contributing factor with evidence
- Two actionable recommendations (Action / Reasoning / Benefit / Tradeoff)

---

## Tool Descriptions

### `compute_revenue_summary(scenario, schedule_type?)`
Computes total revenue, per-interval stats, hourly breakdown, and top/bottom performing intervals for a given scenario.

### `identify_high_price_intervals(threshold_percentile?, scenario, schedule_type?)`
Finds intervals where price exceeds a given percentile and checks whether the battery was discharging during those windows. Also identifies "bad timing" — charging when prices were high.

### `compare_dispatch(schedule_type?, metric?)`
Side-by-side comparison of charge/discharge energy and revenue between historical and perfect scenarios. Reports dispatch alignment and the top divergence intervals.

### `analyze_state_of_charge(scenario, schedule_type?, full_threshold?, empty_threshold?)`
Analyses the SOC profile to find intervals where a near-full or near-empty battery prevented optimal dispatch during high/low price windows.

---

## Output Files

| File | Description |
|------|-------------|
| `output/battery_analysis_report.md` | Structured Markdown report |
| `output/battery_analysis_report.tool_log.json` | JSON trace of all tool calls |

---

## Generalisability

The agent works with any CSV that has the required columns (see `data/battery_data.csv`). No code changes are needed. Simply pass the `--data` flag pointing to your file.

---

## Example Output

See [`output/example_output.md`](output/example_output.md) for a sample report produced against the Blyth Battery dataset.
