"""Judge LLM: evaluates complete OdontokingAgent conversations."""

import os

import openai
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import logger

_JUDGE_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "metrics", "prompts", "dental_judge.md")

with open(_JUDGE_PROMPT_FILE, "r") as _f:
    _JUDGE_SYSTEM_PROMPT = _f.read()


class TurnScore(BaseModel):
    turn_index: int
    user_message: str
    agent_response: str
    score: float = Field(description="puntuación 0 a 10 para este turno")
    comment: str


class ScenarioJudgement(BaseModel):
    overall_score: float = Field(description="puntuación global 0 a 10")
    passed: bool = Field(description="True si overall_score >= 7")
    summary: str = Field(description="2-3 oraciones: qué hizo bien, qué falló")
    turn_scores: list[TurnScore]
    criteria_met: bool = Field(description="True si el criterio de éxito fue cumplido")
    criteria_reasoning: str = Field(description="por qué el criterio fue o no cumplido")


async def judge_scenario(
    client: openai.AsyncOpenAI,
    scenario_result: dict,
    judge_system_prompt: str,
) -> ScenarioJudgement | None:
    """Evaluate a single scenario conversation with the judge LLM."""
    conversation = scenario_result.get("conversation", [])
    if not conversation:
        logger.warning("judge_skipped_empty_conversation", scenario_id=scenario_result.get("scenario_id"))
        return None

    conversation_text = "\n".join(
        f"[{'PACIENTE' if t['role'] == 'user' else 'ASISTENTE'}]: {t['content']}"
        for t in conversation
    )
    user_content = (
        f"CRITERIO DE ÉXITO:\n{scenario_result['success_criteria']}\n\n"
        f"CONVERSACIÓN COMPLETA:\n{conversation_text}"
    )

    try:
        response = await client.beta.chat.completions.parse(
            model=settings.EVALUATION_LLM,
            messages=[
                {"role": "system", "content": judge_system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format=ScenarioJudgement,
        )
        judgement = response.choices[0].message.parsed
        logger.info(
            "judge_scenario_completed",
            scenario_id=scenario_result.get("scenario_id"),
            overall_score=judgement.overall_score if judgement else None,
            passed=judgement.passed if judgement else None,
        )
        return judgement
    except Exception as e:
        logger.exception("judge_scenario_failed", scenario_id=scenario_result.get("scenario_id"), error=str(e))
        return None


async def judge_all(results: list[dict]) -> list[dict]:
    """Judge all scenario results and attach the judgement to each."""
    client = openai.AsyncOpenAI(
        api_key=settings.EVALUATION_API_KEY,
        base_url=settings.EVALUATION_BASE_URL,
    )

    judged: list[dict] = []
    for result in results:
        if result.get("error"):
            logger.warning(
                "judge_skipped_errored_scenario",
                scenario_id=result.get("scenario_id"),
                error=result["error"],
            )
            judged.append({**result, "judgement": None})
            continue

        judgement = await judge_scenario(client, result, _JUDGE_SYSTEM_PROMPT)
        judged.append({**result, "judgement": judgement.model_dump() if judgement else None})

    return judged
