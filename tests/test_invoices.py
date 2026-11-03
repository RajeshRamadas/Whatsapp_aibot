from datetime import date
from decimal import Decimal

import pytest

from app.services.invoices import calculate_tax, financial_year


@pytest.mark.parametrize('supplier,recipient,cgst,sgst,utgst,igst', [
    ('27', '27', 9000, 9000, 0, 0),
    ('27', '29', 0, 0, 0, 18000),
    ('04', '04', 9000, 0, 9000, 0),
])
def test_inclusive_tax(supplier, recipient, cgst, sgst, utgst, igst):
    assert calculate_tax(118000, Decimal('18'), supplier, recipient) == {
        'gross_paise': 118000, 'taxable_paise': 100000, 'cgst_paise': cgst, 'sgst_paise': sgst,
        'utgst_paise': utgst, 'igst_paise': igst,
    }


def test_financial_year_rolls_on_april_first():
    assert financial_year(date(2026, 3, 31)) == 2025
    assert financial_year(date(2026, 4, 1)) == 2026
