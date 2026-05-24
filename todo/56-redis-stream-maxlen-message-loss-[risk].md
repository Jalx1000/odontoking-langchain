# RedisStreamBroker.publish maxlen=10000 approximate — high-traffic can lose unacked entries

**Type:** risk
**Severity:** medium
**Area:** app/core/broker.py

## Problem
`RedisStreamBroker.publish` uses `maxlen=10_000, approximate=True` on `XADD`. Redis trims the stream to approximately 10k entries. Under high traffic, if workers fall behind, entries older than the trim threshold are deleted before being processed.

## Impact
Messages are silently lost during traffic spikes if the consumer lag exceeds 10k entries. No DLQ entry is created — the message is simply gone.

## Suggested fix
Monitor consumer lag via Prometheus (`redis_stream_length` metric). Set `maxlen` to a value appropriate for expected peak throughput × maximum acceptable lag time. Add an alert when stream length exceeds 80% of `maxlen`.
