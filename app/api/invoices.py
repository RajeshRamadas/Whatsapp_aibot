"""Workspace billing details and invoice downloads."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select

from ..config import settings
from ..db import Invoice, Session, Tenant
from ..invoice_config import STATES, BillingDetails
from ..services.invoices import pdf
from .principal import tenant_for_role

router = APIRouter(prefix='/api/v1/billing')


@router.get('/details')
def details(tenant: Tenant = Depends(tenant_for_role('owner'))):
    return {'details': tenant.billing_details, 'invoicing_enabled': settings.gst_invoice_issuer is not None,
            'states': STATES}


@router.put('/details')
def update_details(body: BillingDetails, tenant: Tenant = Depends(tenant_for_role('owner'))):
    with Session.begin() as db:
        db.get(Tenant, tenant.id, with_for_update=True).billing_details = body.model_dump()
    return body


@router.get('/invoices')
def list_invoices(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                  tenant: Tenant = Depends(tenant_for_role('owner'))):
    with Session() as db:
        rows = db.scalars(select(Invoice).where(Invoice.tenant_id == tenant.id)
                          .order_by(Invoice.issued_on.desc(), Invoice.number.desc()).offset(offset).limit(limit))
        return [{'id': row.id, 'number': row.number, 'issued_on': row.issued_on,
                 'gross_paise': row.snapshot['gross_paise']} for row in rows]


@router.get('/invoices/{invoice_id}/pdf')
def download(invoice_id: str, tenant: Tenant = Depends(tenant_for_role('owner'))):
    with Session() as db:
        invoice = db.scalar(select(Invoice).where(Invoice.tenant_id == tenant.id, Invoice.id == invoice_id))
    if invoice is None:
        raise HTTPException(404, 'Invoice not found')
    filename = invoice.number.replace('/', '-')
    return Response(pdf(invoice), media_type='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="invoice-{filename}.pdf"',
                             'Cache-Control': 'private, no-store'})
