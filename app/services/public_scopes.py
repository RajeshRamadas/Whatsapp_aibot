"""Public integration permissions, kept separate from dashboard role keys."""
from typing import Literal, get_args

PublicScope = Literal['knowledge:read', 'knowledge:write', 'conversations:read', 'conversations:write',
    'messages:send', 'contacts:read', 'contacts:write', 'leads:read', 'leads:write', 'evals:read', 'evals:write']
SCOPES = frozenset(get_args(PublicScope))
READ = frozenset(scope for scope in SCOPES if scope.endswith(':read'))
ROLE_SCOPES = {'viewer': READ, 'agent': READ | {'messages:send', 'conversations:write', 'leads:write'},
               'admin': SCOPES, 'owner': SCOPES}


def permissions(scopes):
    result = set()
    for scope in scopes:
        result.update(ROLE_SCOPES[scope] if scope in ROLE_SCOPES else {scope} if scope in SCOPES else set())
    return frozenset(result)
