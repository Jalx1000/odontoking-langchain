"""Admin API router — aggregates all admin sub-routers under /admin."""

from fastapi import APIRouter

from app.api.admin.billing import router as billing_router
from app.api.admin.conversations import router as conversations_router
from app.api.admin.stats import router as stats_router
from app.api.admin.tenants import router as tenants_router

admin_router = APIRouter(prefix="/admin")

admin_router.include_router(tenants_router)
admin_router.include_router(stats_router)
admin_router.include_router(conversations_router)
admin_router.include_router(billing_router)
