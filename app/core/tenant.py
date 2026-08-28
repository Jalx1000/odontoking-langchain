"""Tenant registry - maps slug to per-tenant WhatsApp and agent configuration.

Phase 1 (env-vars): in-memory dict loaded from environment variables at startup.
Phase 4 (DB-backed): loads from PostgreSQL with Redis cache (TTL 5 min).

Lookup priority:
  1. Redis cache  (fast, avoids DB on every webhook)
  2. PostgreSQL   (source of truth, populated via Admin API)
  3. Env-var fallback for Odontoking (keeps backward compatibility)

Each tenant = one Meta Developer App + one WhatsApp Business Account.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import settings
from app.core.logging import logger


@dataclass
class TenantConfig:
    """Runtime tenant configuration - decrypted, ready to use."""

    slug: str
    display_name: str
    verify_token: str
    phone_number_id: str
    wa_access_token: str
    agent_type: str
    crm_url: str = ""
    crm_token: str = ""
    agent_endpoint_url: Optional[str] = None
    agent_api_key: Optional[str] = None
    llm_model: str = "gpt-4o-mini"
    billing_model: str = "fixed"
    billing_tier: str = "local"
    extra: dict = field(default_factory=dict)

    def to_cache_dict(self) -> dict:
        return {
            "slug": self.slug,
            "display_name": self.display_name,
            "verify_token": self.verify_token,
            "phone_number_id": self.phone_number_id,
            "wa_access_token": self.wa_access_token,
            "agent_type": self.agent_type,
            "crm_url": self.crm_url,
            "crm_token": self.crm_token,
            "agent_endpoint_url": self.agent_endpoint_url,
            "agent_api_key": self.agent_api_key,
            "llm_model": self.llm_model,
            "billing_model": self.billing_model,
            "billing_tier": self.billing_tier,
        }

    @classmethod
    def from_cache_dict(cls, d: dict) -> "TenantConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Env-var fallback - always available for Odontoking (Phase 1 backward compat)
# ---------------------------------------------------------------------------

def _build_env_registry() -> dict[str, TenantConfig]:
    registry: dict[str, TenantConfig] = {}
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
    logger.info("tenant_env_registry_loaded", tenants=list(registry.keys()))
    return registry


_ENV_REGISTRY: dict[str, TenantConfig] = _build_env_registry()


# ---------------------------------------------------------------------------
# Cache helpers - Redis-backed (TTL = settings.TENANT_CACHE_TTL)
# ---------------------------------------------------------------------------

def _cache_key(slug: str) -> str:
    return f"tenant:config:{slug}"


def _phone_index_key() -> str:
    return "tenant:phone_index"


async def _cache_get(slug: str) -> Optional[TenantConfig]:
    """Return TenantConfig from Redis cache or None."""
    from app.core.cache import cache_service
    raw = await cache_service.get(_cache_key(slug))
    if raw:
        try:
            return TenantConfig.from_cache_dict(json.loads(raw))
        except Exception:
            pass
    return None


async def _cache_set(config: TenantConfig) -> None:
    from app.core.cache import cache_service
    await cache_service.set(
        _cache_key(config.slug),
        json.dumps(config.to_cache_dict()),
        ttl=settings.TENANT_CACHE_TTL,
    )


async def _cache_invalidate(slug: str) -> None:
    from app.core.cache import cache_service
    await cache_service.delete(_cache_key(slug))


# ---------------------------------------------------------------------------
# DB lookup - PostgreSQL via DatabaseService
# ---------------------------------------------------------------------------

def _tenant_from_db_row(row) -> TenantConfig:
    from app.services.encryption import decrypt
    return TenantConfig(
        slug=row.slug,
        display_name=row.display_name,
        verify_token=decrypt(row.verify_token),
        phone_number_id=row.phone_number_id,
        wa_access_token=decrypt(row.wa_access_token),
        agent_type=row.agent_type,
        crm_url=row.crm_url,
        crm_token=decrypt(row.crm_token) if row.crm_token else "",
        agent_endpoint_url=row.agent_endpoint_url,
        agent_api_key=decrypt(row.agent_api_key) if row.agent_api_key else None,
        llm_model=row.llm_model,
        billing_model=row.billing_model,
        billing_tier=row.billing_tier,
    )


async def _db_get(slug: str) -> Optional[TenantConfig]:
    """Query PostgreSQL for a tenant by slug."""
    try:
        from sqlmodel import Session, select
        from app.services.database import database_service
        from app.models.tenant import Tenant
        with Session(database_service.engine) as session:
            row = session.exec(select(Tenant).where(Tenant.slug == slug, Tenant.is_active == True)).first()
            if row:
                return _tenant_from_db_row(row)
    except Exception as e:
        logger.warning("tenant_db_lookup_failed", slug=slug, error=str(e))
    return None


# ---------------------------------------------------------------------------
# Public API - used throughout the app
# ---------------------------------------------------------------------------

async def get_tenant_async(slug: str) -> Optional[TenantConfig]:
    """Async lookup: Redis → DB → env fallback."""
    cached = await _cache_get(slug)
    if cached:
        return cached

    from_db = await _db_get(slug)
    if from_db:
        await _cache_set(from_db)
        return from_db

    # Phase 1 fallback - env-var registry
    return _ENV_REGISTRY.get(slug)


def get_tenant(slug: str) -> Optional[TenantConfig]:
    """Sync lookup - env registry only (safe for import-time use).

    For webhook handlers use get_tenant_async() to benefit from DB + cache.
    """
    return _ENV_REGISTRY.get(slug)


def get_tenant_by_phone_id(phone_number_id: str) -> Optional[TenantConfig]:
    """Sync lookup by phone_number_id - env registry only."""
    return next((t for t in _ENV_REGISTRY.values() if t.phone_number_id == phone_number_id), None)


async def invalidate_tenant_cache(slug: str) -> None:
    """Call after creating or updating a tenant via Admin API."""
    await _cache_invalidate(slug)
    logger.info("tenant_cache_invalidated", slug=slug)


def list_tenants() -> list[str]:
    return list(_ENV_REGISTRY.keys())
