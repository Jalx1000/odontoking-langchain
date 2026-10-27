"""Evaluation scenarios for OdontokingAgent.

- `dental_scenarios.SCENARIOS`: classic chat-driven booking flow.
- `dental_forms_scenarios.FORMS_SCENARIOS`: WhatsApp native-forms flow (run with
  WHATSAPP_FLOWS_ENABLED=true). Covers "Escenarios del agente Odontoking.md".

`SCENARIOS` exposes both so `make eval-scenarios` can target any id; pick a forms subset with
`make eval-scenarios SCENARIOS=forms_limpieza_general,forms_recordatorio_confirma`.
"""

from evals.scenarios.dental_forms_scenarios import FORMS_SCENARIOS
from evals.scenarios.dental_scenarios import SCENARIOS as CLASSIC_SCENARIOS

SCENARIOS = CLASSIC_SCENARIOS + FORMS_SCENARIOS

__all__ = ["SCENARIOS", "CLASSIC_SCENARIOS", "FORMS_SCENARIOS"]
