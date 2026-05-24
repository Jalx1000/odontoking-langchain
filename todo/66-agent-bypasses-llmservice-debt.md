# OdontokingAgent.__init__ instantiates ChatOpenAI without retry/timeout — bypasses LLMService fallback

**Type:** debt
**Severity:** high
**Area:** app/core/langgraph/odontoking_graph.py

## Problem
The agent constructs `ChatOpenAI` directly, bypassing `LLMService`'s retry, fallback, and global timeout budget.

## Impact
A single OpenAI hiccup fails the entire request; no model fallback. The LLMService logic is effectively dead for the production agent.

## Suggested fix
Route all LLM calls through `LLMService.invoke()`. Remove direct `ChatOpenAI` construction.
