"""Admin endpoints — DLQ management and conversation history."""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, distinct, func
from sqlmodel import Session, select

from app.api.admin.deps import require_admin
from app.core.broker import RedisStreamBroker, broker
from app.core.logging import logger
from app.models.chat_history_odonto import ChatHistoryOdonto
from app.models.tenant import Tenant
from app.services.database import database_service

router = APIRouter(prefix="/tenants", tags=["admin-conversations"])

_WA_SUFFIX = "@whatsapp.sofopolis.net"


def _wa_id(session_id: str) -> str:
    return session_id.removesuffix(_WA_SUFFIX)


def _session_id(wa_id: str) -> str:
    return f"{wa_id}{_WA_SUFFIX}"


def _parse_message(raw: str) -> dict | None:
    try:
        data = json.loads(raw)
        msg_type = data.get("type", "unknown")
        if msg_type == "tool":
            return None
        content = data.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        return {
            "role": "user" if msg_type == "human" else "assistant",
            "content": str(content).strip(),
            "has_tool_calls": bool(data.get("tool_calls")),
        }
    except (json.JSONDecodeError, AttributeError):
        return None


# ── DLQ ───────────────────────────────────────────────────────────────────────

@router.get("/{slug}/dlq", dependencies=[Depends(require_admin)])
async def list_dlq(slug: str):
    """List messages in the Dead Letter Queue for a tenant."""
    if not isinstance(broker, RedisStreamBroker):
        return {"slug": slug, "count": 0, "dlq": [], "backend": "in_memory"}

    items = await broker.dlq_list(slug)
    return {"slug": slug, "count": len(items), "dlq": items}


@router.post("/{slug}/dlq/{index}/retry", dependencies=[Depends(require_admin)])
async def retry_dlq_message(slug: str, index: int):
    """Re-enqueue a DLQ message by its list index."""
    if not isinstance(broker, RedisStreamBroker):
        raise HTTPException(status_code=503, detail="DLQ requires Redis Streams backend.")

    success = await broker.dlq_retry(slug, index)
    if not success:
        raise HTTPException(status_code=404, detail="Message not found in DLQ.")

    logger.info("admin_dlq_retried", slug=slug, index=index)
    return {"status": "requeued", "slug": slug, "index": index}


@router.delete("/{slug}/dlq/{index}", dependencies=[Depends(require_admin)])
async def discard_dlq_message(slug: str, index: int):
    """Permanently remove a message from the DLQ (no retry)."""
    if not isinstance(broker, RedisStreamBroker):
        raise HTTPException(status_code=503, detail="DLQ requires Redis Streams backend.")

    items = await broker._r.lrange(f"wa:dlq:{slug}", 0, -1)
    if index >= len(items):
        raise HTTPException(status_code=404, detail="Message not found in DLQ.")

    await broker._r.lset(f"wa:dlq:{slug}", index, "__discarded__")
    await broker._r.lrem(f"wa:dlq:{slug}", 1, "__discarded__")
    logger.info("admin_dlq_discarded", slug=slug, index=index)
    return {"status": "discarded", "slug": slug, "index": index}


# ── Conversations ─────────────────────────────────────────────────────────────

@router.get("/{slug}/conversations", dependencies=[Depends(require_admin)])
async def list_conversations(slug: str, limit: int = 50, offset: int = 0):
    """List all WhatsApp conversations for a tenant (paginated)."""
    with Session(database_service.engine) as db:
        tenant = db.exec(select(Tenant).where(Tenant.slug == slug)).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")

        rows = db.exec(
            select(
                ChatHistoryOdonto.session_id,
                func.count(ChatHistoryOdonto.id).label("message_count"),
                func.max(ChatHistoryOdonto.created_at).label("last_message_at"),
                func.min(ChatHistoryOdonto.created_at).label("first_message_at"),
            )
            .group_by(ChatHistoryOdonto.session_id)
            .order_by(desc(func.max(ChatHistoryOdonto.created_at)))
            .offset(offset)
            .limit(limit)
        ).all()

        total = db.exec(
            select(func.count(distinct(ChatHistoryOdonto.session_id)))
        ).one()

    return {
        "slug": slug,
        "total": total,
        "limit": limit,
        "offset": offset,
        "conversations": [
            {
                "wa_id": _wa_id(r.session_id),
                "session_id": r.session_id,
                "message_count": r.message_count,
                "last_message_at": r.last_message_at.isoformat(),
                "first_message_at": r.first_message_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.get("/{slug}/conversations/{wa_id}", dependencies=[Depends(require_admin)])
async def get_conversation(slug: str, wa_id: str):
    """Full message history for a WhatsApp conversation."""
    sid = _session_id(wa_id)

    with Session(database_service.engine) as db:
        tenant = db.exec(select(Tenant).where(Tenant.slug == slug)).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")

        rows = db.exec(
            select(ChatHistoryOdonto)
            .where(ChatHistoryOdonto.session_id == sid)
            .order_by(ChatHistoryOdonto.created_at)
        ).all()

    messages = []
    for r in rows:
        parsed = _parse_message(r.message)
        if parsed:
            messages.append({**parsed, "created_at": r.created_at.isoformat()})

    return {
        "wa_id": wa_id,
        "session_id": sid,
        "slug": slug,
        "message_count": len(messages),
        "messages": messages,
    }


@router.delete("/{slug}/conversations/{wa_id}", dependencies=[Depends(require_admin)])
async def clear_conversation(slug: str, wa_id: str):
    """Delete all messages in a WhatsApp conversation."""
    sid = _session_id(wa_id)

    with Session(database_service.engine) as db:
        rows = db.exec(
            select(ChatHistoryOdonto).where(ChatHistoryOdonto.session_id == sid)
        ).all()
        deleted = len(rows)
        for r in rows:
            db.delete(r)
        db.commit()

    logger.info("admin_conversation_cleared", slug=slug, wa_id=wa_id, deleted=deleted)
    return {"status": "cleared", "wa_id": wa_id, "messages_deleted": deleted}
