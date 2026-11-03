from app.llm import ChatResult, Tool
from tests.conftest import FakeChat


def test_scripted_tool_result():
    chat = FakeChat('test')
    tool = Tool('lookup', 'Find a value', {'type': 'object', 'required': ['id']}, lambda args: args['id'] * 2)
    chat.tool_turns = [
        lambda results, enabled: ('', [('lookup', '{"id": 3}')]),
        lambda results, enabled: (f"Found {results[0]['result']}", []),
    ]
    assert chat.run('model', '', [], [tool]) == ChatResult('Found 6', ['lookup'])


def test_invalid_requests_and_parallel_calls_use_budget():
    chat = FakeChat('test')
    handled = []
    tool = Tool('lookup', 'Find a value', {'type': 'object', 'required': ['id']}, handled.append)

    def finish(results, enabled):
        assert not enabled
        assert results[0] == {'error': 'Invalid tool arguments'}
        assert results[-1] == {'error': 'Tool call limit reached'}
        return 'Done', []

    chat.tool_turns = [
        lambda results, enabled: ('', [('lookup', '{')] + [('lookup', {'id': 1})] * 6),
        finish,
    ]
    assert chat.run('model', '', [], [tool]) == ChatResult('Done', ['lookup'] * 4)
    assert handled == [{'id': 1}] * 4
