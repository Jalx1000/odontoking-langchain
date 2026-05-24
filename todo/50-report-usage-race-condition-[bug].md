# internal.py report_usage updates usage_logs without row-level lock — concurrent reports race

**Type:** bug
**Severity:** medium
**Area:** app/api/admin/internal.py

## Problem
`report_usage` reads the current usage count, increments it, and writes it back in two separate operations without a row-level lock or atomic update. Concurrent reports for the same tenant/day can read the same initial value and both write the same incremented value, losing one count.

## Impact
Usage statistics are undercounted under load. Billing and quota enforcement based on these counts will be inaccurate.

## Suggested fix
Use a SQL atomic increment: `UPDATE usage_logs SET count = count + :delta WHERE tenant_id = :tid AND date = :date`. This eliminates the read-modify-write race. Use `INSERT ... ON CONFLICT DO UPDATE SET count = count + :delta` (upsert) to handle the case where the row doesn't exist yet.
