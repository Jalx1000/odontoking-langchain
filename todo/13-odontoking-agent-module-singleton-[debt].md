# OdontokingAgent is module-level singleton — impossible to test with mocked settings

**Type:** debt
**Severity:** high
**Area:** app/core/langgraph/odontoking_graph.py

## Problem
`odontoking_agent = OdontokingAgent()` is executed at the bottom of the module at import time. This means the agent is initialized with whatever settings and credentials are present at import, before any test fixture can override them.

## Impact
Unit tests cannot create a fresh agent with test credentials. Integration tests must use real OpenAI and Odontoking credentials even for logic that doesn't require them.

## Suggested fix
Remove the module-level instantiation. Export the class only. Instantiate in `app/main.py` lifespan startup and inject via FastAPI dependency or app state. Tests can then create their own instance with mocked settings.
