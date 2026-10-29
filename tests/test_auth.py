from app.api import auth
from app.config import settings
from app.services import email


# TestClient uses HTTP; production continues to default to Secure cookies.
settings.auth_cookie_secure = False


class RecordingEmail(email.EmailSender):
    def __init__(self):
        self.sent = []

    def send(self, recipient, subject, code):
        self.sent.append((recipient, subject, code))


def test_signup_verify_login_logout_and_csrf(client, monkeypatch):
    recorder = RecordingEmail()
    monkeypatch.setattr(email, 'sender', recorder)
    auth._auth_hits.clear()
    data = {'email': 'HELLO@Example.com', 'password': 'a-long-unique-password', 'name': 'Test User'}
    signup = client.post('/api/v1/auth/signup', json=data)
    assert signup.status_code == 201, signup.text
    assert signup.json()['email'] == 'hello@example.com'
    assert signup.json()['email_verified'] is False
    code = recorder.sent[-1][2]
    verify = client.post('/api/v1/auth/verify-email', json={'email': 'hello@example.com', 'code': code})
    assert verify.status_code == 200, verify.text
    assert client.post('/api/v1/auth/login', json={'email': data['email'], 'password': 'wrong'}).status_code == 401
    login = client.post('/api/v1/auth/login', json={'email': data['email'], 'password': data['password']})
    assert login.status_code == 200, login.text
    assert client.get('/api/v1/auth/me').json()['email_verified'] is True
    assert client.post('/api/v1/auth/logout').status_code == 403
    csrf = client.cookies.get('chatbot_csrf')
    assert client.post('/api/v1/auth/logout', headers={'X-CSRF-Token': csrf}).status_code == 204
    assert client.get('/api/v1/auth/me').status_code == 401


def test_password_reset_revokes_sessions(client, monkeypatch):
    recorder = RecordingEmail()
    monkeypatch.setattr(email, 'sender', recorder)
    auth._auth_hits.clear()
    data = {'email': 'reset@example.com', 'password': 'old-unique-password', 'name': 'Test'}
    assert client.post('/api/v1/auth/signup', json=data).status_code == 201
    assert client.post('/api/v1/auth/login', json=data).status_code == 200
    assert client.post('/api/v1/auth/password-reset/request', json={'email': data['email']}).status_code == 200
    code = recorder.sent[-1][2]
    result = client.post('/api/v1/auth/password-reset/confirm', json={
        'email': data['email'], 'code': code, 'password': 'new-unique-password'})
    assert result.status_code == 200, result.text
    assert client.get('/api/v1/auth/me').status_code == 401
    assert client.post('/api/v1/auth/login', json=data).status_code == 401
    assert client.post('/api/v1/auth/login', json={'email': data['email'],
                                                  'password': 'new-unique-password'}).status_code == 200


def test_login_rate_limit_and_weak_password(client, monkeypatch):
    monkeypatch.setattr(email, 'sender', RecordingEmail())
    auth._auth_hits.clear()
    data = {'email': 'limited@example.com', 'password': 'password123', 'name': 'Test'}
    assert client.post('/api/v1/auth/signup', json=data).status_code == 422
    data['password'] = 'strong-unique-password'
    assert client.post('/api/v1/auth/signup', json=data).status_code == 201
    for _ in range(5):
        assert client.post('/api/v1/auth/login', json={'email': data['email'], 'password': 'bad'}).status_code == 401
    assert client.post('/api/v1/auth/login', json=data).status_code == 429
