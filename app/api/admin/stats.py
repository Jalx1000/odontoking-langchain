"""Admin monitoring endpoints — queue depths, error rates, conversation stats."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from app.api.admin.deps import require_admin
from app.core.broker import broker, RedisStreamBroker
from app.models.tenant import Tenant
from app.models.usage_log import UsageLog
from app.services.database import database_service

router = APIRouter(prefix="/stats", tags=["admin-stats"])


@router.get("", dependencies=[Depends(require_admin)])
async def global_stats():
    """Overview: all tenants, message totals, and queue depths for the last 30 days."""
    today = date.today()
    since = today - timedelta(days=30)

    with Session(database_service.engine) as session:
        tenants = session.exec(select(Tenant).where(Tenant.is_active == True)).all()

        # Aggregate usage_logs per tenant
        rows = session.exec(
            select(
                UsageLog.tenant_id,
                func.sum(UsageLog.msg_received).label("received"),
                func.sum(UsageLog.msg_processed).label("processed"),
                func.sum(UsageLog.msg_failed).label("failed"),
                func.sum(UsageLog.cost_usd).label("cost_usd"),
            )
            .where(UsageLog.log_date >= since)
            .group_by(UsageLog.tenant_id)
        ).all()

        usage_by_tid = {str(r.tenant_id): r for r in rows}

    tenant_list = []
    for t in tenants:
        u = usage_by_tid.get(str(t.id))
        tenant_list.append({
            "slug": t.slug,
            "display_name": t.display_name,
            "billing_tier": t.billing_tier,
            "is_active": t.is_active,
            "last_30_days": {
                "msg_received": int(u.received or 0) if u else 0,
                "msg_processed": int(u.processed or 0) if u else 0,
                "msg_failed": int(u.failed or 0) if u else 0,
                "cost_usd": float(u.cost_usd or 0) if u else 0.0,
            },
        })

    return {"tenants": tenant_list, "period_days": 30}


@router.get("/{slug}", dependencies=[Depends(require_admin)])
async def tenant_stats(slug: str, days: int = 30):
    """Per-tenant stats: daily message breakdown + current queue depth."""
    since = date.today() - timedelta(days=days)

    with Session(database_service.engine) as session:
        tenant = session.exec(select(Tenant).where(Tenant.slug == slug)).first()
        if not tenant:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Tenant not found.")

        daily = session.exec(
            select(UsageLog)
            .where(UsageLog.tenant_id == tenant.id, UsageLog.log_date >= since)
            .order_by(UsageLog.log_date)
        ).all()

    # Queue depth from Redis Streams
    queue_depth: int = 0
    dlq_depth: int = 0
    if isinstance(broker, RedisStreamBroker):
        try:
            info = await broker._r.xinfo_stream(f"wa:{slug}")
            queue_depth = info.get("length", 0)
            dlq_raw = await broker._r.llen(f"wa:dlq:{slug}")
            dlq_depth = dlq_raw or 0
        except Exception:
            pass

    return {
        "slug": slug,
        "display_name": tenant.display_name,
        "queue_depth": queue_depth,
        "dlq_depth": dlq_depth,
        "daily": [
            {
                "date": str(r.date),
                "msg_received": r.msg_received,
                "msg_processed": r.msg_processed,
                "msg_failed": r.msg_failed,
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
                "cost_usd": float(r.cost_usd),
            }
            for r in daily
        ],
    }
