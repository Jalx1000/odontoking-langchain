# QA-Dev Memory Index

- [Async test pattern](feedback_async_test_pattern.md) — pytest-asyncio not installed; use asyncio.run() wrappers; tool.coroutine for raw async access
- [Pre-existing failures](project_pre_existing_failures.md) — test_get_services.py::TestExtraFields::test_only_id_and_name_returned fails pre-existing; baseline 120p/112s/1f
- [n8n + FastAPI dual-agent architecture](project_n8n_fastapi_architecture.md) — Two live WhatsApp agents; n8n sends with wrong phone_number_id 1143641718822543 hardcoded in 6 nodes
