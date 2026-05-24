# evals/ exists but no scheduled run — prompt changes and eval drift go unnoticed

**Type:** debt
**Severity:** low
**Area:** evals/

## Problem
The `evals/` framework exists but has no scheduled execution; prompt drift is invisible.

## Impact
Quality regressions ship silently between releases.

## Suggested fix
Add a nightly GitHub Actions workflow that runs `make eval-quick` and posts results to Slack. Track success rate over time.
