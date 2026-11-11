"""Owner/admin management of explicit, tenant-scoped lead sharing."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, StringConstraints, model_validator

from ..config import settings
from ..services import lead_sync as service
from ..services.appointments import AppointmentError
from .appointments import Confirm
from .principal import Principal, require_role

router = APIRouter(prefix='/api/v1/lead-sync', tags=['lead-sync'])
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Source = Literal['name', 'phone', 'email', 'need', 'budget', 'location', 'preferred_time', 'status',
                 'conversation_url', 'updated_at']
Destination = Annotated[str, StringConstraints(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,99}$')]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SyncCredentials(Input):
    provider: Literal['hubspot', 'zoho']
    label: Name
    region: Literal['in', 'us', 'eu', 'au'] = 'in'
    token: SecretStr | None = Field(None, min_length=8, max_length=8192)
    client_id: SecretStr | None = Field(None, min_length=8, max_length=500)
    client_secret: SecretStr | None = Field(None, min_length=8, max_length=500)
    refresh_token: SecretStr | None = Field(None, min_length=8, max_length=8192)

    @model_validator(mode='after')
    def required(self):
        if self.provider == 'hubspot':
            if not self.token or any((self.client_id, self.client_secret, self.refresh_token)):
                raise ValueError('Provide only the HubSpot private-app token')
        elif self.token or not all((self.client_id, self.client_secret, self.refresh_token)):
            raise ValueError('Provide the Zoho OAuth client ID, secret and refresh token')
        return self


class SyncConnectionView(BaseModel):
    id: str
    provider: str
    label: str
    region: str
    status: str
    enabled: bool
    key_last4: str | None
    config: dict
    catalog: dict
    last_success_at: datetime | None
    last_error: str | None


class SyncConnections(BaseModel):
    google_configured: bool
    sources: list[str]
    connections: list[SyncConnectionView]


class SyncConfig(Input):
    enabled: StrictBool
    confirm_sharing: StrictBool = False
    mapping: dict[Source, Destination] = Field(default_factory=dict, max_length=10)
    spreadsheet_id: Annotated[str, StringConstraints(pattern=r'^[a-zA-Z0-9_-]{1,200}$')] | None = None
    tab_id: Annotated[int, Field(strict=True, ge=0, le=2147483647)] | None = None
    include_notes: StrictBool = False


class SyncDiscovery(Input):
    spreadsheet_id: Annotated[str, StringConstraints(pattern=r'^[a-zA-Z0-9_-]{1,200}$')] | None = None


class SyncSheet(Input):
    title: Name


class SyncBackfill(Confirm):
    lead_ids: list[UUID] = Field(min_length=1, max_length=100)


class SyncJob(BaseModel):
    id: str
    conversation_id: str | None
    kind: str
    lead_version: int
    state: str
    attempts: int
    last_error: str | None
    created_at: datetime


class SyncQueued(BaseModel):
    queued: int


def run(p, action, *args, **kwargs):
    try:
        return action(p.tenant.id, *args, **kwargs)
    except AppointmentError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except ValueError:
        raise HTTPException(503, 'Connector encryption is unavailable') from None


def actor(p):
    return ('user', p.user.id) if p.user else ('api_key', None)


@router.get('', response_model=SyncConnections)
def connections(p: Principal = Depends(require_role('admin'))):
    return {'connections': service.connections(p.tenant.id), 'sources': list(service.SOURCES),
            'google_configured': bool(settings.lead_sync_google_client_id and settings.lead_sync_google_client_secret
                                      and settings.lead_sync_google_redirect_uri and settings.master_key)}


@router.post('/connections', response_model=SyncConnectionView, status_code=201)
def connect(body: SyncCredentials, p: Principal = Depends(require_role('admin'))):
    credentials = {k: getattr(body, k).get_secret_value() for k in (
        'token', 'client_id', 'client_secret', 'refresh_token')
                   if getattr(body, k) is not None}
    return run(p, service.connect, body.provider, body.label, body.region, credentials, actor=actor(p))


@router.put('/connections/{identity}', response_model=SyncConnectionView)
def configure(identity: UUID, body: SyncConfig, p: Principal = Depends(require_role('admin'))):
    return run(p, service.configure, str(identity), body.model_dump(), actor=actor(p))


@router.post('/connections/{identity}/discover', response_model=SyncConnectionView)
def discover(identity: UUID, body: SyncDiscovery, p: Principal = Depends(require_role('admin'))):
    return run(p, service.action, str(identity), 'discover', options=body.model_dump(exclude_none=True), actor=actor(p))


@router.post('/connections/{identity}/spreadsheet', response_model=SyncConnectionView)
def spreadsheet(identity: UUID, body: SyncSheet, p: Principal = Depends(require_role('admin'))):
    return run(p, service.action, str(identity), 'create_sheet', options=body.model_dump(), actor=actor(p))


@router.delete('/connections/{identity}', response_model=SyncConnectionView)
def disconnect(identity: UUID, body: Confirm, p: Principal = Depends(require_role('admin'))):
    return run(p, service.action, str(identity), 'disconnect', actor=actor(p))


@router.get('/connections/{identity}/jobs', response_model=list[SyncJob])
def jobs(identity: UUID, p: Principal = Depends(require_role('admin'))):
    return run(p, service.jobs, str(identity))


@router.post('/connections/{identity}/backfill', response_model=SyncQueued)
def backfill(identity: UUID, body: SyncBackfill, p: Principal = Depends(require_role('admin'))):
    return run(p, service.backfill, str(identity), [str(i) for i in body.lead_ids], actor=actor(p))


@router.post('/connections/{identity}/jobs/{job_id}/retry', response_model=SyncQueued)
def retry(identity: UUID, job_id: UUID, body: Confirm, p: Principal = Depends(require_role('admin'))):
    return run(p, service.retry, str(identity), str(job_id), actor=actor(p))
