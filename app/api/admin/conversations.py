"""Admin endpoints — DLQ management and conversation history."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.admin.deps import require_admin
from app.core.broker import RedisStreamBroker, broker
from app.core.logging import logger

router = APIRouter(prefix="/tenants", tags=["admin-conversations"])


@router.get("/{slug}/dlq", dependencies=[Depends(require_admin)])
async def list_dlq(slug: str):
    """List messages in the Dead Letter Queue for a tenant."""
    if not isinstance(broker, RedisStreamBroker):
        return {"dlq": [], "backend": "in_memory"}

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
