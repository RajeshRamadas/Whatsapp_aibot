"""Bounded tabular imports with conservative types and exact decimal values."""
import csv
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from ..config import settings

NUMBER = re.compile(r'^[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,2}(?:,[0-9]{2})*,[0-9]{3})'
                    r'(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,3})?$')


@dataclass
class Sheet:
    title: str
    columns: list[dict]
    rows: list[list]


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError()
    raw = str(value).strip()
    if raw.startswith(('₹', '$', '€', '£')):
        raw = raw[1:].strip()
    if not NUMBER.fullmatch(raw):
        raise ValueError()
    result = Decimal(raw.replace(',', ''))
    if not result.is_finite() or abs(result) > 10**24 or result.as_tuple().exponent < -12:
        raise ValueError()
    return result


def iso_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        return date.fromisoformat(value)
    raise ValueError()


def boolean(value):
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    raise ValueError()


def convert(values):
    if not any(v is not None for v in values):
        return 'text', values
    currencies = {str(v).strip()[0] for v in values if v is not None and str(v).strip()
                  and str(v).strip()[0] in '₹$€£'}
    for kind, parser in [('boolean', boolean), ('numeric', number), ('date', iso_date)]:
        if kind == 'numeric' and len(currencies) > 1:
            continue
        try:
            return kind, [None if v is None else parser(v) for v in values]
        except (ValueError, TypeError, InvalidOperation):
            pass
    return 'text', [None if v is None else str(v) for v in values]


def sheet(title, records, budget):
    headers = next(records, None)
    if not headers or not any(v is not None and str(v).strip() for v in headers):
        return None
    if len(headers) > 60:
        raise ValueError('Spreadsheets support up to 60 columns per sheet.')
    if any('\x00' in str(v) for v in headers):
        raise ValueError('Spreadsheet headers cannot contain null characters.')
    columns = [{'name': f'c{i + 1}', 'label': str(v).strip()[:200] if v is not None and str(v).strip()
                else f'Column {i + 1}'} for i, v in enumerate(headers)]
    rows = []
    for record in records:
        budget[0] += 1
        if budget[0] > settings.spreadsheet_max_rows:
            raise ValueError('This spreadsheet exceeds the row limit. Trim unused or blank rows before upload.')
        if len(record) > len(headers) and any(v not in (None, '') for v in record[len(headers):]):
            raise ValueError('A spreadsheet row has more values than its header. Add a complete header row.')
        values = [None if v is None or v == '' else v for v in record[:len(headers)]]
        values += [None] * (len(headers) - len(values))
        if not any(v is not None for v in values):
            continue
        if any(len(str(v)) > 2000 or '\x00' in str(v) for v in values if v is not None):
            raise ValueError('Spreadsheet cells must contain at most 2000 characters and no null characters.')
        budget[1] += len(values)
        if budget[0] > settings.spreadsheet_max_rows or budget[1] > 250000:
            raise ValueError('This spreadsheet exceeds the configured row or 250000-cell import limit.')
        rows.append(values)
    for i, column in enumerate(columns):
        kind, values = convert([row[i] for row in rows]) if rows else ('text', [])
        column['type'] = kind
        if kind == 'numeric':
            symbols = {str(row[i]).strip()[0] for row in rows if row[i] is not None and str(row[i]).strip()
                       and str(row[i]).strip()[0] in '₹$€£'}
            column['currency'] = next(iter(symbols), None)
        for row, value in zip(rows, values, strict=True):
            row[i] = value
    return Sheet(title[:200], columns, rows)


def parse(path):
    budget, result = [0, 0], []
    try:
        if path.suffix.lower() == '.csv':
            with path.open(encoding='utf-8-sig', newline='') as stream:
                value = sheet('CSV', iter(csv.reader(stream, strict=True)), budget)
                if value:
                    result.append(value)
        else:
            with zipfile.ZipFile(path) as archive:
                if sum(v.file_size for v in archive.infolist()) > 50 * 1024 * 1024:
                    raise ValueError('The expanded workbook exceeds 50 MB. Split it into smaller files.')
            workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
            try:
                if len(workbook.worksheets) > 12:
                    raise ValueError('Workbooks support up to 12 sheets.')
                for tab in workbook:
                    def records(tab=tab):
                        for cells in tab.iter_rows():
                            if any(c.data_type in ('f', 'e') for c in cells):
                                raise ValueError('Replace workbook formulas and error cells '
                                                 'with saved values before upload.')
                            values = []
                            for cell in cells:
                                value = cell.value
                                units = re.findall(r'\[\$([₹$€£])(?:-[^]]*)?\]|(?<![\[a-zA-Z])([₹$€£])',
                                                   cell.number_format or '')
                                unit = next((a or b for a, b in units), None)
                                if unit and type(value) in (int, float, Decimal):
                                    value = unit + str(value)
                                values.append(value)
                            yield values
                    value = sheet(tab.title, iter(records()), budget)
                    if value:
                        result.append(value)
            finally:
                workbook.close()
    except (csv.Error, UnicodeError, zipfile.BadZipFile, OSError):
        raise ValueError('The spreadsheet could not be read. Upload a UTF-8 CSV or a valid XLSX workbook.') from None
    if not result:
        raise ValueError('Add a nonempty header row to the spreadsheet.')
    return result
