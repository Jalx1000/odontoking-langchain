"""Reporter: compiles evaluation results into console output and JSON reports."""

import json
import os
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.core.config import settings
from app.core.logging import logger

_console = Console()

_REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def compile_report(judged_results: list[dict]) -> dict:
    """Compile judged results into a structured report dict."""
    total = len(judged_results)
    passed_count = sum(
        1 for r in judged_results
        if r.get("judgement") and r["judgement"].get("passed", False)
    )
    failed_count = total - passed_count

    scores = [
        r["judgement"]["overall_score"]
        for r in judged_results
        if r.get("judgement") and r["judgement"].get("overall_score") is not None
    ]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    pass_rate = f"{round(passed_count / total * 100, 1)}%" if total > 0 else "0%"

    scenarios_summary = []
    for r in judged_results:
        j = r.get("judgement") or {}
        scenarios_summary.append({
            "id": r["scenario_id"],
            "tags": r.get("tags", []),
            "score": j.get("overall_score"),
            "passed": j.get("passed", False),
            "summary": j.get("summary"),
            "criteria_met": j.get("criteria_met"),
            "criteria_reasoning": j.get("criteria_reasoning"),
            "conversation": r.get("conversation", []),
            "turn_scores": j.get("turn_scores", []),
            "error": r.get("error"),
        })

    return {
        "ran_at": datetime.now().isoformat(),
        "agent": "OdontokingAgent",
        "judge_model": settings.EVALUATION_LLM,
        "total_scenarios": total,
        "passed": passed_count,
        "failed": failed_count,
        "pass_rate": pass_rate,
        "avg_score": avg_score,
        "scenarios": scenarios_summary,
    }


def print_console_report(report: dict) -> None:
    """Print a rich-formatted evaluation report to stdout."""
    ran_at = report.get("ran_at", "")[:16].replace("T", " ")

    header = Panel(
        f"[bold]Modelo juez:[/bold] {report['judge_model']}     [bold]Fecha:[/bold] {ran_at}\n"
        f"[bold]Escenarios:[/bold]  {report['total_scenarios']} total    "
        f"[bold]Pasaron:[/bold] {report['passed']}    "
        f"[bold]Fallaron:[/bold] {report['failed']}\n"
        f"[bold]Tasa de éxito:[/bold] {report['pass_rate']}    "
        f"[bold]Nota promedio:[/bold] {report['avg_score']}/10",
        title="OdontokingAgent — Reporte de Evaluación",
        border_style="blue",
    )
    _console.print(header)
    _console.print()

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Escenario", min_width=35)
    table.add_column("Nota", justify="center", min_width=6)
    table.add_column("Estado", justify="center", min_width=8)

    for s in report["scenarios"]:
        score_val = s.get("score")
        score_str = f"{score_val:.1f}" if score_val is not None else "N/A"
        passed = s.get("passed", False)

        if s.get("error"):
            status_text = Text("ERROR", style="bold yellow")
            score_str = "—"
        elif passed:
            status_text = Text("PASÓ", style="bold green")
        else:
            status_text = Text("FALLÓ", style="bold red")

        table.add_row(s["id"], score_str, status_text)

    _console.print(table)

    failed_scenarios = [s for s in report["scenarios"] if not s.get("passed", False) and not s.get("error")]
    errored_scenarios = [s for s in report["scenarios"] if s.get("error")]

    if errored_scenarios:
        _console.print()
        _console.print("[bold yellow]ESCENARIOS CON ERROR[/bold yellow]")
        _console.print("─" * 40)
        for s in errored_scenarios:
            _console.print(f"[yellow]ERROR: {s['id']}[/yellow]")
            _console.print(f"  {s['error']}")

    if failed_scenarios:
        _console.print()
        _console.print("[bold red]FALLOS DESTACADOS[/bold red]")
        _console.print("─" * 40)
        for s in failed_scenarios:
            score_val = s.get("score")
            score_str = f"{score_val:.1f}" if score_val is not None else "N/A"
            _console.print(f"[bold red]FALLÓ:[/bold red] {s['id']} (nota: {score_str})")
            _console.print(f"  Criterio: {s.get('criteria_reasoning', '—')}")
            _console.print(f"  Juez: {s.get('summary', '—')}")
            _console.print()

    if not failed_scenarios and not errored_scenarios:
        _console.print()
        _console.print("[bold green]Todos los escenarios pasaron.[/bold green]")


def generate_report(report: dict) -> str:
    """Save the report as a JSON file and return the path."""
    os.makedirs(_REPORTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(_REPORTS_DIR, f"eval_{timestamp}.json")

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("eval_report_saved", report_path=report_path)
    _console.print(f"\nReporte JSON: {report_path}")
    return report_path
