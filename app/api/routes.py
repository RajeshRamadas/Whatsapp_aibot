from fastapi import APIRouter

from . import api_keys, auth, chat, conversations, evals, knowledge, tenants, whatsapp, widget, workspaces
from .shared import UPLOADS as UPLOADS
from .shared import origins as origins

router = APIRouter()
for module in (auth, api_keys, workspaces, tenants, knowledge, chat, conversations, evals, whatsapp, widget):
    router.include_router(module.router)
