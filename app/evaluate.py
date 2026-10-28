"""Score the bot against a tenant's test questions, so changes can be measured instead of guessed.

Each case checks, without another model call:
- expected_source: that file is among the cited sources (retrieval found the right document);
- expected_keywords: every keyword appears in the answer, case-insensitive (e.g. "30 days", "Rs 999");
- expect_no_answer: the documents don't cover the question, so the bot must not invent an answer.
"""
import logging

from . import rag
from .db import EvalCase, Tenant

log = logging.getLogger(__name__)

# Phrases that show the bot declined rather than answered.
DECLINE_PHRASES = ("don't know", 'do not know', 'not sure', "couldn't find", 'could not find', 'cannot find',
                   "can't find", 'no information', "doesn't mention", 'does not mention', 'not mentioned',
                   "don't have", 'do not have', 'not covered', 'unable to find')


def declined(text: str) -> bool:
    text = text.lower().replace('’', "'")
    return any(p in text for p in DECLINE_PHRASES)


def score(case: EvalCase, result: rag.Answer) -> dict:
    cited = [s['filename'] for s in result.sources]
    answer = result.text.lower()
    if case.expect_no_answer:
        passed = not result.sources or declined(result.text)
        return {'passed': passed, 'checks': {'declined': passed}}
    checks = {}
    if case.expected_source:
        checks['source_found'] = case.expected_source in cited
    missing = [k for k in case.expected_keywords or [] if k.lower() not in answer]
    if case.expected_keywords:
        checks['keywords_found'] = not missing
    return {'passed': all(checks.values()), 'checks': checks, 'missing_keywords': missing}


def run(tenant: Tenant, cases: list[EvalCase]) -> dict:
    results = []
    for case in cases:
        entry = {'id': case.id, 'question': case.question}
        try:
            result = rag.answer(tenant, None, case.question)
            entry |= score(case, result)
            entry |= {'answer': result.text, 'sources': [s['filename'] for s in result.sources]}
        except Exception as e:
            log.exception('Eval case %s failed', case.id)
            entry |= {'passed': False, 'error': str(e)[:300]}
        results.append(entry)
    with_source = [r for r in results if 'source_found' in r.get('checks', {})]
    passed = sum(r['passed'] for r in results)
    return {
        'total': len(results),
        'passed': passed,
        'accuracy': round(passed / len(results), 3) if results else None,
        'source_hit_rate': round(sum(r['checks']['source_found'] for r in with_source) / len(with_source), 3)
        if with_source else None,
        'results': results,
    }
