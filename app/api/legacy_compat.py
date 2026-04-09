"""
Маршруты под старые пути из scripts/test_api.py и документации (/api/health, /api/devices/...).

Версионированное API: /api/v1, /api/v2.
"""
import time
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["legacy-compat"])


@router.get("/health")
async def api_health():
    """Алиас для проверок, ожидающих /api/health (корневой /health без префикса остаётся)."""
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "version": "2.0.0",
    }


@router.get("/devices")
async def api_devices_list():
    """Список устройств без tenant-авторизации (дымовой тест). Для продакшена — /api/v1/devices."""
    return {
        "devices": [],
        "message": "Use GET /api/v1/devices with Bearer API key for tenant-scoped data.",
    }


@router.get("/devices/{unique_id}/positions")
async def api_device_positions(unique_id: str):
    return {"device_id": unique_id, "positions": []}


@router.get("/devices/{unique_id}/last")
async def api_device_last(unique_id: str):
    return {"device_id": unique_id, "position": None}


@router.get("/devices/{unique_id}/can")
async def api_device_can(unique_id: str):
    return {"device_id": unique_id, "can": []}


@router.get("/devices/{unique_id}/frames")
async def api_device_frames(unique_id: str):
    return {"device_id": unique_id, "frames": []}
