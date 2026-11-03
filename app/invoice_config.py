"""Validated domestic GST invoice settings."""
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

STATES = {
    '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab', '04': 'Chandigarh',
    '05': 'Uttarakhand', '06': 'Haryana', '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh',
    '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur',
    '15': 'Mizoram', '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam', '19': 'West Bengal',
    '20': 'Jharkhand', '21': 'Odisha', '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
    '26': 'Dadra and Nagar Haveli and Daman and Diu', '27': 'Maharashtra', '29': 'Karnataka',
    '30': 'Goa', '31': 'Lakshadweep', '32': 'Kerala', '33': 'Tamil Nadu', '34': 'Puducherry',
    '35': 'Andaman and Nicobar Islands', '36': 'Telangana', '37': 'Andhra Pradesh', '38': 'Ladakh',
}
UNION_TERRITORY_TAX = {'04', '26', '31', '35', '38'}
GSTIN = Annotated[str, StringConstraints(
    strip_whitespace=True, to_upper=True, pattern=r'^[0-9]{2}[A-Za-z]{5}[0-9]{4}[A-Za-z][1-9A-Za-z][Zz][0-9A-Za-z]$',
)]


class BillingDetails(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    address: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
    state: str = Field(pattern=r'^[0-9]{2}$')
    postal_code: str = Field(pattern=r'^[1-9][0-9]{5}$')
    gstin: GSTIN | None = None

    @model_validator(mode='after')
    def validate_place(self):
        if self.state not in STATES:
            raise ValueError('Select a supported Indian state or union territory')
        if self.gstin and self.gstin[:2] != self.state:
            raise ValueError('GSTIN must match the selected state')
        return self


class InvoiceIssuer(BillingDetails):
    gstin: GSTIN
    sac: str = Field(pattern=r'^[0-9]{6}$')
    tax_rate: Decimal = Field(ge=0, le=100, decimal_places=2)
