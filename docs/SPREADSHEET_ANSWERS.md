# Exact spreadsheet answers

Upload CSV/XLSX files in **Knowledge → Documents**, then open **Exact spreadsheet
answers**. Owners/admins can review each sheet's column labels, machine aliases,
types, currency symbols and imported row count, preview rows, and enable or pause
customer access. New and reprocessed sheets start paused. Enabling makes that
sheet's rows available to customers of the bot; use customer-facing data here.

Existing uploads need **Reprocess spreadsheet** once to build typed tables. This
uses the normal background knowledge-processing path and usage accounting. It
pauses existing table access, and the replacement must be reviewed and enabled.
Reimport queue failures can be retried. CSV/XLSX row chunks are excluded from
customer answer retrieval, including older uploads: exact answers use full-table
queries, so a few matching retrieval examples cannot be mistaken for a total.

## Prepare the data

- Use a UTF-8 CSV with a header row, or an XLSX workbook with up to 12 sheets and
  60 columns per sheet. Column aliases are `c1`, `c2`, etc.; original labels remain
  visible, including duplicate, blank and Indian-language labels.
- Consistent columns become exact `NUMERIC`, `BOOLEAN`, `DATE`, or `TEXT`. Mixed
  values stay text, including mixed currencies; they are not silently coerced to
  zero. Leading-zero codes, phone prefixes and simple XLSX zero-padding masks
  remain text. Blank cells become null. Empty tables with headers are supported.
- Indian and international numeric grouping, scientific notation and saved XLSX
  currency formats are recognized. Decimal values are preserved without float
  rounding in database results. Currency symbols remain units; `$` does not imply
  a particular country's dollar. Keep prices in one explicit unit, such as INR,
  rather than mixing rupees, lakh, crore and text in a single column.
- Replace XLSX formulas and error cells with saved values. The importer does not
  calculate formulas or fetch external workbook links. Date-only cells become
  dates; other date/time text is preserved without inventing a timezone.
- Limits apply to the entire file: `SPREADSHEET_MAX_ROWS` (default 20000, including
  scanned blank records), 250000 imported cell slots, 2000 characters per cell,
  and 50 MB expanded XLSX data. The normal upload-size limit also applies.

## Ask and verify exact questions

The bot uses `list_tables` to discover enabled sheets and `query_table` to run a
model-written SELECT. Discovery is tenant scoped, searchable by labels/filename,
and paginated. Query results and schema labels are marked **UNTRUSTED DATA**.
The prompt instructs the model to ignore instructions in cells, use COUNT/SUM for
full-table totals, preserve units and cite the result's filename/sheet number.

For example, a CSV with `Kind,Price (INR)` and prices 3500000, 3900000, 4500000 for
three 2BHK rows should answer “how many 2BHK plots under ₹40 lakh?” with **2**.
The owner can verify it in **Test an exact query**, using the actual table name
shown by the screen:

```sql
SELECT COUNT(*) AS total, SUM(c2) AS cost
FROM s_0123456789abcdef0123456789abcdef_0
WHERE c1 = '2BHK' AND c2 < 4000000
```

Basic filters, arithmetic, aggregates, DISTINCT, grouping, ordering, limited casts
and scalar functions are supported. Each query uses one owned table. Joins,
subqueries/CTEs, writes, row locks, system catalogs/functions and arbitrary functions
are rejected. Unsupported queries fail with safe codes; raw SQL/errors/cell values
are not included in application audit logs. SQL literals are bound separately and
SQL comments/parser diagnostic context are discarded.

Queries run as a separate tenant NOLOGIN role with SELECT privileges on only its
schema's generated tables, inside a read-only transaction with only that schema on
the search path. `SPREADSHEET_QUERY_TIMEOUT_MS` defaults to 1500 (maximum 5000),
`SPREADSHEET_QUERY_ROW_LIMIT` defaults to 100 (maximum 200), and results are capped
at 32 KB. Aggregation happens before the result-row cap. Truncation is explicit;
limited displayed rows must not be used to infer a full-table count. Nonfinite
numeric results fail safely. Failed customer queries produce an uncertainty reply.

## Deployment and lifecycle

Migration **0048_spreadsheet_tables** adds metadata. The importer transactionally
creates `t_<tenant UUID>` schemas, generated typed tables and separate database-local
reader roles. The application database user must be able to create/manage schemas
and roles and grant its generated reader roles to itself. The Compose database
owner already has these privileges. Query readers cannot log in or create roles/databases and receive no CREATE
grants on tenant schemas or application-table grants.

Publication commits chunks, tables and source readiness together after provider
calls finish. Source deletion removes its data in the same transaction, including
cascading deletes outside the API. An hourly worker sweep clears orphan tables and
deleted-tenant schemas/roles, serialized against publication. Run the arq worker
for imports and cleanup. Reimporting creates new table IDs; old IDs stop working.
Pausing removes table tools/data from customer answers; admin previews remain
available for review. Existing chat messages retain their normal retention policy.

Administrative endpoints (owner/admin, authenticated workspace context):

- `GET /api/v1/tables`: source/import status and table metadata.
- `GET /api/v1/tables/{id}/preview`: up to 20 rows, subject to server limits.
- `PATCH /api/v1/tables/{id}` with `{"enabled": true|false}`.
- `POST /api/v1/tables/{id}/query` with `{"sql": "SELECT ..."}`.
- `POST /api/v1/tables/imports/{source_id}`: queue reprocessing of a ready/failed
  CSV/XLSX source. No AI call runs in this request handler.

Automated acceptance uses real Postgres/Redis and fake embedding/chat providers,
including exact counts, mutations and catalog reads rejected independently by the
DB role, timeout, parameterization, citations, reimport and browser controls. Check
representative questions against a workspace's configured live model before launch.
