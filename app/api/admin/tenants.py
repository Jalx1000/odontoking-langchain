"""Admin CRUD endpoints for tenant management."""

import uuid
from datetime import datetime, UTC
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.admin.deps import require_admin
from app.core.logging import logger
from app.core.tenant import invalidate_tenant_cache
from app.models.tenant import Tenant
from app.services.database import database_service
from app.services.encryption import decrypt, encrypt
from app.utils.sanitization import validate_external_url

router = APIRouter(prefix="/tenants", tags=["admin-tenants"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class TenantCreate(BaseModel):
    slug: str
    display_name: str
    phone_number_id: str
    wa_access_token: str
    verify_token: str
    agent_type: str = "odontoking"
    llm_model: str = "gpt-4o-mini"
    crm_url: str = ""
    crm_token: str = ""
    agent_endpoint_url: Optional[str] = None
    agent_api_key: Optional[str] = None
    billing_model: str = "fixed"
    billing_tier: str = "local"
    msg_limit_month: int = 5000


class TenantUpdate(BaseModel):
    display_name: Optional[str] = None
    wa_access_token: Optional[str] = None
    verify_token: Optional[str] = None
    agent_type: Optional[str] = None
    llm_model: Optional[str] = None
    crm_url: Optional[str] = None
    crm_token: Optional[str] = None
    agent_endpoint_url: Optional[str] = None
    agent_api_key: Optional[str] = None
    billing_model: Optional[str] = None
    billing_tier: Optional[str] = None
    msg_limit_month: Optional[int] = None
    is_active: Optional[bool] = None


class TenantResponse(BaseModel):
    id: uuid.UUID
    slug: str
    display_name: str
    phone_number_id: str
    agent_type: str
    llm_model: str
    crm_url: str
    agent_endpoint_url: Optional[str] = None
    # agent_api_key is write-only — never returned in responses
    billing_model: str
    billing_tier: str
    msg_limit_month: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Sensitive fields are masked — never returned in plaintext
    wa_access_token_masked: str
    verify_token_masked: str

    @classmethod
    def from_db(cls, row: Tenant) -> "TenantResponse":
        token = decrypt(row.wa_access_token)
        vtoken = decrypt(row.verify_token)
        return cls(
            id=row.id,
            slug=row.slug,
            display_name=row.display_name,
            phone_number_id=row.phone_number_id,
            agent_type=row.agent_type,
            llm_model=row.llm_model,
            crm_url=row.crm_url,
            agent_endpoint_url=row.agent_endpoint_url,
            billing_model=row.billing_model,
            billing_tier=row.billing_tier,
            msg_limit_month=row.msg_limit_month,
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
            wa_access_token_masked=f"{token[:8]}...{token[-4:]}" if len(token) > 12 else "****",
            verify_token_masked=f"{vtoken[:4]}...{vtoken[-4:]}" if len(vtoken) > 8 else "****",
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[TenantResponse], dependencies=[Depends(require_admin)])
async def list_tenants():
    """List all tenants (active and inactive)."""
    with Session(database_service.engine) as session:
        rows = session.exec(select(Tenant).order_by(Tenant.created_at)).all()
        return [TenantResponse.from_db(r) for r in rows]


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_tenant(body: TenantCreate):
    """Create a new tenant. Sensitive tokens are encrypted before storage."""
    try:
        if body.crm_url:
            validate_external_url(body.crm_url, "crm_url")
        if body.agent_endpoint_url:
            validate_external_url(body.agent_endpoint_url, "agent_endpoint_url")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    with Session(database_service.engine) as session:
        existing = session.exec(select(Tenant).where(Tenant.slug == body.slug)).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Tenant '{body.slug}' already exists.")

        row = Tenant(
            slug=body.slug,
            display_name=body.display_name,
            phone_number_id=body.phone_number_id,
            wa_access_token=encrypt(body.wa_access_token),
            verify_token=encrypt(body.verify_token),
            agent_type=body.agent_type,
            llm_model=body.llm_model,
            crm_url=body.crm_url,
            crm_token=encrypt(body.crm_token) if body.crm_token else "",
            agent_endpoint_url=body.agent_endpoint_url or "",
            agent_api_key=encrypt(body.agent_api_key) if body.agent_api_key else "",
            billing_model=body.billing_model,
            billing_tier=body.billing_tier,
            msg_limit_month=body.msg_limit_month,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info("admin_tenant_created", slug=row.slug)
        await invalidate_tenant_cache(row.slug)
        return TenantResponse.from_db(row)


@router.get("/{slug}", response_model=TenantResponse, dependencies=[Depends(require_admin)])
async def get_tenant(slug: str):
    """Get a single tenant by slug."""
    with Session(database_service.engine) as session:
        row = session.exec(select(Tenant).where(Tenant.slug == slug)).first()
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        return TenantResponse.from_db(row)


@router.patch("/{slug}", response_model=TenantResponse, dependencies=[Depends(require_admin)])
async def update_tenant(slug: str, body: TenantUpdate):
    """Update tenant fields. Omit fields to leave them unchanged."""
    try:
        if body.crm_url:
            validate_external_url(body.crm_url, "crm_url")
        if body.agent_endpoint_url:
            validate_external_url(body.agent_endpoint_url, "agent_endpoint_url")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    with Session(database_service.engine) as session:
        row = session.exec(select(Tenant).where(Tenant.slug == slug)).first()
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found.")

        if body.display_name is not None:
            row.display_name = body.display_name
        if body.wa_access_token is not None:
            row.wa_access_token = encrypt(body.wa_access_token)
        if body.verify_token is not None:
            row.verify_token = encrypt(body.verify_token)
        if body.agent_type is not None:
            row.agent_type = body.agent_type
        if body.llm_model is not None:
            row.llm_model = body.llm_model
        if body.crm_url is not None:
            row.crm_url = body.crm_url
        if body.crm_token is not None:
            row.crm_token = encrypt(body.crm_token)
        if body.agent_endpoint_url is not None:
            row.agent_endpoint_url = body.agent_endpoint_url
        if body.agent_api_key:  # only update if non-empty string sent
            row.agent_api_key = encrypt(body.agent_api_key)
        if body.billing_model is not None:
            row.billing_model = body.billing_model
        if body.billing_tier is not None:
            row.billing_tier = body.billing_tier
        if body.msg_limit_month is not None:
            row.msg_limit_month = body.msg_limit_month
        if body.is_active is not None:
            row.is_active = body.is_active

        row.updated_at = datetime.now(UTC)
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info("admin_tenant_updated", slug=slug)
        await invalidate_tenant_cache(slug)
        return TenantResponse.from_db(row)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def deactivate_tenant(slug: str):
    """Soft-delete a tenant (sets is_active=False). Does not remove data."""
    with Session(database_service.engine) as session:
        row = session.exec(select(Tenant).where(Tenant.slug == slug)).first()
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        row.is_active = False
        row.updated_at = datetime.now(UTC)
        session.add(row)
        session.commit()
        logger.info("admin_tenant_deactivated", slug=slug)
        await invalidate_tenant_cache(slug)


@router.post("/{slug}/test", dependencies=[Depends(require_admin)])
async def test_tenant_credentials(slug: str):
    """Verify that the tenant's Meta and CRM credentials are valid."""
    with Session(database_service.engine) as session:
        row = session.exec(select(Tenant).where(Tenant.slug == slug)).first()
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found.")

    token = decrypt(row.wa_access_token)
    results: dict = {"slug": slug, "meta": {}, "crm": {}}

    # Test Meta Graph API
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://graph.facebook.com/v25.0/{row.phone_number_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            results["meta"] = {"status": resp.status_code, "ok": resp.is_success}
    except Exception as e:
        results["meta"] = {"ok": False, "error": str(e)}

    # Test CRM connectivity
    if row.crm_url:
        try:
            validate_external_url(row.crm_url, "crm_url")
            crm_token = decrypt(row.crm_token) if row.crm_token else ""
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                resp = await client.get(
                    f"{row.crm_url}/api/v1/leads",
                    params={"limit": 1},
                    headers={"Authorization": f"Bearer {crm_token}"},
                )
                results["crm"] = {"status": resp.status_code, "ok": resp.is_success}
        except ValueError as e:
            results["crm"] = {"ok": False, "error": str(e)}
        except Exception as e:
            results["crm"] = {"ok": False, "error": str(e)}

    logger.info("admin_tenant_test", slug=slug, results=str(results))
    return results
