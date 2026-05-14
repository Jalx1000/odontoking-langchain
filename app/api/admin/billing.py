"""Admin billing endpoints — monthly usage reports and CSV export."""

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, func, select

from app.api.admin.deps import require_admin
from app.models.tenant import Tenant
from app.models.usage_log import UsageLog
from app.services.database import database_service

router = APIRouter(prefix="/billing", tags=["admin-billing"])


def _parse_month(month_str: str) -> tuple[date, date]:
    """Parse 'YYYY-MM' into (first_day, last_day) of that month."""
    try:
        year, m = map(int, month_str.split("-"))
        first = date(year, m, 1)
        # Last day: first day of next month - 1 day
        if m == 12:
            last = date(year + 1, 1, 1)
        else:
            last = date(year, m + 1, 1)
        return first, last
    except Exception:
        raise HTTPException(status_code=422, detail="month must be in YYYY-MM format.")


@router.get("/{slug}", dependencies=[Depends(require_admin)])
async def tenant_billing_report(slug: str, month: str):
    """Monthly billing report for one tenant.

    Args:
        month: Period in YYYY-MM format (e.g. 2026-05).
    """
    first, last = _parse_month(month)

    with Session(database_service.engine) as session:
        tenant = session.exec(select(Tenant).where(Tenant.slug == slug)).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")

        agg = session.exec(
            select(
                func.sum(UsageLog.msg_received).label("received"),
                func.sum(UsageLog.msg_processed).label("processed"),
                func.sum(UsageLog.msg_failed).label("failed"),
                func.sum(UsageLog.tokens_input).label("tokens_in"),
                func.sum(UsageLog.tokens_output).label("tokens_out"),
                func.sum(UsageLog.cost_usd).label("cost"),
            ).where(
                UsageLog.tenant_id == tenant.id,
                UsageLog.log_date >= first,
                UsageLog.log_date < last,
            )
        ).first()

    received = int(agg.received or 0) if agg else 0
    processed = int(agg.processed or 0) if agg else 0
    failed = int(agg.failed or 0) if agg else 0
    cost = float(agg.cost or 0) if agg else 0.0

    # Over-limit detection
    over_limit = received > tenant.msg_limit_month

    return {
        "slug": slug,
        "display_name": tenant.display_name,
        "month": month,
        "billing_model": tenant.billing_model,
        "billing_tier": tenant.billing_tier,
        "msg_limit_month": tenant.msg_limit_month,
        "summary": {
            "msg_received": received,
            "msg_processed": processed,
            "msg_failed": failed,
            "tokens_input": int(agg.tokens_in or 0) if agg else 0,
            "tokens_output": int(agg.tokens_out or 0) if agg else 0,
            "cost_usd": round(cost, 4),
        },
        "over_limit": over_limit,
        "over_limit_by": max(0, received - tenant.msg_limit_month),
    }


@router.get("/export/csv", dependencies=[Depends(require_admin)])
async def export_all_billing_csv(month: str):
    """Export a CSV with all tenants' billing summary for a given month."""
    first, last = _parse_month(month)

    with Session(database_service.engine) as session:
        tenants = session.exec(select(Tenant).where(Tenant.is_active == True)).all()
        rows = []
        for t in tenants:
            agg = session.exec(
                select(
                    func.sum(UsageLog.msg_received),
                    func.sum(UsageLog.msg_processed),
                    func.sum(UsageLog.msg_failed),
                    func.sum(UsageLog.cost_usd),
                ).where(
                    UsageLog.tenant_id == t.id,
                    UsageLog.log_date >= first,
                    UsageLog.log_date < last,
                )
            ).first()
            rows.append({
                "slug": t.slug,
                "display_name": t.display_name,
                "billing_tier": t.billing_tier,
                "billing_model": t.billing_model,
                "msg_limit": t.msg_limit_month,
                "msg_received": int(agg[0] or 0) if agg else 0,
                "msg_processed": int(agg[1] or 0) if agg else 0,
                "msg_failed": int(agg[2] or 0) if agg else 0,
                "cost_usd": float(agg[3] or 0) if agg else 0.0,
            })

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()) if rows else [])
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=billing_{month}.csv"},
    )
