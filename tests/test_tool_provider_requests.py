import json

import httpx
import pytest

from app.llm import AnthropicChat, GeminiChat, OpenAIChat, Tool


@pytest.mark.parametrize('provider', ['openai', 'anthropic', 'gemini'])
def test_tool_request_and_result_round_trip(provider):
    requests = []

    def reply(request):
        requests.append(json.loads(request.content))
        first = len(requests) == 1
        if provider == 'openai':
            message = {'role': 'assistant', 'content': None if first else 'Found it'}
            if first:
                message['tool_calls'] = [{'id': 'call_1', 'type': 'function',
                                          'function': {'name': 'lookup', 'arguments': '{"id":7}'}}]
            return httpx.Response(200, json={'choices': [{'index': 0, 'message': message}]})
        if provider == 'anthropic':
            content = ([{'type': 'tool_use', 'id': 'toolu_1', 'name': 'lookup', 'input': {'id': 7}}]
                       if first else [{'type': 'text', 'text': 'Found it'}])
            return httpx.Response(200, json={'content': content, 'stop_reason': 'tool_use' if first else 'end_turn'})
        parts = [{'functionCall': {'name': 'lookup', 'args': {'id': 7}}}] if first else [{'text': 'Found it'}]
        return httpx.Response(200, json={'candidates': [{'content': {'role': 'model', 'parts': parts}}]})

    transport = httpx.MockTransport(reply)
    if provider == 'openai':
        from openai import OpenAI

        chat = OpenAIChat.__new__(OpenAIChat)
        chat.client = OpenAI(api_key='test', http_client=httpx.Client(transport=transport))
    elif provider == 'anthropic':
        from anthropic import Anthropic

        chat = AnthropicChat.__new__(AnthropicChat)
        chat.client = Anthropic(api_key='test', http_client=httpx.Client(transport=transport))
    else:
        from google import genai
        from google.genai import types

        chat = GeminiChat.__new__(GeminiChat)
        chat.client = genai.Client(api_key='test', http_options=types.HttpOptions(
            client_args={'transport': transport}))

    tool = Tool('lookup', 'Find an item', {'type': 'object', 'properties': {'id': {'type': 'integer'}},
                                          'required': ['id']}, lambda args: args['id'] + 1)
    result = chat.run('test-model', 'Be helpful', [{'role': 'user', 'content': 'Find item 7'}], [tool])
    assert result.text == 'Found it'
    assert result.tool_calls == ['lookup']
    first, second = requests
    if provider == 'openai':
        assert first['tools'][0]['function']['parameters'] == tool.parameters
        assert second['messages'][-1]['tool_call_id'] == 'call_1'
        assert json.loads(second['messages'][-1]['content']) == {'result': 8}
    elif provider == 'anthropic':
        assert first['tools'][0]['input_schema'] == tool.parameters
        assert first['tool_choice'] == {'type': 'auto'}
        block = second['messages'][-1]['content'][0]
        assert block['tool_use_id'] == 'toolu_1'
        assert json.loads(block['content']) == {'result': 8}
    else:
        assert first['tools'][0]['functionDeclarations'][0]['parametersJsonSchema'] == tool.parameters
        block = second['contents'][-1]['parts'][0]['functionResponse']
        assert block['name'] == 'lookup'
        assert block['response'] == {'result': 8}
