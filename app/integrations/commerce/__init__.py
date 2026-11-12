"""Fixed read-only provider operations over the existing pinned, bounded HTTPS client."""
import asyncio
import base64
import re
from urllib.parse import urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, model_validator

from ...services.web_fetch import canonical, public
from ..http_actions.transport import ActionError, Client, redact

VERSION = '2026-10'
ORDER = re.compile(r'#?[A-Za-z0-9][A-Za-z0-9_-]{0,39}')


class StoreInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: str = Field(pattern=r'^(shopify|woocommerce)$')
    url: str = Field(max_length=255)
    token: SecretStr = Field(min_length=8, max_length=4096)
    consumer_key: str = Field(default='', max_length=100)
    read_only: StrictBool

    @model_validator(mode='after')
    def validate_store(self):
        self.url = canonical(self.url)
        parts = urlsplit(self.url)
        host = parts.hostname or ''
        if parts.path != '/' or parts.query or parts.port not in (None, 443):
            raise ValueError('Use the HTTPS store origin without a path or query')
        if not self.read_only or any(c in self.token.get_secret_value() for c in '\r\n'):
            raise ValueError('Use read-only credentials')
        if self.provider == 'shopify':
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.myshopify\.com', host) or self.consumer_key:
                raise ValueError('Use the permanent myshopify.com domain')
        elif not re.fullmatch(r'ck_[a-f0-9]{40}', self.consumer_key):
            raise ValueError('A WooCommerce consumer key is required')
        # DNS is checked and pinned again by transport at every request.
        import ipaddress
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if host == 'localhost' or host.endswith('.localhost') or address and not public(str(address)):
            raise ValueError('Use a public HTTPS store')
        return self


class Provider:
    def __init__(self, store, credentials, transport=None):
        self.store, self.credentials, self.transport = store, credentials, transport or Client()

    async def request(self, path='', *, query=None, variables=None):
        s, c = self.store, self.credentials
        if s.provider == 'shopify':
            method, url = 'POST', s.url.rstrip('/') + f'/admin/api/{VERSION}/graphql.json'
            headers, body = {'X-Shopify-Access-Token': c['token']}, {'query': query, 'variables': variables or {}}
            if not query or not query.lstrip().startswith(('query ', 'query{', 'query {')):
                raise ActionError('read_only_required')
        else:
            method, url, body = 'GET', s.url.rstrip('/') + '/wp-json/wc/v3/' + path, None
            encoded = base64.b64encode((c['consumer_key'] + ':' + c['token']).encode()).decode()
            headers = {'Authorization': 'Basic ' + encoded}
        code, raw = await self.transport.request(method, url, headers, body=body)
        if not 200 <= code < 300:
            raise ActionError('auth_failed' if code in (401, 403) else 'provider_rejected')
        raw = redact(raw, [c['token'], c['consumer_key'], *headers.values()])
        if s.provider == 'shopify':
            if not isinstance(raw, dict) or raw.get('errors') or not isinstance(raw.get('data'), dict):
                raise ActionError('provider_rejected')
            return raw['data']
        return raw

    async def test(self):
        if self.store.provider == 'shopify':
            data = await self.request(query='query { currentAppInstallation { accessScopes { handle } } }')
            scopes = {s['handle'] for s in data['currentAppInstallation']['accessScopes']}
            if not {'read_orders', 'read_products'} <= scopes or any(s.startswith('write_') for s in scopes):
                raise ActionError('read_only_required')
        else:
            # Woo does not expose the caller's key permissions: require a read key at setup;
            # our adapter has no mutation operations even if a merchant supplies a broader key.
            await self.request('orders?per_page=1&_fields=id')
            await self.request('products?per_page=1&_fields=id')

    async def order(self, number):
        if not ORDER.fullmatch(number):
            raise ActionError('invalid_order')
        if self.store.provider == 'shopify':
            data = await self.request(query='''query Order($search: String!) { orders(first: 2, query: $search) {
                nodes { name phone billingAddress { phone } shippingAddress { phone }
                displayFinancialStatus displayFulfillmentStatus
                lineItems(first: 50) { nodes { title quantity } pageInfo { hasNextPage } }
                fulfillments(first: 20) { status trackingInfo { company number url } } } } }''',
                variables={'search': 'name:' + number})
            rows = [r for r in data['orders']['nodes'] if r['name'].lstrip('#') == number.lstrip('#')]
            if len(rows) != 1:
                return None
            r = rows[0]
            if r['lineItems']['pageInfo']['hasNextPage']:
                raise ActionError('order_too_large')
            phones = [r.get('phone'), (r.get('billingAddress') or {}).get('phone'),
                      (r.get('shippingAddress') or {}).get('phone')]
            return {'phones': phones, 'number': r['name'], 'status': r['displayFinancialStatus'],
                'fulfilment': r['displayFulfillmentStatus'], 'items': r['lineItems']['nodes'],
                'tracking': r['fulfillments']}
        data = await self.request('orders?' + urlencode({'search': number, 'per_page': 50}))
        rows = [r for r in data if str(r.get('number')) == number.lstrip('#')]
        if len(rows) != 1:
            return None
        r = rows[0]
        return {'phones': [(r.get('billing') or {}).get('phone')], 'number': str(r['number']),
            'status': r['status'], 'fulfilment': r['status'],
            'items': [{'title': i['name'], 'quantity': i['quantity']} for i in r['line_items']],
            'tracking': []}  # Core WooCommerce has no standard shipment-tracking field.

    async def products(self):
        products, cursor = [], None
        currency = None
        if self.store.provider == 'woocommerce':
            currency = (await self.request('settings/general/woocommerce_currency'))['value']
        # Publish only complete snapshots; never remove products after a partial/failed fetch.
        async with asyncio.timeout(180):
            for page in range(1, 202):
                if self.store.provider == 'shopify':
                    data = await self.request(query='''query Products($after: String) {
                        shop { currencyCode } products(first: 5, after: $after, query: "status:active") {
                        nodes { id title description status variants(first: 100) {
                          nodes { title price inventoryQuantity } pageInfo { hasNextPage } } }
                        pageInfo { hasNextPage endCursor } } }''', variables={'after': cursor})
                    connection = data['products']
                    for p in connection['nodes']:
                        if p['variants']['pageInfo']['hasNextPage']:
                            raise ActionError('catalogue_limit')
                        products.append({'id': p['id'], 'title': p['title'], 'description': p['description'],
                            'currency': data['shop']['currencyCode'], 'variants': p['variants']['nodes']})
                    if not connection['pageInfo']['hasNextPage']:
                        break
                    cursor = connection['pageInfo']['endCursor']
                    if not cursor:
                        raise ActionError('invalid_response')
                else:
                    rows = await self.request(f'products?status=publish&per_page=10&page={page}')
                    for p in rows:
                        variants = [{'title': p['name'], 'price': p['price'], 'stock_status': p['stock_status'],
                                     'stock_quantity': p.get('stock_quantity')}]
                        if p['type'] == 'variable':
                            variants = await self.request(f'products/{int(p["id"])}/variations?per_page=100')
                            if len(variants) >= 100:
                                raise ActionError('catalogue_limit')
                            variants = [{'title': ', '.join(a['option'] for a in v['attributes']),
                                'price': v['price'], 'stock_status': v['stock_status'],
                                'stock_quantity': v.get('stock_quantity')}
                                for v in variants if v['status'] == 'publish']
                        products.append({'id': str(p['id']), 'title': p['name'], 'description': p['description'],
                                         'currency': currency, 'variants': variants})
                    if len(rows) < 10:
                        break
                if len(products) > 1000 or page == 201:
                    raise ActionError('catalogue_limit')
        if len(products) > 1000:
            raise ActionError('catalogue_limit')
        return products
