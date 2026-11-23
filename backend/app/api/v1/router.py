from fastapi import APIRouter

from app.api.v1 import browse, reviews, share, system

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(browse.router)
api_router.include_router(share.router)
api_router.include_router(reviews.router)
