"""Unit tests for the molestia → specialty/service classifier.

The raw LLM call (_llm_classify) is patched so no network is involved. The focus is the
robustness contract: validate the LLM's IDs against the real catalogs, and always fall back to
a keyword match (and finally "General") so booking is never left without a specialty.
"""

from unittest.mock import AsyncMock, patch

import pytest

import app.core.langgraph.molestia_classifier as mc
from app.core.langgraph.molestia_classifier import MolestiaClassification, classify_molestia

_SPECIALTIES = [
    {"id": 2, "name": "Endodoncista"},
    {"id": 6, "name": "Ortodoncia"},
    {"id": 10, "name": "Periodoncia"},
    {"id": 15, "name": "Rehabilitacion"},
    {"id": 20, "name": "General"},
]
_SERVICES = [
    {"id": 122, "name": "Prótesis acrílico parciales"},
    {"id": 174, "name": "Limpieza de toda la cavidad bucal"},
]


@pytest.mark.asyncio
async def test_uses_valid_llm_result():
    """A valid LLM specialty/service id is used as-is."""
    with patch.object(mc, "_llm_classify", AsyncMock(return_value=MolestiaClassification(specialty_id=15, service_id=122))):
        sid, svc = await classify_molestia("quiero una corona", _SPECIALTIES, _SERVICES)
    assert sid == 15
    assert svc["id"] == 122


@pytest.mark.asyncio
async def test_invalid_llm_specialty_falls_back_to_keyword():
    """An out-of-catalog specialty id is discarded and resolved by keyword."""
    with patch.object(mc, "_llm_classify", AsyncMock(return_value=MolestiaClassification(specialty_id=999, service_id=None))):
        sid, _ = await classify_molestia("se me rompió la corona dental", _SPECIALTIES, _SERVICES)
    assert sid == 15  # "corona" → Rehabilitacion


@pytest.mark.asyncio
async def test_llm_failure_uses_keyword_fallback():
    """When the LLM returns nothing, keyword matching resolves the specialty."""
    with patch.object(mc, "_llm_classify", AsyncMock(return_value=None)):
        assert (await classify_molestia("me sangran las encías", _SPECIALTIES, _SERVICES))[0] == 10
        assert (await classify_molestia("quiero brackets", _SPECIALTIES, _SERVICES))[0] == 6


@pytest.mark.asyncio
async def test_unknown_molestia_defaults_to_general():
    """A molestia matching no keyword defaults to the General specialty."""
    with patch.object(mc, "_llm_classify", AsyncMock(return_value=None)):
        sid, _ = await classify_molestia("algo totalmente inclasificable xyz", _SPECIALTIES, _SERVICES)
    assert sid == 20  # General


@pytest.mark.asyncio
async def test_no_specialties_returns_none():
    """With no catalog, the classifier returns no specialty (caller handles the empty case)."""
    with patch.object(mc, "_llm_classify", AsyncMock(return_value=None)):
        assert await classify_molestia("dolor", [], []) == (None, None)
