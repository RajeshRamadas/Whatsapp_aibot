"""Scripted model, real Postgres SQL and background ingestion for browser acceptance."""
from app import llm
from app.api import spreadsheets
from tests.browser_app import BrowserKnowledge, browser_ingestion

spreadsheets.enqueue = browser_ingestion
_original = BrowserKnowledge.run


def run(self, model, system, messages, tools):
    if messages[-1]['content'].startswith('EXACT PLOTS'):
        registry = {t.name: t for t in tools}
        if 'list_tables' not in registry:
            return llm.ChatResult('That information is currently unavailable.', [])
        table = registry['list_tables'].handler({'search': 'plots'})['tables'][0]
        result = registry['query_table'].handler({'table_id': table['id'], 'sql':
            f"SELECT COUNT(*) AS total FROM {table['table_name']} WHERE c1='2BHK' AND c2 < 4000000"})
        return llm.ChatResult(f"There are {result['rows'][0][0]} matching plots. [{result['source']['number']}]", [])
    return _original(self, model, system, messages, tools)


BrowserKnowledge.run = run
