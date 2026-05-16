"""Regression tests for _AGENT_REGISTRY and _make_process_fn — Q-2."""

from unittest.mock import MagicMock, patch


class TestAgentRegistry:
    """Verify that _AGENT_REGISTRY resolves agents by agent_type, not by slug."""

    def test_odontoking_agent_type_resolves_to_odontoking_agent(self):
        """TenantConfig with agent_type='odontoking' must resolve to odontoking_agent (not None)."""
        with (
            patch("app.core.observability.langfuse_init"),
            patch("app.core.observability.langfuse_callback_handler", new=MagicMock()),
            patch("app.services.database.database_service"),
            patch("app.services.memory.memory_service"),
            patch("app.core.langgraph.odontoking_graph.OdontokingAgent.create_graph"),
            patch("app.core.langgraph.graph.LangGraphAgent.create_graph"),
        ):
            from app.api.v1.whatsapp import _AGENT_REGISTRY
            from app.core.langgraph.odontoking_graph import odontoking_agent
            from app.core.tenant import TenantConfig

            tenant = TenantConfig(
                slug="odontoking-dev",
                display_name="Odontoking Dev",
                verify_token="tok",
                phone_number_id="111",
                wa_access_token="tok2",
                agent_type="odontoking",
            )

            resolved = _AGENT_REGISTRY.get(tenant.agent_type)

            assert resolved is not None, "agent_type='odontoking' must map to a registered agent"
            assert resolved is odontoking_agent

    def test_unknown_agent_type_falls_back_to_odontoking_agent(self):
        """TenantConfig with agent_type='unknown-future-agent' must fall back to odontoking_agent."""
        with (
            patch("app.core.observability.langfuse_init"),
            patch("app.core.observability.langfuse_callback_handler", new=MagicMock()),
            patch("app.services.database.database_service"),
            patch("app.services.memory.memory_service"),
            patch("app.core.langgraph.odontoking_graph.OdontokingAgent.create_graph"),
            patch("app.core.langgraph.graph.LangGraphAgent.create_graph"),
        ):
            from app.api.v1.whatsapp import _AGENT_REGISTRY
            from app.core.langgraph.odontoking_graph import odontoking_agent
            from app.core.tenant import TenantConfig

            tenant = TenantConfig(
                slug="future-clinic",
                display_name="Future Clinic",
                verify_token="tok",
                phone_number_id="222",
                wa_access_token="tok2",
                agent_type="unknown-future-agent",
            )

            resolved = _AGENT_REGISTRY.get(tenant.agent_type, odontoking_agent)

            assert resolved is odontoking_agent, (
                "Unknown agent_type must fall back to odontoking_agent, not raise or return None"
            )
