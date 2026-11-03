from fastapi import APIRouter

from . import (
    api_keys,
    audit,
    auth,
    bot,
    channels,
    chat,
    conversations,
    evals,
    events,
    inbox,
    jobs,
    knowledge,
    leads,
    meta_signup,
    onboarding,
    push,
    qa,
    templates,
    tenants,
    whatsapp,
    widget,
    workspaces,
)
from .shared import UPLOADS as UPLOADS
from .shared import origins as origins

router = APIRouter()
for module in (auth, audit, api_keys, workspaces, tenants, channels, knowledge, onboarding, bot, jobs,
               chat, conversations, evals, events, inbox, leads, meta_signup, push, qa, templates, whatsapp, widget):
    router.include_router(module.router)
