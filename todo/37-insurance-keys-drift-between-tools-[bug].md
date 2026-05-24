# verify_insurance docstring and _INSURANCE_ID_MAP use different keys — schema drift

**Type:** bug
**Severity:** medium
**Area:** app/core/langgraph/tools/crm.py

## Problem
`verify_insurance` tool docstring lists accepted insurance names as "Alianza"/"Nacional Seguro"/"Membresía Odontoking", but `_INSURANCE_ID_MAP` in `crm.py` uses different keys ("alianza"/"nacional vida"/...). The LLM reads the docstring to decide what to pass, but the map uses different strings.

## Impact
Insurance verification fails silently for patients with "Nacional Seguro" because the LLM passes that string but the map only recognizes "nacional vida". Patients are incorrectly told their insurance isn't supported.

## Suggested fix
Align the docstring with the actual keys in `_INSURANCE_ID_MAP`. Use lowercase normalized keys consistently. Add a test that asserts every value listed in the docstring is present as a key in the map.
