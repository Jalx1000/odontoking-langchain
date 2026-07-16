"""Outbound WhatsApp gateway package.

Use get_gateway() to obtain the active provider, selected by settings.WHATSAPP_GATEWAY:
  - "sofo-crm"      → CrmGateway  (via Krayin CRM middleware)
  - "meta" / other  → MetaGateway (direct Meta Cloud API, default)
"""

from app.core.config import settings
from app.core.logging import logger
from app.services.gateway.base import Destination, MessageGateway
from app.services.gateway.crm import CrmGateway
from app.services.gateway.meta import MetaGateway

_gateway: MessageGateway | None = None


def get_gateway() -> MessageGateway:
    """Return the active outbound gateway (memoized singleton)."""
    global _gateway
    if _gateway is None:
        if settings.WHATSAPP_GATEWAY == "sofo-crm":
            _gateway = CrmGateway()
        else:
            _gateway = MetaGateway()
        logger.info("whatsapp_gateway_selected", gateway=_gateway.name, configured=settings.WHATSAPP_GATEWAY)
    return _gateway


__all__ = ["Destination", "MessageGateway", "get_gateway"]
