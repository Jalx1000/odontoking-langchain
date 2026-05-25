---
name: pre-existing-test-failures
description: Known pre-existing test failures that are NOT regressions — do not count them as caused by new changes
metadata:
  type: project
---

`tests/unit/tools/test_get_services.py::TestExtraFields::test_only_id_and_name_returned` fails because the test asserts only `{id, name}` keys in the response, but the tool (`odontoking.py`) also returns `duration_minutes`. This was a test authoring error — the tool has always returned this field.

**Why:** The test was written with an incorrect expectation. The tool behaviour is correct.

**How to apply:** When running the full suite, expect 1 pre-existing failure in `test_get_services.py`. Do not flag it as caused by new changes. Baseline before new tests: 120 passing, 112 skipped, 1 failing.
