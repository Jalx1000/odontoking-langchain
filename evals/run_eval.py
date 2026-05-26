#!/usr/bin/env python3
"""Entry point: python evals/run_eval.py [--scenarios id1,id2] [--no-report]"""

import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.runner import run_all_scenarios
from evals.judge import judge_all
from evals.reporter import compile_report, generate_report, print_console_report
from evals.scenarios import SCENARIOS


async def main(scenario_ids: list[str] | None, no_report: bool) -> int:
    """Orchestrate runner, judge, and reporter for OdontokingAgent evaluation."""
    scenarios = SCENARIOS
    if scenario_ids:
        scenarios = [s for s in SCENARIOS if s["id"] in scenario_ids]
        if not scenarios:
            print(f"No se encontraron escenarios con IDs: {scenario_ids}")
            return 1

    print("[1/3] Corriendo conversaciones con el agente...")
    results = await run_all_scenarios(scenarios)

    print("[2/3] Evaluando con juez LLM...")
    judged = await judge_all(results)

    print("[3/3] Generando reporte...")
    report = compile_report(judged)
    print_console_report(report)

    if not no_report:
        generate_report(report)

    all_passed = all(r.get("judgement") and r["judgement"].get("passed", False) for r in judged)
    return 0 if all_passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evalúa OdontokingAgent con escenarios de conversación reales.")
    parser.add_argument("--scenarios", help="IDs de escenarios separados por coma", default=None)
    parser.add_argument("--no-report", action="store_true", help="No guardar reporte JSON")
    args = parser.parse_args()
    ids = args.scenarios.split(",") if args.scenarios else None
    sys.exit(asyncio.run(main(ids, args.no_report)))
