"""Fixed, read-only store operations over the shared pinned HTTPS transport."""
import base64
import re
from urllib.parse import urlencode, urlsplit

from bs4 import BeautifulSoup

from .http_actions.contracts import valid_url
from .http_actions.transport import ActionError, Client, redact

VERSION = '2026-10'
PAGE = 'pageInfo { hasNextPage endCursor }'
PRODUCTS = '''query($after: String) { shop { currencyCode } products(first: 10, after: $after,
 query: "status:active", sortKey: ID) { nodes { id title description status
 variants(first: 20) { nodes { title price inventoryQuantity } pageInfo { hasNextPage } } }
 pageInfo { hasNextPage endCursor } } }'''
ORDERS = '''query($query: String!) { orders(first: 5, query: $query) { nodes { name phone email
 displayFinancialStatus displayFulfillmentStatus shippingAddress { phone }
 lineItems(first: 20) { nodes { name quantity } pageInfo { hasNextPage } }
 fulfillments(first: 10) { status trackingInfo { company number url } } } } }'''


def origin(value, provider):
    value = valid_url(value.rstrip('/'))
    parts = urlsplit(value)
    if parts.path or parts.query or parts.port not in (None, 443):
        raise ValueError('Use the HTTPS store origin without a path or query')
    if provider == 'shopify' and not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.myshopify\.com', parts.hostname or ''):
        raise ValueError('Use the store myshopify.com domain')
    return value


def clean(value, limit=4000):
    return BeautifulSoup(str(value or ''), 'html.parser').get_text(' ', strip=True)[:limit]


class Store:
    def __init__(self, provider, url, credentials, client=None):
        self.provider, self.url = provider, origin(url, provider)
        self.credentials, self.client = credentials, client or Client()

    async def request(self, path='', params=None, *, query=None, variables=None):
        if self.provider == 'shopify':
            target = self.url + f'/admin/api/{VERSION}/graphql.json'
            headers = {'X-Shopify-Access-Token': self.credentials['token']}
            body = {'query': query, 'variables': variables or {}}
            method = 'POST'  # Only the fixed GraphQL queries above; never a mutation.
        else:
            target = self.url + '/wp-json/wc/v3/' + path
            if params:
                target += '?' + urlencode(params)
            raw = self.credentials['key'] + ':' + self.credentials['secret']
            headers = {'Authorization': 'Basic ' + base64.b64encode(raw.encode()).decode()}
            method, body = 'GET', None
        code, data = await self.client.request(method, target, headers, body=body)
        if code in (401, 403):
            raise ActionError('auth_failed')
        if code != 200:
            raise ActionError('store_rejected')
        if self.provider == 'shopify':
            if not isinstance(data, dict) or data.get('errors') or not isinstance(data.get('data'), dict):
                raise ActionError('store_rejected')
            data = data['data']
        return redact(data, [*self.credentials.values(), *headers.values()])

    async def test(self):
        if self.provider == 'shopify':
            data = await self.request(query='query { currentAppInstallation { accessScopes { handle } } }')
            scopes = {s['handle'] for s in data['currentAppInstallation']['accessScopes']}
            if not {'read_orders', 'read_products'} <= scopes or any(s.startswith('write_') for s in scopes):
                raise ActionError('read_only_scopes_required')
        else:
            for path in ('orders', 'products'):
                data = await self.request(path, {'per_page': 1, '_fields': 'id'})
                if not isinstance(data, list):
                    raise ActionError('invalid_store_response')

    async def products(self, maximum):
        products, cursor, page = [], None, 1
        currency = None
        if self.provider == 'woocommerce':
            currency = (await self.request('data/currencies/current'))['code']
        while True:
            if self.provider == 'shopify':
                data = await self.request(query=PRODUCTS, variables={'after': cursor})
                currency = data['shop']['currencyCode']
                group = data['products']
                rows = group['nodes']
                more, next_cursor = group['pageInfo']['hasNextPage'], group['pageInfo']['endCursor']
                if more and (not next_cursor or next_cursor == cursor):
                    raise ActionError('invalid_store_response')
                cursor = next_cursor
            else:
                rows = await self.request('products', {'per_page': 10, 'page': page, 'status': 'publish',
                    'orderby': 'id', 'order': 'asc', '_fields': 'id,name,description,price,stock_status,type'})
                more = len(rows) == 10
            for row in rows:
                if self.provider == 'shopify':
                    variants = row['variants']
                    if row['status'] != 'ACTIVE' or variants['pageInfo']['hasNextPage']:
                        raise ActionError('catalogue_limit')
                    variants = [{'title': v['title'], 'price': v['price'], 'stock': v['inventoryQuantity']}
                                for v in variants['nodes']]
                elif row['type'] == 'variable':
                    variants = await self.request(f'products/{int(row["id"])}/variations',
                        {'per_page': 21, '_fields': 'id,attributes,price,stock_status,status'})
                    if len(variants) > 20:
                        raise ActionError('catalogue_limit')
                    variants = [{'title': ', '.join(clean(a['option'], 100) for a in v['attributes']),
                                 'price': v['price'], 'stock': v['stock_status']} for v in variants
                                if v['status'] == 'publish']
                elif row['type'] in ('simple', 'external'):
                    variants = [{'title': '', 'price': row['price'], 'stock': row['stock_status']}]
                else:
                    raise ActionError('unsupported_product_type')
                products.append({'id': str(row['id']), 'title': clean(row.get('title', row.get('name')), 200),
                    'description': clean(row['description']), 'currency': currency, 'variants': variants})
                if len(products) > maximum:
                    raise ActionError('catalogue_limit')
            if not more:
                return products
            page += 1

    async def order(self, number):
        if not re.fullmatch(r'#?[A-Za-z0-9][A-Za-z0-9_-]{0,39}', number):
            raise ActionError('invalid_order_number')
        number = number.lstrip('#')
        if self.provider == 'shopify':
            data = await self.request(query=ORDERS, variables={'query': 'name:' + number})
            rows = [r for r in data['orders']['nodes'] if r['name'].lstrip('#') == number]
            if len(rows) != 1:
                return None
            row = rows[0]
            if row['lineItems']['pageInfo']['hasNextPage']:
                raise ActionError('order_too_large')
            return {'phone': row['phone'] or (row.get('shippingAddress') or {}).get('phone'), 'email': row['email'],
                'data': {'order_number': row['name'], 'status': row['displayFinancialStatus'],
                         'fulfilment': row['displayFulfillmentStatus'], 'items': row['lineItems']['nodes'],
                         'tracking': row['fulfillments']}}
        rows = await self.request('orders', {'search': number, 'per_page': 5,
            '_fields': 'number,status,billing,line_items,shipping_lines'})
        rows = [r for r in rows if str(r['number']) == number]
        if len(rows) != 1:
            return None
        row = rows[0]
        return {'phone': row['billing'].get('phone'), 'email': row['billing'].get('email'), 'data': {
            'order_number': row['number'], 'status': row['status'], 'fulfilment': row['status'],
            'items': [{'name': r['name'], 'quantity': r['quantity']} for r in row['line_items']],
            'tracking': [], 'tracking_note': 'Tracking is not provided by the WooCommerce core API'}}
