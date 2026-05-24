# RabbitMQBroker._setup_done is per-process — multi-replica queue redeclare causes 406

**Type:** risk
**Severity:** medium
**Area:** app/core/broker.py

## Problem
`_setup_done` is a per-process set. On multi-replica deploy, each new replica calls `setup()` and tries to declare exchanges/queues. If a queue was already declared with certain arguments by a previous replica and the new replica uses different arguments, RabbitMQ returns `406 PRECONDITION_FAILED` and drops the channel.

## Impact
New replicas fail to consume messages on startup if queue arguments ever change between deploys. Silent message loss until the replica is restarted.

## Suggested fix
Use `passive=True` on queue/exchange declare after the first successful setup, or use a distributed lock (Redis) to ensure only one replica runs full setup per deploy. Alternatively, ensure queue arguments never change between deploys by pinning them in a migration-like setup script.
