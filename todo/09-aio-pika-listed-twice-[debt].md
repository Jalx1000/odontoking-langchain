# aio-pika listed twice in pyproject.toml with inconsistent versions

**Type:** debt
**Severity:** low
**Area:** pyproject.toml

## Problem
`aio-pika` appears in both core dependencies (`>=9.6.2`) and the optional `rabbitmq` extra (`>=9.4.0`). The version constraints conflict and the intent is ambiguous — is RabbitMQ support optional or required?

## Impact
`uv sync` resolves the higher constraint but the duplication is confusing. A developer removing the optional group might accidentally break RabbitMQ support.

## Suggested fix
Remove `aio-pika` from core deps and keep it only in the `rabbitmq` optional group with the higher version constraint (`>=9.6.2`). Update the README/CLAUDE.md to note that RabbitMQ requires `uv sync --extra rabbitmq`.
