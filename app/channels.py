"""Channel-neutral message contracts for future WhatsApp and other adapters.

A channel adapter should authenticate and normalize inbound events, then invoke
the shared chat service. The current FastAPI chat endpoint is the working web
transport; refactor its orchestration into a service before adding WhatsApp.
"""
from dataclasses import dataclass
from typing import Literal

Channel = Literal["web", "whatsapp"]

@dataclass(frozen=True)
class InboundMessage:
    tenant_id: str
    channel: Channel
    external_user_id: str
    text: str
    external_message_id: str
    conversation_id: str | None = None

@dataclass(frozen=True)
class OutboundMessage:
    channel: Channel
    external_user_id: str
    text: str
    conversation_id: str
