"""Only advance the authenticator clock in browser tests; all cryptography and auth routes stay real."""
from datetime import timedelta

from app.db import now
from app.main import app
from app.services import mfa

_offset = 0
mfa.now = lambda: now() + timedelta(seconds=_offset)


@app.post('/api/v1/_test/security/tick', include_in_schema=False)
def tick():
    global _offset
    _offset += 31
    return {'timestamp': int(mfa.now().timestamp())}
