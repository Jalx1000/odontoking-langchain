"""Admin API dependencies — authentication and common utilities."""

import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings
from app.core.logging import logger


async def require_admin(x_admin_key: str = Header(default="")) -> None:
    """Validate the static admin API key sent in X-Admin-Key header.

    All /admin endpoints call this dependency. The key is set via ADMIN_API_KEY env var.
    """
    if not settings.ADMIN_API_KEY:
        logger.warning("admin_api_key_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API is not configured. Set ADMIN_API_KEY.",
        )
    if not hmac.compare_digest(x_admin_key, settings.ADMIN_API_KEY):
        logger.warning("admin_unauthorized_attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin API key.",
        )
