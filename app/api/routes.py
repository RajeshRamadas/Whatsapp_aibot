from fastapi import APIRouter

from . import (
    api_keys,
    audit,
    auth,
    bot,
    chat,
    conversations,
    evals,
    events,
    jobs,
    knowledge,
    onboarding,
    tenants,
    whatsapp,
    widget,
    workspaces,
)
from .shared import UPLOADS as UPLOADS
from .shared import origins as origins

router = APIRouter()
for module in (auth, audit, api_keys, workspaces, tenants, knowledge, onboarding, bot, jobs, chat, conversations,
               evals, events, whatsapp, widget):
    router.include_router(module.router)
