"""A small SQL subset, a tenant-only role, read-only transactions and bounded results."""
import json
import math
import re
import time
from datetime import date, datetime
from decimal import Decimal

import sqlglot
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlglot import exp
from sqlglot.errors import ErrorLevel

from ..config import settings
from ..db import Session, Source, engine
from ..models.spreadsheets import SpreadsheetTable
from ..services.audit import audit
from .store import q, role, schema

ALLOWED = {getattr(exp, name) for name in (
    'Select From Table TableAlias Identifier Column Star Literal Null Boolean Alias Where And Or Not Paren '
    'EQ NEQ GT GTE LT LTE Is In Between Like ILike Order Ordered Group Having Limit Offset Distinct '
    'Count Sum Avg Min Max Abs Round Coalesce Nullif Lower Upper Trim Length Cast DataType DataTypeParam '
    'Add Sub Mul Div Mod Neg Case If').split()}
CASTS = {'TEXT', 'VARCHAR', 'DECIMAL', 'BIGDECIMAL', 'INT', 'BIGINT', 'SMALLINT', 'FLOAT', 'DOUBLE', 'BOOLEAN', 'DATE'}


class QueryError(ValueError):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)


def owned(db, tenant_id, identity):
    pair = db.execute(select(SpreadsheetTable, Source).join(Source, Source.id == SpreadsheetTable.source_id)
        .where(SpreadsheetTable.id == str(identity), SpreadsheetTable.tenant_id == tenant_id,
               Source.tenant_id == tenant_id)).first()
    if pair is None:
        raise QueryError('table_not_found', 404)
    return pair


def guarded(sql, table, tenant_id, row_limit):
    if not isinstance(sql, str) or not 1 <= len(sql) <= 4000:
        raise QueryError('invalid_query')
    try:
        statements = sqlglot.parse(sql, read='postgres', error_level=ErrorLevel.RAISE, error_message_context=0)
        tree = statements[0] if len(statements) == 1 else None
        nodes = list(tree.walk()) if isinstance(tree, exp.Select) else []
        if (not nodes or len(nodes) > 300 or any(type(n) not in ALLOWED for n in nodes)
                or sum(isinstance(n, exp.Select) for n in nodes) != 1):
            raise QueryError('select_only')
        references = list(tree.find_all(exp.Table))
        if len(references) != 1:
            raise QueryError('single_table_required')
        reference = references[0]
        if (reference.name != table.table_name or reference.catalog
                or reference.db not in ('', schema(tenant_id))):
            raise QueryError('table_not_found', 404)
        # Reject alias/schema/function modifiers even if future parser versions accept them.
        if any(k not in ('this', 'db', 'catalog', 'alias') and v for k, v in reference.args.items()):
            raise QueryError('invalid_query')
        if reference.args.get('alias') and reference.args['alias'].args.get('columns'):
            raise QueryError('invalid_alias')
        columns = {c['name'] for c in table.columns}
        aliases = {n.alias for n in tree.find_all(exp.Alias)}
        if any(not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', name) for name in aliases):
            raise QueryError('invalid_alias')
        for column in tree.find_all(exp.Column):
            if (column.name not in columns | aliases | {'*'} or column.db or column.catalog
                    or column.table not in ('', reference.alias_or_name)):
                raise QueryError('unknown_column')
        for node in nodes:
            if isinstance(node, exp.Identifier) and len(node.name) > 80:
                raise QueryError('invalid_identifier')
            if isinstance(node, exp.DataType) and node.this.value not in CASTS:
                raise QueryError('unsupported_cast')
            if isinstance(node, exp.Star) and not isinstance(node.parent, (exp.Select, exp.Column, exp.Count)):
                raise QueryError('invalid_query')
            if isinstance(node, exp.Literal) and len(str(node.this)) > 2000:
                raise QueryError('invalid_literal')
        expressions = []
        for expression in tree.expressions:
            if isinstance(expression, exp.Star) or isinstance(expression, exp.Column) and expression.is_star:
                expressions.extend(exp.column(c['name']) for c in table.columns)
            else:
                expressions.append(expression)
        if len(expressions) > 60:
            raise QueryError('too_many_columns')
        tree.set('expressions', expressions)
        limit = tree.args.get('limit')
        if limit:
            value = limit.expression
            if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) < 0:
                raise QueryError('invalid_limit')
            row_limit = min(row_limit, int(value.this))
        offset = tree.args.get('offset')
        if offset and (not isinstance(offset.expression, exp.Literal) or not offset.expression.is_int
                       or not 0 <= int(offset.expression.this) <= settings.spreadsheet_max_rows):
            raise QueryError('invalid_offset')
        reference.set('db', exp.to_identifier(schema(tenant_id)))
        tree = tree.limit(row_limit)
        parameters = {}
        for literal in list(tree.find_all(exp.Literal)):
            if isinstance(literal.parent, exp.DataTypeParam):
                if not literal.is_int or not 0 <= int(literal.this) <= 1000:
                    raise QueryError('unsupported_cast')
                continue
            name = 'p' + str(len(parameters))
            parameters[name] = (literal.this if literal.is_string else
                                int(literal.this) if literal.is_int else Decimal(literal.this))
            literal.replace(exp.Placeholder(this=name))
        statement = re.sub(r'%\((p[0-9]+)\)s', r':\1', tree.sql(dialect='postgres', comments=False))
        return statement, parameters
    except QueryError:
        raise
    except Exception:
        raise QueryError('invalid_query') from None


def scalar(value):
    if (isinstance(value, float) and not math.isfinite(value)
            or isinstance(value, Decimal) and not value.is_finite()):
        raise QueryError('unsupported_result')
    if isinstance(value, Decimal):
        return str(value)
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def execute(tenant_id, identity, sql, *, admin=False, limit=None):
    with Session() as db:
        table, source = owned(db, tenant_id, identity)
        if source.status != 'ready' or not admin and not table.enabled:
            raise QueryError('table_unavailable', 409)
        reader = role(db, tenant_id)
    cap = min(limit or settings.spreadsheet_query_row_limit, settings.spreadsheet_query_row_limit)
    statement, parameters = guarded(sql, table, tenant_id, cap + 1)
    began, error, result = time.monotonic(), None, None
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            connection.execute(text(f'SET LOCAL statement_timeout = {settings.spreadsheet_query_timeout_ms}'))
            connection.execute(text("SET LOCAL lock_timeout = '500ms'"))
            connection.execute(text(f'SET LOCAL ROLE {q(reader)}'))
            connection.execute(text(f'SET LOCAL search_path TO {q(schema(tenant_id))}'))
            cursor = connection.execute(text(statement), parameters)
            rows, names = cursor.fetchmany(cap + 1), list(cursor.keys())
            result = {'columns': names, 'rows': [[scalar(v) for v in row] for row in rows[:cap]],
                      'truncated': len(rows) > cap, 'untrusted': True,
                      'source': {'id': source.id, 'filename': source.filename, 'heading': table.title}}
            if len(json.dumps(result, ensure_ascii=False).encode()) > 32000:
                raise QueryError('result_too_large')
    except DBAPIError as exc:
        error = 'query_timeout' if getattr(exc.orig, 'sqlstate', '') in ('57014', '55P03') else 'query_failed'
    except QueryError as exc:
        error = exc.code
    with Session.begin() as db:
        audit(db, tenant_id, 'spreadsheet.queried', str(identity), actor_type='system', details={
            'success': error is None, 'rows': len(result['rows']) if result and not error else 0,
            'duration_ms': int((time.monotonic() - began) * 1000), 'error': error})
    if error:
        raise QueryError(error)
    result['query_context'] = {'select': statement, 'parameters': {k: scalar(v) for k, v in parameters.items()},
                               'schema': table.columns}
    return result
