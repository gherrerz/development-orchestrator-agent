from fastapi import APIRouter
from app.api.credit import router as credit_router

api_router = APIRouter()
api_router.include_router(credit_router, prefix="/credit", tags=["credit"])
