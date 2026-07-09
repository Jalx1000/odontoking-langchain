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

    return {
        "scenario_id": scenario["id"],
        "wa_id": wa_id,
        "tags": scenario.get("tags", []),
        "success_criteria": scenario["success_criteria"],
        "conversation": conversation,
        "ran_at": datetime.now().isoformat(),
    }


async def run_all_scenarios(scenarios: list[dict] | None = None) -> list[dict]:
    """Instantiate the agent, run all scenarios, and return results."""
    agent = OdontokingAgent()
    await agent.create_graph()
    results: list[dict] = []

    target_scenarios = scenarios or SCENARIOS
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

    await agent.close()
    return results
