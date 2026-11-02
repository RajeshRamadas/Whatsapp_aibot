import asyncio
import json

from app.services import events


def test_sse_requires_workspace_auth(client, tenant):
    assert client.get('/api/v1/events').status_code == 401
    assert client.get('/api/v1/events', headers={
        **tenant['headers'], 'Last-Event-ID': 'not-a-stream-id',
    }).status_code == 422


def test_event_format_and_scoping():
    assert events.stream_key('workspace-a') != events.stream_key('workspace-b')
    assert events.channel_key('workspace-a') == 'tenant:workspace-a'
    frame = events.encode_event('123-0', 'message.created', json.dumps({'conversation_id': 'c1'}))
    assert frame == 'id: 123-0\nevent: message.created\ndata: {"conversation_id": "c1"}\n\n'


def test_replay_only_after_last_id(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.calls = []
            self.closed = False

        async def xread(self, streams, count, block):
            self.calls.append((streams, count, block))
            if len(self.calls) == 1:
                return [('tenant:workspace-a:events', [('101-0', {
                    'event': 'message.created', 'data': '{"conversation_id":"c1"}',
                })])]
            return []

        async def aclose(self):
            self.closed = True

    fake = FakeRedis()
    monkeypatch.setattr(events, 'redis_client', lambda: fake)

    async def collect():
        frames = []
        async def disconnected():
            return bool(frames)
        async for frame in events.listen('workspace-a', '100-0', disconnected):
            frames.append(frame)
        return frames

    frames = asyncio.run(collect())
    assert frames[0].startswith('id: 101-0\nevent: message.created\n')
    assert fake.calls == [({'tenant:workspace-a:events': '100-0'}, 100, 15000)]
    assert fake.closed


def test_heartbeat_when_no_events(monkeypatch):
    class FakeRedis:
        async def xread(self, streams, count, block):
            return []
        async def aclose(self):
            pass
    monkeypatch.setattr(events, 'redis_client', FakeRedis)

    async def collect():
        generator = events.listen('workspace-a', '1-0', lambda: asyncio.sleep(0, result=False))
        frame = await anext(generator)
        await generator.aclose()
        return frame

    assert asyncio.run(collect()) == ': heartbeat\n\n'
