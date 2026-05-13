"""Tenant registry — maps slug to per-tenant WhatsApp and agent configuration.

Phase 1: in-memory dict loaded from environment variables.
Phase 2: DB-backed (SQLModel Tenant model) with hot-reload.

Each tenant = one Meta Developer App + one WhatsApp Business Account.
"""

from dataclasses import dataclass, field
from typing import Optional

from app.core.config import settings
from app.core.logging import logger


@dataclass
class TenantConfig:
    """All configuration scoped to one client/tenant."""

    slug: str                   # URL identifier: "odontoking", "kohlberg"
    display_name: str           # Human name for logs/alerts
    verify_token: str           # Meta webhook verify token (per-tenant)
    phone_number_id: str        # Meta WhatsApp Phone Number ID
    wa_access_token: str        # Meta permanent access token
    agent_type: str             # Which agent class handles this tenant
    # Future Phase 2 fields (already declared, values empty until DB exists):
    crm_url: str = ""
    crm_token: str = ""
    llm_model: str = "gpt-4o-mini"
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# In-memory registry — Phase 1
# New tenants: add a new TenantConfig block here until Phase 2 (DB registry).
# ---------------------------------------------------------------------------

def _build_registry() -> dict[str, TenantConfig]:
    registry: dict[str, TenantConfig] = {}

    # ── Odontoking ──────────────────────────────────────────────────────────
    if settings.WHATSAPP_PHONE_NUMBER_ID:
        registry["odontoking"] = TenantConfig(
            slug="odontoking",
            display_name="Odontoking",
            verify_token=settings.WHATSAPP_VERIFY_TOKEN,
            phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
            wa_access_token=settings.WHATSAPP_ACCESS_TOKEN,
            agent_type="odontoking",
            crm_url=settings.ODONTOKING_API_URL,
            crm_token=settings.ODONTOKING_API_TOKEN,
            llm_model="gpt-4o-mini",
        )

    # ── Kohlberg (add env vars when onboarding) ──────────────────────────────
    # import os
    # if os.getenv("KOHLBERG_PHONE_NUMBER_ID"):
    #     registry["kohlberg"] = TenantConfig(
    #         slug="kohlberg",
    #         display_name="Kohlberg",
    #         verify_token=os.getenv("KOHLBERG_VERIFY_TOKEN", ""),
    #         phone_number_id=os.getenv("KOHLBERG_PHONE_NUMBER_ID", ""),
    #         wa_access_token=os.getenv("KOHLBERG_ACCESS_TOKEN", ""),
    #         agent_type="kohlberg",
    #         crm_url=os.getenv("KOHLBERG_API_URL", ""),
    #         crm_token=os.getenv("KOHLBERG_API_TOKEN", ""),
    #         llm_model="gpt-4o",
    #     )

    logger.info("tenant_registry_loaded", tenants=list(registry.keys()))
    return registry


_REGISTRY: dict[str, TenantConfig] = _build_registry()

# Index by phone_number_id for fast webhook routing
_BY_PHONE_ID: dict[str, TenantConfig] = {
    t.phone_number_id: t for t in _REGISTRY.values()
}


def get_tenant(slug: str) -> Optional[TenantConfig]:
    """Look up a tenant by URL slug. Returns None if not found."""
    return _REGISTRY.get(slug)


def get_tenant_by_phone_id(phone_number_id: str) -> Optional[TenantConfig]:
    """Look up a tenant by WhatsApp Phone Number ID (from Meta payload metadata)."""
    return _BY_PHONE_ID.get(phone_number_id)


def list_tenants() -> list[str]:
    """Return all registered tenant slugs."""
    return list(_REGISTRY.keys())
