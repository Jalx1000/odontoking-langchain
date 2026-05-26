---
name: project-eval-system
description: Automated evaluation system for OdontokingAgent — architecture, files, and key decisions
metadata:
  type: project
---

Eval system implemented in `evals/` for OdontokingAgent. Runs real conversations then judges them with an LLM.

**Why:** Cron job on Railway — must exit with code 0/1, report to stdout, env vars from Railway (no .env).

**Key architecture decisions:**

1. Runner passes ONE message per turn to `get_response` — the Postgres checkpointer maintains full history via `thread_id=wa_id`. This replicates production WhatsApp behavior exactly.

2. Each scenario uses a unique `wa_id` with `eval_` prefix (e.g. `eval_591700000001`) to isolate Postgres threads and avoid contaminating production data.

3. `clear_history(wa_id)` is called at scenario start — failure is ignored (warn + continue) because Postgres may not be available in all envs.

4. Judge reads `evals/metrics/prompts/dental_judge.md` at module load time (same pattern as `evals/metrics/__init__.py`). Uses `client.beta.chat.completions.parse(response_format=ScenarioJudgement)` — structured output.

5. Errored scenarios (runner exception) skip judging — `judgement: null` in report, counted as failed.

**Files created:**
- `evals/scenarios/__init__.py` + `evals/scenarios/dental_scenarios.py` — 8 scenarios
- `evals/metrics/prompts/dental_judge.md` — judge system prompt
- `evals/judge.py` — `TurnScore`, `ScenarioJudgement`, `judge_scenario`, `judge_all`
- `evals/runner.py` — `run_scenario`, `run_all_scenarios`
- `evals/reporter.py` — `compile_report`, `print_console_report`, `generate_report`
- `evals/run_eval.py` — entry point with argparse
- `evals/reports/.gitkeep`

**Warning from plan:** `update_crm` tool will hit the real Odontoking CRM API during evals. Use staging `ODONTOKING_API_URL` or set `LANGFUSE_TRACING_ENABLED=false` to avoid contaminating production.

**How to apply:** When debugging eval failures, check `evals/reports/eval_*.json` for `turn_scores` to pinpoint which turn broke the flow.
