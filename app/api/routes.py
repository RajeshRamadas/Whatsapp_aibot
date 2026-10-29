from fastapi import APIRouter

from . import chat, conversations, evals, knowledge, tenants, whatsapp, widget
from .shared import UPLOADS as UPLOADS, origins as origins

router = APIRouter()
for module in (tenants, knowledge, chat, conversations, evals, whatsapp, widget):
    router.include_router(module.router)
