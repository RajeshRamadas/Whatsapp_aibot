"""Scripted draft and independent verdict for real queued-turn browser delivery."""
import json

from app import llm
from tests.browser_app import BrowserKnowledge

_run, _verify = BrowserKnowledge.run, BrowserKnowledge.verify


def run(self, model, system, messages, tools):
    question = messages[-1]['content']
    if question.startswith('GROUNDING '):
        days = 90 if 'unsupported' in question else 30
        return llm.ChatResult(f'Refunds accepted within {days} days. [1]', [])
    return _run(self, model, system, messages, tools)


def verify(self, model, system, messages):
    payload = json.loads(messages[0]['content'])
    if payload['answer'] == 'Refunds accepted within 90 days. [1]':
        assert '30 days' in json.dumps(payload['evidence'])
        return json.dumps({'verdict': 'unsupported', 'citations': []})
    return _verify(self, model, system, messages)


BrowserKnowledge.run, BrowserKnowledge.verify = run, verify
