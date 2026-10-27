"""Runner: executes OdontokingAgent conversation scenarios for evaluation."""

import asyncio
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logging import logger
from app.schemas.chat import Message
from app.core.langgraph.odontoking_graph import OdontokingAgent
from evals.scenarios import SCENARIOS

# Optional pause between turns/scenarios so a fast local run does not exhaust the OpenAI
# per-minute token limit (which otherwise floods the run with 429s and skews results).
# Default 0 keeps normal behavior; set EVAL_TURN_DELAY / EVAL_SCENARIO_DELAY (seconds) to throttle.
_TURN_DELAY = float(os.getenv("EVAL_TURN_DELAY", "0"))
_SCENARIO_DELAY = float(os.getenv("EVAL_SCENARIO_DELAY", "0"))


async def run_scenario(agent: OdontokingAgent, scenario: dict) -> dict:
    """Execute a full scenario turn-by-turn and return the recorded conversation."""
    wa_id = scenario["wa_id"]
    ctx = scenario.get("patient_context", {})
    conversation: list[dict] = []

    try:
        await agent.clear_history(wa_id)
        logger.info("runner_history_cleared", scenario_id=scenario["id"], wa_id=wa_id)
    except Exception as e:
        logger.warning("runner_clear_history_skipped", scenario_id=scenario["id"], wa_id=wa_id, error=str(e))

    for turn_index, user_text in enumerate(scenario["turns"]):
        user_msg = Message(role="user", content=user_text)
        try:
            agent_response = await agent.get_response(
                messages=[user_msg],
                wa_id=wa_id,
                is_new_patient=ctx.get("is_new_patient", True),
                ci_paciente=ctx.get("ci_paciente"),
                seguro_paciente=ctx.get("seguro_paciente"),
                nombre_registrado=ctx.get("nombre_registrado"),
                nombre_whatsapp=ctx.get("nombre_whatsapp"),
                # Forms-mode context (optional): lets scenarios exercise reminders (C1/C2),
                # "no cita activa", and "¿cuándo es mi cita?" without hitting the CRM.
                citas=ctx.get("citas"),
                cita_activa=ctx.get("cita_activa"),
                conversation_id=ctx.get("conversation_id"),
                pacientes_a_cargo=ctx.get("pacientes_a_cargo"),
                recordatorios=ctx.get("recordatorios"),
            )
        except Exception as e:
            logger.exception(
                "runner_turn_failed",
                scenario_id=scenario["id"],
                turn_index=turn_index,
                error=str(e),
            )
            agent_response = f"[ERROR en turno {turn_index}: {e}]"

        conversation.append({"role": "user", "content": user_text})
        conversation.append({"role": "assistant", "content": agent_response})

        if _TURN_DELAY:
            await asyncio.sleep(_TURN_DELAY)

    return {
        "scenario_id": scenario["id"],
        "wa_id": wa_id,
        "tags": scenario.get("tags", []),
        "success_criteria": scenario["success_criteria"],
        "conversation": conversation,
        "ran_at": datetime.now().isoformat(),
    }


async def _seed_reminders() -> dict[int, int]:
    """Re-seed the sandbox reminders and return {conversation_id: activity_id} (respuesta=null).

    The eval reminder scenarios need a pending reminder each run; activity_ids change on every seed,
    so we read them fresh from the CRM here instead of hardcoding. Best-effort: on failure returns
    {} and the scenarios keep whatever activity_id they carry.
    """
    import httpx

    from app.core.config import settings

    url = f"{settings.CRM_BASE_URL}/api/v1/whatsapp/eval/seed"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {settings.CRM_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        mapping: dict[int, int] = {}
        for c in data.get("contactos", []):
            cid = c.get("conversation_id")
            rec = (c.get("recordatorios") or [])
            pending = next((r for r in rec if r.get("respuesta") is None), None)
            if cid is not None and pending and pending.get("activity_id"):
                mapping[int(cid)] = int(pending["activity_id"])
        logger.info("runner_reminders_seeded", mapping=mapping)
        return mapping
    except Exception as e:
        logger.warning("runner_reminder_seed_failed", error=str(e))
        return {}


def _apply_seeded_reminders(scenario: dict, seeded: dict[int, int]) -> None:
    """Inject the fresh activity_id into a scenario's recordatorios (matched by conversation_id)."""
    ctx = scenario.get("patient_context", {})
    recs = ctx.get("recordatorios")
    if not recs or not seeded:
        return
    cid = ctx.get("conversation_id")
    aid = seeded.get(int(cid)) if cid is not None else None
    if aid is None:
        return
    for r in recs:
        if isinstance(r, dict) and r.get("respuesta") is None:
            r["activity_id"] = aid


async def run_all_scenarios(scenarios: list[dict] | None = None) -> list[dict]:
    """Instantiate the agent, run all scenarios, and return results."""
    agent = OdontokingAgent()
    await agent.create_graph()
    results: list[dict] = []

    target_scenarios = scenarios or SCENARIOS

    # Re-seed sandbox reminders once per run if any scenario needs one, and patch in fresh ids.
    if any(s.get("patient_context", {}).get("recordatorios") for s in target_scenarios):
        seeded = await _seed_reminders()
        for s in target_scenarios:
            _apply_seeded_reminders(s, seeded)
    for scenario in target_scenarios:
        logger.info("runner_scenario_start", scenario_id=scenario["id"])
        print(f"  Corriendo: {scenario['id']}")
        try:
            result = await run_scenario(agent, scenario)
            result["error"] = None
        except Exception as e:
            logger.exception("runner_scenario_failed", scenario_id=scenario["id"], error=str(e))
            result = {
                "scenario_id": scenario["id"],
                "wa_id": scenario["wa_id"],
                "tags": scenario.get("tags", []),
                "success_criteria": scenario["success_criteria"],
                "conversation": [],
                "ran_at": datetime.now().isoformat(),
                "error": str(e),
            }
        results.append(result)
        if _SCENARIO_DELAY:
            await asyncio.sleep(_SCENARIO_DELAY)

    await agent.close()
    return results
