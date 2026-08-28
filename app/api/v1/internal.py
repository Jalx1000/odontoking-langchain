"""Internal API - endpoints called by agent services, not external clients."""

import hmac
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.config import settings
from app.core.logging import logger
from app.models.tenant import Tenant
from app.models.usage_log import UsageLog
from app.services.database import database_service

router = APIRouter(prefix="/internal", tags=["internal"])


class UsageReport(BaseModel):
    """Usage telemetry sent by an agent service after processing a message."""

    tenant_slug: str
    wa_id: str
    tokens_input: int = Field(default=0, ge=0)
    tokens_output: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    msg_processed: int = Field(default=1, ge=0)


async def _require_internal_key(x_internal_key: str = Header(default="")) -> None:
    """Validate X-Internal-Key header."""
    if not settings.INTERNAL_API_KEY:
        logger.warning("internal_api_key_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API is not configured. Set INTERNAL_API_KEY.",
        )
    if not hmac.compare_digest(x_internal_key, settings.INTERNAL_API_KEY):
        logger.warning("internal_unauthorized_attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal API key.",
        )


@router.post("/usage", dependencies=[Depends(_require_internal_key)])
async def report_usage(body: UsageReport) -> dict:
    """Receive usage telemetry from an agent service and upsert into usage_logs."""
    today = date.today()

    with Session(database_service.engine) as session:
        tenant = session.exec(
            select(Tenant).where(
                Tenant.slug == body.tenant_slug,
                Tenant.is_active == True,  # noqa: E712
            )
        ).first()
        if not tenant:
            logger.warning("internal_usage_unknown_tenant", slug=body.tenant_slug)
            raise HTTPException(status_code=404, detail="Tenant not found.")

        log = session.exec(
            select(UsageLog).where(
                UsageLog.tenant_id == tenant.id,
                UsageLog.log_date == today,
            )
        ).first()

        if log is None:
            log = UsageLog(tenant_id=tenant.id, log_date=today)
            session.add(log)

        log.msg_processed += body.msg_processed
        log.tokens_input += body.tokens_input
        log.tokens_output += body.tokens_output
        log.cost_usd = round(log.cost_usd + body.cost_usd, 6)
        log.updated_at = datetime.now(UTC)
        session.commit()

    logger.info(
        "internal_usage_recorded",
        tenant=body.tenant_slug,
        tokens_in=body.tokens_input,
        tokens_out=body.tokens_output,
        cost=body.cost_usd,
    )
    return {"status": "ok"}
