from fastapi import APIRouter

from app.api.dependencies import AppSettings
from app.services.capabilities import CapabilityService

router = APIRouter()


@router.get("")
def capabilities(settings: AppSettings) -> dict:
    return CapabilityService(settings).report()
