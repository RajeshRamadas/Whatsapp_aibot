"""Calculate, number and render invoices from captured subscription charges."""
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..config import settings
from ..db import Invoice, InvoiceSequence, now
from ..invoice_config import STATES, UNION_TERRITORY_TAX

INDIA = ZoneInfo('Asia/Kolkata')


def calculate_tax(gross_paise: int, rate: Decimal, issuer_state: str, recipient_state: str) -> dict:
    """Back GST out of the captured inclusive amount, rounding once to the nearest paise."""
    if gross_paise <= 0 or not rate.is_finite() or rate < 0:
        raise ValueError('Invalid charge or tax rate')
    tax = int((Decimal(gross_paise) * rate / (100 + rate)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    local = issuer_state == recipient_state
    central = tax // 2 if local else 0
    regional = tax - central if local else 0
    return {'gross_paise': gross_paise, 'taxable_paise': gross_paise - tax, 'cgst_paise': central,
            'sgst_paise': regional if issuer_state not in UNION_TERRITORY_TAX else 0,
            'utgst_paise': regional if issuer_state in UNION_TERRITORY_TAX else 0,
            'igst_paise': 0 if local else tax}


def financial_year(day):
    return day.year if day.month >= 4 else day.year - 1


def issue(db, tenant, payment):
    """Called with the workspace locked by the signed charge transaction."""
    issuer = settings.gst_invoice_issuer
    amount = payment.get('amount')
    if (issuer is None or not tenant.billing_details or payment.get('currency') != 'INR'
            or payment.get('status') != 'captured' or not isinstance(amount, int) or amount <= 0):
        return
    payment_id = payment.get('id')
    if not isinstance(payment_id, str) or not 1 <= len(payment_id) <= 64:
        return
    if db.scalar(select(Invoice.id).where(Invoice.payment_id == payment_id)):
        return
    day = now().astimezone(INDIA).date()
    year = financial_year(day)
    sequence = insert(InvoiceSequence).values(financial_year=year, last_number=1)
    number = db.scalar(sequence.on_conflict_do_update(
        index_elements=['financial_year'], set_={'last_number': InvoiceSequence.last_number + 1},
    ).returning(InvoiceSequence.last_number))
    recipient = tenant.billing_details
    amounts = calculate_tax(amount, issuer.tax_rate, issuer.state, recipient['state'])
    db.add(Invoice(tenant_id=tenant.id, payment_id=payment_id,
                   number=f'{year % 100:02d}-{(year + 1) % 100:02d}/{number:06d}', issued_on=day,
                   snapshot={'issuer': issuer.model_dump(mode='json'), 'recipient': recipient,
                             'description': 'Chatbot subscription service', **amounts}))


def money(paise):
    return f'INR {Decimal(paise) / 100:,.2f}'


def pdf(invoice):
    snapshot = invoice.snapshot
    issuer = snapshot['issuer']
    recipient = snapshot['recipient']
    styles = getSampleStyleSheet()
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, title='Tax invoice ' + invoice.number)

    def paragraph(text, style='Normal'):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), styles[style])

    def party(title, details):
        return [
            paragraph(title, 'Heading2'), paragraph(details['name']), paragraph(details['address']),
            paragraph(f"{STATES[details['state']]} ({details['state']}) {details['postal_code']}, India"),
            paragraph('GSTIN ' + details['gstin'] if details.get('gstin') else 'Unregistered recipient'),
        ]

    rows = [
        ['Description', 'Amount'],
        [snapshot['description'] + ' · SAC ' + issuer['sac'], money(snapshot['taxable_paise'])],
    ]
    rate = Decimal(issuer['tax_rate'])
    if issuer['state'] == recipient['state']:
        regional = 'UTGST' if issuer['state'] in UNION_TERRITORY_TAX else 'SGST'
        rows.append([f'CGST ({rate / 2:g}%)', money(snapshot['cgst_paise'])])
        rows.append([f'{regional} ({rate / 2:g}%)', money(snapshot[regional.lower() + '_paise'])])
    else:
        rows.append([f'IGST ({rate:g}%)', money(snapshot['igst_paise'])])
    rows.append(['Total paid', money(snapshot['gross_paise'])])
    table = Table(rows, colWidths=[310, 125], hAlign='LEFT', style=[
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e5eeeb')),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'), ('LINEABOVE', (0, -1), (-1, -1), 1, colors.grey),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ])
    document.build([
        paragraph('Tax invoice', 'Title'),
        paragraph(f'{invoice.number} · Issued {invoice.issued_on.isoformat()}'),
        paragraph('Razorpay payment ' + invoice.payment_id),
        *party('Supplier', issuer), *party('Bill to', recipient), Spacer(1, 10),
        paragraph(f"Place of supply: {STATES[recipient['state']]} ({recipient['state']})"),
        paragraph('Reverse charge: No'), Spacer(1, 12), table,
    ])
    return buffer.getvalue()
