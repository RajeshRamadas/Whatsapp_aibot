"""Browser-only deterministic evaluation worker; no real provider requests."""
import asyncio

from app.main import app
from app.services import evaluation_runs
from tests.browser_app import BrowserKnowledge

_original = BrowserKnowledge.complete
_better = False
_paused = False


def complete(self, *args):
    if _better and args and args[-1][-1]['content'] == 'Do you repair cars?':
        return "I don't know based on our documents. Please ask our team."
    return _original(self, *args)


BrowserKnowledge.complete = complete


@app.post('/api/v1/_test/accuracy/improve', include_in_schema=False)
def improve():
    global _better
    _better = True
    return {'ok': True}


@app.post('/api/v1/_test/accuracy/pause', include_in_schema=False)
def pause():
    global _paused
    _paused = True
    return {'ok': True}


@app.middleware('http')
async def accuracy_worker_fixture(request, call_next):
    response = await call_next(request)
    if (not _paused and request.method == 'POST' and request.url.path == '/api/v1/evals/run'
            and response.status_code == 202):
        for _ in range(200):
            if not await asyncio.to_thread(evaluation_runs.process_one):
                break
    return response
