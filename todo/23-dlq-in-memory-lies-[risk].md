# RabbitMQBroker._dlq is in-memory — DLQ list/retry endpoints lie about content

**Type:** risk
**Severity:** medium
**Area:** app/core/broker.py

## Problem
`RabbitMQBroker._dlq` is a plain list in memory. The admin DLQ list and retry endpoints (`GET /dlq`, `POST /dlq/retry`) read and write from this in-memory list. On process restart, all DLQ entries are lost.

## Impact
Failed messages that were moved to DLQ disappear on redeploy. Operators see an empty DLQ after restart and assume messages were processed, when they were actually silently dropped.

## Suggested fix
Persist DLQ entries to PostgreSQL or a dedicated RabbitMQ dead-letter exchange. At minimum, log each DLQ entry at `logger.error` level so it appears in Grafana/email alerts and can be manually replayed.
