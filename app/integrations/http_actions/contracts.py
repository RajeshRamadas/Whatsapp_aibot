"""Bounded action definitions; templates cannot change an action's origin."""
import ipaddress
import json
import re
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

import regex
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from ...services.web_fetch import canonical

Name = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_]{0,31}$')]
Path = Annotated[str, StringConstraints(pattern=r'^\$(?:\.[A-Za-z_][A-Za-z0-9_-]*|\[(?:0|[1-9][0-9]{0,3})\]){1,8}$')]
REF = re.compile(r'\{([a-z][a-z0-9_]*)\}')
BLOCKED = {'host', 'authorization', 'cookie', 'set-cookie', 'content-length', 'connection',
           'transfer-encoding', 'proxy-authorization', 'forwarded', 'x-forwarded-for', 'idempotency-key'}


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Parameter(Input):
    name: Name
    description: str = Field(min_length=1, max_length=200)
    type: Literal['string', 'integer', 'number', 'boolean'] = 'string'
    required: bool = True
    pattern: str | None = Field(None, max_length=200)

    @field_validator('pattern')
    @classmethod
    def valid_pattern(cls, value):
        if value:
            try:
                regex.compile(value)
            except regex.error:
                raise ValueError('Invalid validation pattern') from None
        return value


class Auth(Input):
    kind: Literal['none', 'api_key', 'bearer', 'basic', 'oauth_client_credentials'] = 'none'
    header: str = Field('X-API-Key', pattern=r'^[A-Za-z][A-Za-z0-9-]{0,60}$')
    value: str = Field('', max_length=4096)
    username: str = Field('', max_length=200)
    password: str = Field('', max_length=4096)
    token_url: str = Field('', max_length=2048)
    client_id: str = Field('', max_length=500)
    client_secret: str = Field('', max_length=4096)
    scope: str = Field('', max_length=500)

    @model_validator(mode='after')
    def credentials(self):
        if self.kind in ('api_key', 'bearer') and not self.value:
            raise ValueError('A credential value is required')
        if self.kind == 'api_key' and self.header.lower() in BLOCKED:
            raise ValueError('Choose a dedicated API key header')
        if self.kind == 'basic' and (not self.username or not self.password or ':' in self.username):
            raise ValueError('Basic authentication requires a username and password')
        if self.kind == 'oauth_client_credentials':
            self.token_url = valid_url(self.token_url)
            if not self.client_id or not self.client_secret:
                raise ValueError('OAuth client credentials are required')
        if any('\r' in v or '\n' in v for v in (self.value, self.username, self.password, self.client_id)):
            raise ValueError('Credentials cannot contain line breaks')
        return self


def valid_url(value):
    parts = urlsplit(value)
    if '{' in parts.netloc or '}' in parts.netloc or parts.fragment:
        raise ValueError('The HTTPS origin must be fixed and fragments are not supported')
    rendered = REF.sub('sample', value)
    if '{' in rendered or '}' in rendered:
        raise ValueError('Use named parameter placeholders')
    canonical(rendered)
    try:
        ip = ipaddress.ip_address(parts.hostname or '')
    except ValueError:
        return value
    if getattr(ip, 'is_site_local', False):
        raise ValueError('Private addresses are not supported')
    return value


class Definition(Input):
    name: Name
    description: str = Field(min_length=1, max_length=500)
    parameters: list[Parameter] = Field(default_factory=list, max_length=12)
    method: Literal['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] = 'GET'
    url_template: str = Field(max_length=2048)
    body_template: dict | None = None
    headers: dict[str, str] = Field(default_factory=dict, max_length=10)
    auth: Auth = Field(default_factory=Auth)
    outputs: dict[Name, Path] = Field(min_length=1, max_length=12)
    personal_data: bool = True
    identity: Literal['none', 'phone_match', 'email_otp'] = 'none'
    identity_path: Path | None = None
    verification_url: str | None = Field(None, max_length=2048)
    allow_changes: bool = False
    customer_confirmation: bool = True

    @field_validator('url_template', 'verification_url')
    @classmethod
    def urls(cls, value):
        return valid_url(value) if value else value

    @model_validator(mode='after')
    def policy(self):
        names = [p.name for p in self.parameters]
        if len(names) != len(set(names)) or 'customer_phone' in names:
            raise ValueError('Use unique parameter names; customer_phone is supplied by the server')
        allowed = set(names) | {'customer_phone'}
        templates = [self.url_template, self.verification_url or '', json.dumps(self.body_template)]
        if any(set(REF.findall(t)) - allowed for t in templates):
            raise ValueError('Template references an undeclared parameter')
        if len(json.dumps(self.body_template).encode()) > 8192:
            raise ValueError('Request body template is too large')
        if self.method != 'GET' and not self.allow_changes:
            raise ValueError('Explicitly allow changes for non-GET actions')
        if self.method == 'GET' and self.body_template:
            raise ValueError('GET actions cannot have a request body')
        if self.personal_data and self.identity == 'none':
            raise ValueError('Personal data requires an identity check')
        if self.identity != 'none' and not self.identity_path:
            raise ValueError('Choose the response identity field')
        if self.method != 'GET' and self.identity != 'none':
            if not self.verification_url:
                raise ValueError('Changing personal data requires a read-only identity lookup first')
            if urlsplit(self.verification_url).netloc != urlsplit(self.url_template).netloc:
                raise ValueError('Identity lookup must use the action origin')
            if set(REF.findall(self.url_template)) - set(REF.findall(self.verification_url)):
                raise ValueError('Identity lookup must bind every URL parameter used by the write')
        for key, value in self.headers.items():
            if (not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,60}', key) or key.lower() in BLOCKED
                    or len(value) > 4096 or '\r' in value or '\n' in value):
                raise ValueError('Use safe static headers and the dedicated authentication fields')
        return self


def arguments(definition, values):
    if not isinstance(values, dict) or set(values) - {p.name for p in definition.parameters}:
        raise ValueError('Unknown action parameters')
    result = {}
    for p in definition.parameters:
        if p.name not in values:
            if p.required:
                raise ValueError('Required action parameter is missing')
            continue
        value = values[p.name]
        valid = (isinstance(value, str) and len(value) <= 1000 if p.type == 'string' else
                 type(value) is bool if p.type == 'boolean' else
                 type(value) is int and abs(value) <= 10**12 if p.type == 'integer' else
                 type(value) in (int, float) and abs(value) <= 10**12)
        if not valid:
            raise ValueError('Invalid action parameter type or size')
        try:
            if p.pattern and not regex.fullmatch(p.pattern, str(value), timeout=0.05):
                raise ValueError('Action parameter does not match its validation pattern')
        except TimeoutError:
            raise ValueError('Validation pattern exceeded its time limit') from None
        result[p.name] = value
    return result


def render(template, values, *, url=False):
    if isinstance(template, dict):
        return {k: render(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, values) for v in template]
    if not isinstance(template, str):
        return template
    if not url and (match := REF.fullmatch(template)):
        return values[match[1]]
    return REF.sub(lambda m: quote(str(values[m[1]]), safe='') if url else str(values[m[1]]), template)


def extract(value, path):
    for key, index in re.findall(r'\.([A-Za-z_][A-Za-z0-9_-]*)|\[([0-9]+)\]', path):
        value = value[key] if key else value[int(index)]
    return value
