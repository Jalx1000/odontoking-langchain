# RabbitMQBroker._republish ACKs original then publishes — if publish fails after ACK, message permanently lost

**Type:** bug
**Severity:** high
**Area:** app/core/broker.py

## Problem
`_republish` ACKs the original message before publishing the replacement; a publish error after ACK loses the message.

## Impact
Silent message loss during DLQ retry flow under broker hiccups.

## Suggested fix
Publish first with publisher confirms; ACK only after publish confirm. On publish failure, NACK with requeue. Add a test simulating publish-after-ACK failure.
