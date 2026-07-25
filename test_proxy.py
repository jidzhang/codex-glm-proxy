#!/usr/bin/env python3
"""Comprehensive tests for proxy conversion logic."""

import json, sys, os, unittest, threading, http.server, socketserver, time, socket
from unittest.mock import patch
import http.client as hc
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proxy

# Tests in this module exercise ConnectionPool / conversion logic directly
# and assume direct (non-tunneled) upstream connections. Ambient proxy env
# vars from the developer's shell would otherwise route acquires through a
# local proxy and break pool-keying assertions. Save and clear them for the
# duration of the module, restore at teardown.
_PROXY_ENV_VARS = (
    "GLM_HTTP_PROXY", "GLM_HTTPS_PROXY",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)
_saved_proxy_env = {k: os.environ.pop(k) for k in _PROXY_ENV_VARS if k in os.environ}


def tearDownModule():
    os.environ.update(_saved_proxy_env)


SAMPLE_CHAT_STREAM_CHUNKS = [{"id": "chatcmpl-123", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}, {"id": "chatcmpl-123", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}]}, {"id": "chatcmpl-123", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {"content": " world"}, "finish_reason": None}]}, {"id": "chatcmpl-123", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
SAMPLE_TOOL_CALL_CHUNKS = [{"id": "chatcmpl-tc", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}, {"id": "chatcmpl-tc", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {"content": "I will run "}, "finish_reason": None}]}, {"id": "chatcmpl-tc", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_abc123", "function": {"name": "exec", "arguments": ""}}]}, "finish_reason": None}]}, {"id": "chatcmpl-tc", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"command\": \"ls"}}]}, "finish_reason": None}]}, {"id": "chatcmpl-tc", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"}"}}]}, "finish_reason": None}]}, {"id": "chatcmpl-tc", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]
SAMPLE_MULTI_TOOL_CALL_CHUNKS = [{"id": "chatcmpl-mtc", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}, {"id": "chatcmpl-mtc", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_001", "function": {"name": "exec", "arguments": "{\"cmd\": \"ls\"}"}}]}, "finish_reason": None}]}, {"id": "chatcmpl-mtc", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 1, "id": "call_002", "function": {"name": "apply_patch", "arguments": "{\"patch\": \"--- a/f.txt\"}"}}]}, "finish_reason": None}]}, {"id": "chatcmpl-mtc", "object": "chat.completion.chunk", "created": 1234567890, "model": "glm-5.1", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]


# ---------------------------------------------------------------------------
# Model mapping tests
# ---------------------------------------------------------------------------

class TestModelMapping(unittest.TestCase):
    def test_native_models(self):
        for model in ['glm-5.2', 'glm-5.1', 'glm-5-turbo', 'glm-5', 'glm-4.7']:
            self.assertEqual(proxy.MODEL_MAPPING.get(model, proxy.DEFAULT_MODEL), model)

    def test_alias_models(self):
        # GPT-5.x family
        self.assertEqual(proxy.MODEL_MAPPING['gpt-5.5'], 'glm-5.2')
        self.assertEqual(proxy.MODEL_MAPPING['gpt-5.4'], 'glm-5.2')
        self.assertEqual(proxy.MODEL_MAPPING['gpt-5.4-mini'], 'glm-4.7')
        # GPT-4.x family
        self.assertEqual(proxy.MODEL_MAPPING['gpt-4.5'], 'glm-5.2')
        self.assertEqual(proxy.MODEL_MAPPING['gpt-4.1'], 'glm-5.2')
        self.assertEqual(proxy.MODEL_MAPPING['gpt-4.1-mini'], 'glm-4.7')
        self.assertEqual(proxy.MODEL_MAPPING['gpt-4o'], 'glm-5.2')
        self.assertEqual(proxy.MODEL_MAPPING['gpt-4o-mini'], 'glm-4.7')
        self.assertEqual(proxy.MODEL_MAPPING['gpt-4-turbo'], 'glm-5.2')
        self.assertEqual(proxy.MODEL_MAPPING['gpt-4'], 'glm-5.2')
        # OpenAI o-series reasoning models
        self.assertEqual(proxy.MODEL_MAPPING['o3'], 'glm-5.2')
        self.assertEqual(proxy.MODEL_MAPPING['o3-mini'], 'glm-4.7')
        self.assertEqual(proxy.MODEL_MAPPING['o1'], 'glm-5.2')
        self.assertEqual(proxy.MODEL_MAPPING['o1-mini'], 'glm-4.7')

    def test_all_openai_aliases(self):
        aliases = [
            'gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini',
            'gpt-4.5', 'gpt-4.1', 'gpt-4.1-mini', 'gpt-4o', 'gpt-4o-mini',
            'gpt-4-turbo', 'gpt-4',
            'o3', 'o3-mini', 'o1', 'o1-mini',
        ]
        for alias in aliases:
            self.assertIn(alias, proxy.MODEL_MAPPING)

    def test_alias_tier_mapping(self):
        # Flagship tier (including reasoning flagships) -> glm-5.2
        for alias in ['gpt-5.5', 'gpt-5.4', 'gpt-4.5', 'gpt-4.1', 'gpt-4o',
                      'gpt-4-turbo', 'gpt-4', 'o3', 'o1']:
            self.assertEqual(proxy.MODEL_MAPPING[alias], 'glm-5.2')
        # Economy/mini tier -> glm-4.7
        for alias in ['gpt-5.4-mini', 'gpt-4.1-mini', 'gpt-4o-mini',
                      'o3-mini', 'o1-mini']:
            self.assertEqual(proxy.MODEL_MAPPING[alias], 'glm-4.7')

class TestConvertResponsesToChat(unittest.TestCase):
    def _convert(self, body):
        return proxy.convert_responses_to_chat(body)

    def test_simple_string_input(self):
        r = self._convert({'model': 'glm-5.1', 'input': 'hello', 'stream': True})
        self.assertEqual(r['model'], 'glm-5.1')
        self.assertEqual(r['messages'], [{'role': 'user', 'content': 'hello'}])
        self.assertTrue(r['stream'])

    def test_instructions_become_system(self):
        r = self._convert({'model': 'glm-5.1', 'instructions': 'Be helpful', 'input': 'hi'})
        self.assertEqual(r['messages'][0], {'role': 'system', 'content': 'Be helpful'})

    def test_empty_instructions_ignored(self):
        r = self._convert({'model': 'glm-5.1', 'instructions': '', 'input': 'hi'})
        self.assertFalse(any(m['role'] == 'system' for m in r['messages']))

    def test_none_instructions_ignored(self):
        r = self._convert({'model': 'glm-5.1', 'instructions': None, 'input': 'hi'})
        self.assertFalse(any(m['role'] == 'system' for m in r['messages']))

    def test_message_input_items(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Hello'}]}
        ]})
        self.assertEqual(r['messages'], [{'role': 'user', 'content': 'Hello'}])

    def test_developer_role_becomes_system(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'message', 'role': 'developer', 'content': 'System prompt'}
        ]})
        self.assertEqual(r['messages'][0]['role'], 'system')

    def test_function_call_item(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'list'}]},
            {'type': 'function_call', 'call_id': 'call_001', 'name': 'exec', 'arguments': '{"command": "ls"}'},
        ]})
        tc = r['messages'][1]
        self.assertEqual(tc['role'], 'assistant')
        self.assertEqual(len(tc['tool_calls']), 1)
        self.assertEqual(tc['tool_calls'][0]['function']['name'], 'exec')

    def test_function_call_id_fallback(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'function_call', 'id': 'fallback_id', 'name': 'exec', 'arguments': '{}'},
        ]})
        self.assertEqual(r['messages'][0]['tool_calls'][0]['id'], 'fallback_id')

    def test_function_call_output_item(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'function_call_output', 'call_id': 'call_001', 'output': 'result'},
        ]})
        self.assertEqual(r['messages'][0], {'role': 'tool', 'tool_call_id': 'call_001', 'content': 'result'})

    def test_multi_turn(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'List'}]},
            {'type': 'function_call', 'call_id': 'c1', 'name': 'exec', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'c1', 'output': 'files'},
            {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'What?'}]},
        ]})
        self.assertEqual(len(r['messages']), 4)

    def test_tool_conversion(self):
        r = self._convert({'model': 'glm-5.1', 'input': 'test', 'tools': [
            {'type': 'function', 'name': 'exec', 'description': 'Run', 'parameters': {}},
            {'type': 'web_search'},
        ]})
        self.assertEqual(len(r['tools']), 1)
        self.assertEqual(r['tools'][0]['function']['name'], 'exec')

    def test_unsupported_tool_types_filtered(self):
        for tt in ['web_search', 'code_interpreter', 'file_search', 'computer_use']:
            r = self._convert({'model': 'glm-5.1', 'input': 'test', 'tools': [{'type': tt}]})
            self.assertNotIn('tools', r)

    def test_tool_function_with_function_key(self):
        tool = {'type': 'function', 'function': {'name': 'exec', 'description': 'Run', 'parameters': {'type': 'object'}}}
        r = self._convert({'model': 'glm-5.1', 'input': 'test', 'tools': [tool]})
        self.assertEqual(len(r['tools']), 1)
        self.assertEqual(r['tools'][0], tool)

    def test_reasoning_passthrough(self):
        r = self._convert({'model': 'glm-5.1', 'input': 'test', 'reasoning': {'effort': 'high'}})
        self.assertEqual(r['reasoning'], {'effort': 'high'})

    def test_reasoning_xhigh_maps_to_max(self):
        # Codex's xhigh maps to GLM-5.2+'s max tier (earlier GLM silently
        # ignored xhigh, so this is a strict upgrade).
        r = self._convert({'model': 'glm-5.2', 'input': 'test', 'reasoning': {'effort': 'xhigh'}})
        self.assertEqual(r['reasoning'], {'effort': 'max'})

    def test_reasoning_low_medium_high_untouched(self):
        for effort in ('low', 'medium', 'high'):
            r = self._convert({'model': 'glm-5.2', 'input': 'test', 'reasoning': {'effort': effort}})
            self.assertEqual(r['reasoning'], {'effort': effort})

    def test_model_mapping_gpt4o(self):
        r = self._convert({'model': 'gpt-4o', 'input': 'test'})
        self.assertEqual(r['model'], 'glm-5.2')

    def test_unknown_model_uses_default(self):
        r = self._convert({'model': 'unknown', 'input': 'test'})
        self.assertEqual(r['model'], 'glm-5.2')

    def test_default_model_fallback(self):
        r = self._convert({'input': 'test'})  # no model key
        self.assertEqual(r['model'], 'glm-5.2')  # no model -> DEFAULT_MODEL

    def test_empty_input(self):
        r = self._convert({'model': 'glm-5.1', 'input': []})
        self.assertEqual(r['messages'], [])

    def test_no_input_key(self):
        r = self._convert({'model': 'glm-5.1'})
        self.assertEqual(r['messages'], [])

    def test_dict_input_messages(self):
        r = self._convert({'model': 'glm-5.1', 'input': {'messages': [{'role': 'user', 'content': 'hi'}]}})
        self.assertEqual(r['messages'], [{'role': 'user', 'content': 'hi'}])

    def test_dict_input_content(self):
        r = self._convert({'model': 'glm-5.1', 'input': {'content': 'hi'}})
        self.assertEqual(r['messages'], [{'role': 'user', 'content': 'hi'}])

    def test_passthrough_params(self):
        r = self._convert({'model': 'glm-5.1', 'input': 'test',
                          'temperature': 0.5, 'top_p': 0.9, 'max_tokens': 100,
                          'stream': True, 'frequency_penalty': 0.1, 'presence_penalty': 0.2,
                          'stop': ['\n']})
        self.assertEqual(r['temperature'], 0.5)
        self.assertEqual(r['top_p'], 0.9)
        self.assertEqual(r['max_tokens'], 100)
        self.assertEqual(r['stop'], ['\n'])

    def test_max_output_tokens_mapped_to_max_tokens(self):
        r = self._convert({'model': 'glm-5.1', 'input': 'test', 'max_output_tokens': 4096})
        self.assertEqual(r['max_tokens'], 4096)
        self.assertNotIn('max_output_tokens', r)

    def test_tool_choice_passthrough(self):
        r = self._convert({'model': 'glm-5.1', 'input': 'test', 'tool_choice': 'auto'})
        self.assertEqual(r['tool_choice'], 'auto')

    def test_non_dict_items_skipped(self):
        r = self._convert({'model': 'glm-5.1', 'input': ['str', 42, None,
            {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'ok'}]}]})
        self.assertEqual(len(r['messages']), 1)

    def test_item_without_type_skipped(self):
        r = self._convert({'model': 'glm-5.1', 'input': [{'role': 'user', 'content': 'no type'}]})
        self.assertEqual(r['messages'], [])

    def test_message_string_content(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'message', 'role': 'user', 'content': 'plain string'}]})
        self.assertEqual(r['messages'], [{'role': 'user', 'content': 'plain string'}])

    def test_non_input_text_content_ignored(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_image', 'url': 'http://x.com/img.png'},
                {'type': 'input_text', 'text': 'see image'},
            ]}]})
        self.assertEqual(r['messages'], [{'role': 'user', 'content': 'see image'}])

    def test_empty_tools_excluded(self):
        r = self._convert({'model': 'glm-5.1', 'input': 'test', 'tools': [{'type': 'web_search'}]})
        self.assertNotIn('tools', r)

    def test_non_dict_tools_skipped(self):
        r = self._convert({'model': 'glm-5.1', 'input': 'test', 'tools': [
            'not a dict',
            {'type': 'function', 'name': 'exec', 'description': 'Run', 'parameters': {}},
        ]})
        self.assertEqual(len(r['tools']), 1)

    def test_tool_with_function_key_no_type(self):
        r = self._convert({'model': 'glm-5.1', 'input': 'test', 'tools': [
            {'function': {'name': 'exec'}}]})
        self.assertEqual(len(r['tools']), 1)

class TestConvertChatToResponses(unittest.TestCase):
    def test_simple_response(self):
        r = proxy.convert_chat_to_responses({
            'id': 'chatcmpl-123', 'created': 123, 'model': 'glm-5.1',
            'choices': [{'message': {'role': 'assistant', 'content': 'Hello!'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
        })
        self.assertEqual(r['object'], 'response')
        self.assertEqual(r['status'], 'completed')
        self.assertEqual(r['output'][0]['content'][0]['text'], 'Hello!')

    def test_tool_call_response(self):
        r = proxy.convert_chat_to_responses({
            'id': 'chatcmpl-456', 'created': 123, 'model': 'glm-5.1',
            'choices': [{'message': {
                'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'call_abc', 'type': 'function',
                                'function': {'name': 'exec', 'arguments': '{"command":"ls"}'}}],
            }, 'finish_reason': 'tool_calls'}],
        })
        self.assertEqual(len(r['output']), 1)
        tc = r['output'][0]
        self.assertEqual(tc['type'], 'function_call')
        self.assertEqual(tc['name'], 'exec')
        self.assertEqual(tc['call_id'], 'call_abc')

    def test_text_and_tools(self):
        r = proxy.convert_chat_to_responses({
            'id': 'chatcmpl-789', 'created': 123, 'model': 'glm-5.1',
            'choices': [{'message': {
                'role': 'assistant', 'content': 'Running.',
                'tool_calls': [{'id': 'call_1', 'type': 'function',
                                'function': {'name': 'exec', 'arguments': '{}'}}],
            }, 'finish_reason': 'tool_calls'}],
        })
        outputs = r['output']
        self.assertEqual(len(outputs), 2)
        self.assertEqual(outputs[0]['type'], 'message')
        self.assertEqual(outputs[0]['content'][0]['type'], 'output_text')
        self.assertEqual(outputs[1]['type'], 'function_call')
        self.assertEqual(outputs[1]['name'], 'exec')

    def test_empty_content(self):
        r = proxy.convert_chat_to_responses({
            'id': 'chatcmpl-e', 'created': 0, 'model': 'glm-5.1',
            'choices': [{'message': {'role': 'assistant', 'content': ''}, 'finish_reason': 'stop'}],
        })
        self.assertEqual(r['output'], [])

    def test_finish_reason_length(self):
        r = proxy.convert_chat_to_responses({
            'id': 'c1', 'created': 1, 'model': 'glm-5.1',
            'choices': [{'message': {'role': 'assistant', 'content': 'truncated'}, 'finish_reason': 'length'}],
        })
        self.assertEqual(r['status'], 'incomplete')
        self.assertEqual(r['incomplete_details']['reason'], 'max_output_tokens')

    def test_finish_reason_content_filter(self):
        r = proxy.convert_chat_to_responses({
            'id': 'c2', 'created': 1, 'model': 'glm-5.1',
            'choices': [{'message': {'role': 'assistant', 'content': ''}, 'finish_reason': 'content_filter'}],
        })
        self.assertEqual(r['status'], 'incomplete')
        self.assertEqual(r['incomplete_details']['reason'], 'content_filter')

    def test_finish_reason_stop(self):
        r = proxy.convert_chat_to_responses({
            'id': 'c3', 'created': 1, 'model': 'glm-5.1',
            'choices': [{'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}],
        })
        self.assertEqual(r['status'], 'completed')
        self.assertNotIn('incomplete_details', r)

    def test_missing_fields_defaults(self):
        r = proxy.convert_chat_to_responses({})
        self.assertEqual(r['id'], '')
        self.assertEqual(r['created'], 0)
        self.assertEqual(r['model'], '')
        self.assertEqual(r['output'], [])
        self.assertEqual(r['status'], 'completed')

class TestStreamingConversion(unittest.TestCase):
    def _simulate(self, chunks):
        h = proxy.ProxyHandler.__new__(proxy.ProxyHandler)
        h._seq = 0
        h._item_id = None
        h._response_id = None
        h._created = None
        h._model = None
        h._full_content = ''
        h._content_part_id = None
        h._tool_calls = {}
        h._finish_emitted = False
        events = []
        for chunk in chunks:
            line = f'data: {json.dumps(chunk)}'.encode()
            for converted in h._convert_stream_line(line):
                events.append(converted)
        for converted in h._convert_stream_line(b'data: [DONE]'):
            events.append(converted)
        return events

    def _parse(self, raw_events):
        parsed = []
        for raw in raw_events:
            text = raw.decode('utf-8')
            event_type = data = None
            for line in text.strip().split('\n'):
                if line.startswith('event: '):
                    event_type = line[7:]
                elif line.startswith('data: '):
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        data = line[6:]
            if event_type:
                parsed.append((event_type, data))
        return parsed

    def test_basic_stream(self):
        events = self._parse(self._simulate(SAMPLE_CHAT_STREAM_CHUNKS))
        types = [e[0] for e in events]
        self.assertIn('response.created', types)
        self.assertIn('response.output_text.delta', types)
        self.assertIn('response.completed', types)

    def test_content_assembled(self):
        events = self._parse(self._simulate(SAMPLE_CHAT_STREAM_CHUNKS))
        done = [e for e in events if e[0] == 'response.output_text.done']
        self.assertEqual(done[0][1]['text'], 'Hello world')

    def test_tool_call_stream(self):
        events = self._parse(self._simulate(SAMPLE_TOOL_CALL_CHUNKS))
        types = [e[0] for e in events]
        self.assertIn('response.function_call_arguments.delta', types)
        self.assertIn('response.function_call_arguments.done', types)
        args_done = [e for e in events if e[0] == 'response.function_call_arguments.done']
        self.assertEqual(args_done[0][1]['arguments'], '{"command": "ls"}')

    def test_has_done_marker(self):
        self.assertIn(b'[DONE]', self._simulate(SAMPLE_CHAT_STREAM_CHUNKS)[-1])

    def test_empty_stream(self):
        chunks = [
            {'id': 'chatcmpl-e', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]},
            {'id': 'chatcmpl-e', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
        ]
        events = self._parse(self._simulate(chunks))
        types = [e[0] for e in events]
        self.assertIn('response.created', types)
        self.assertNotIn('response.output_text.delta', types)
        self.assertIn('response.content_part.done', types)
        self.assertIn('response.output_item.done', types)

    def test_empty_stream_completed(self):
        chunks = [
            {'id': 'chatcmpl-e2', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]},
            {'id': 'chatcmpl-e2', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
        ]
        events = self._parse(self._simulate(chunks))
        comp = [e for e in events if e[0] == 'response.completed']
        self.assertEqual(len(comp), 1)
        self.assertEqual(comp[0][1]['response']['output'][0]['content'][0]['text'], '')

    def test_multi_tool_call(self):
        events = self._parse(self._simulate(SAMPLE_MULTI_TOOL_CALL_CHUNKS))
        types = [e[0] for e in events]
        self.assertIn('response.function_call_arguments.delta', types)
        self.assertIn('response.function_call_arguments.done', types)
        args_done = [e for e in events if e[0] == 'response.function_call_arguments.done']
        self.assertEqual(len(args_done), 2)

    def test_non_data_line_passthrough(self):
        h = proxy.ProxyHandler.__new__(proxy.ProxyHandler)
        h._seq = 0; h._item_id = None; h._response_id = None
        h._created = None; h._model = None; h._full_content = ''
        h._content_part_id = None; h._tool_calls = {}; h._finish_emitted = False
        r = h._convert_stream_line(b': comment')
        self.assertEqual(r, [b': comment\n'])

    def test_invalid_json_passthrough(self):
        h = proxy.ProxyHandler.__new__(proxy.ProxyHandler)
        h._seq = 0; h._item_id = None; h._response_id = None
        h._created = None; h._model = None; h._full_content = ''
        h._content_part_id = None; h._tool_calls = {}; h._finish_emitted = False
        r = h._convert_stream_line(b'data: {invalid')
        self.assertEqual(r, [b'data: {invalid\n'])

    def test_chunk_without_choices(self):
        chunks = [{'id': 'chatcmpl-nc', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'glm-5.1'}]
        events = self._parse(self._simulate(chunks))
        types = [e[0] for e in events]
        self.assertIn('response.created', types)
        self.assertNotIn('response.output_text.delta', types)

    def test_sequence_numbers_monotonic(self):
        events = self._parse(self._simulate(SAMPLE_CHAT_STREAM_CHUNKS))
        seqs = [e[1]['sequence_number'] for e in events if e[1] and 'sequence_number' in e[1]]
        self.assertEqual(seqs, sorted(seqs))

    def test_response_id_prefixed(self):
        events = self._parse(self._simulate(SAMPLE_CHAT_STREAM_CHUNKS))
        created = [e for e in events if e[0] == 'response.created']
        self.assertTrue(created[0][1]['response']['id'].startswith('resp_'))

    def test_response_id_no_double_prefix(self):
        chunks = [
            {'id': 'resp_abc', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'content': 'hi'}, 'finish_reason': None}]},
            {'id': 'resp_abc', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
        ]
        events = self._parse(self._simulate(chunks))
        created = [e for e in events if e[0] == 'response.created']
        self.assertEqual(created[0][1]['response']['id'], 'resp_abc')

    def test_tool_only_no_text(self):
        events = self._parse(self._simulate(SAMPLE_MULTI_TOOL_CALL_CHUNKS))
        types = [e[0] for e in events]
        self.assertNotIn('response.output_text.delta', types)
        self.assertIn('response.function_call_arguments.delta', types)
        self.assertIn('response.content_part.done', types)
        self.assertIn('response.output_item.done', types)

    def _simulate_no_done(self, chunks):
        """Simulate streaming without explicit [DONE] marker."""
        h = proxy.ProxyHandler.__new__(proxy.ProxyHandler)
        h._seq = 0
        h._item_id = None
        h._response_id = None
        h._created = None
        h._model = None
        h._full_content = ''
        h._content_part_id = None
        h._tool_calls = {}
        h._finish_emitted = False
        events = []
        for chunk in chunks:
            line = f'data: {json.dumps(chunk)}'.encode()
            for converted in h._convert_stream_line(line):
                events.append(converted)
        return events, h

    def test_stream_without_done_has_completed(self):
        chunks = [
            {'id': 'chatcmpl-nd', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]},
            {'id': 'chatcmpl-nd', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'content': 'hi'}, 'finish_reason': None}]},
            {'id': 'chatcmpl-nd', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
        ]
        events, h = self._simulate_no_done(chunks)
        # Without [DONE], _build_done_events is never called
        types = [self._parse([e])[0][0] for e in events if self._parse([e])]
        self.assertIn('response.output_item.done', types)
        # Now simulate end-of-stream cleanup
        done_events = h._build_done_events()
        parsed_done = self._parse(done_events)
        self.assertIn('response.completed', [e[0] for e in parsed_done])

    def test_stream_end_emits_done_when_no_done_marker(self):
        """If upstream closes without [DONE], _stream_response must emit completion."""
        import io
        h = proxy.ProxyHandler.__new__(proxy.ProxyHandler)
        h._seq = 0
        h._item_id = None
        h._response_id = None
        h._created = None
        h._model = None
        h._full_content = ''
        h._content_part_id = None
        h._tool_calls = {}
        h._finish_emitted = False
        h._done_emitted = False
        h.wfile = io.BytesIO()
        h.close_connection = False
        h.requestline = 'POST /responses HTTP/1.1'
        h.request_version = 'HTTP/1.1'
        h.client_address = ('127.0.0.1', 0)
        h.headers = {}

        class FakeUpstream:
            def __init__(self, lines):
                self._lines = lines
                self._idx = 0
            def read(self, n):
                if self._idx >= len(self._lines):
                    return b''
                chunk = self._lines[self._idx]
                self._idx += 1
                return chunk
            def close(self):
                pass

        lines = [
            b'data: {"id":"c1","choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}\n',
            b'data: {"id":"c1","choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n',
            b'data: {"id":"c1","choices":[{"delta":{},"finish_reason":"stop"}]}\n',
        ]
        h._stream_response(FakeUpstream(lines))
        raw = h.wfile.getvalue()
        self.assertIn(b'response.completed', raw)
        self.assertIn(b'[DONE]', raw)

    def test_stream_exception_emits_done(self):
        """If upstream errors mid-stream, client must still receive [DONE]."""
        import io
        h = proxy.ProxyHandler.__new__(proxy.ProxyHandler)
        h._seq = 0
        h._item_id = None
        h._response_id = None
        h._created = None
        h._model = None
        h._full_content = ''
        h._content_part_id = None
        h._tool_calls = {}
        h._finish_emitted = False
        h._done_emitted = False
        h.wfile = io.BytesIO()
        h.close_connection = False
        h.requestline = 'POST /responses HTTP/1.1'
        h.request_version = 'HTTP/1.1'
        h.client_address = ('127.0.0.1', 0)
        h.headers = {}

        class FailingUpstream:
            def __init__(self):
                self._sent = False
            def read(self, n):
                if not self._sent:
                    self._sent = True
                    return b'data: {"id":"c1","choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n'
                raise RuntimeError("upstream exploded")
            def close(self):
                pass

        h._stream_response(FailingUpstream())
        raw = h.wfile.getvalue()
        self.assertIn(b'[DONE]', raw)

    def test_duplicate_finish_reason_ignored(self):
        chunks = [
            {'id': 'chatcmpl-dup', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]},
            {'id': 'chatcmpl-dup', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'content': 'hi'}, 'finish_reason': None}]},
            {'id': 'chatcmpl-dup', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
            {'id': 'chatcmpl-dup', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
        ]
        events = self._parse(self._simulate(chunks))
        done = [e for e in events if e[0] == 'response.output_item.done']
        self.assertEqual(len(done), 1, f"Expected 1 output_item.done, got {len(done)}")

    def test_tool_call_id_updates_from_later_chunk(self):
        chunks = [
            {'id': 'chatcmpl-tid', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]},
            {'id': 'chatcmpl-tid', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': '', 'function': {'name': 'exec', 'arguments': ''}}]}, 'finish_reason': None}]},
            {'id': 'chatcmpl-tid', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': 'call_real', 'function': {'arguments': '{"cmd": "ls"}'}}]}, 'finish_reason': None}]},
            {'id': 'chatcmpl-tid', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]},
        ]
        events = self._parse(self._simulate(chunks))
        args_done = [e for e in events if e[0] == 'response.function_call_arguments.done']
        self.assertEqual(len(args_done), 1)
        self.assertEqual(args_done[0][1]['call_id'], 'call_real')
        output_done = [e for e in events if e[0] == 'response.output_item.done' and e[1]['item']['type'] == 'function_call']
        self.assertEqual(len(output_done), 1)
        self.assertEqual(output_done[0][1]['item']['call_id'], 'call_real')

    def test_tool_call_added_uses_real_id_when_first_delta_empty(self):
        """When the first tool_call delta lacks an id, output_item.added must
        be deferred until the real id arrives -- otherwise added and later
        delta events would carry mismatched item_ids."""
        chunks = [
            {'id': 'chatcmpl-x', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]},
            {'id': 'chatcmpl-x', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': '', 'function': {'name': 'exec', 'arguments': ''}}]}, 'finish_reason': None}]},
            {'id': 'chatcmpl-x', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': 'call_real', 'function': {'arguments': '{"cmd": "ls"}'}}]}, 'finish_reason': None}]},
            {'id': 'chatcmpl-x', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'glm-5.1', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]},
        ]
        events = self._parse(self._simulate(chunks))
        added = [e for e in events if e[0] == 'response.output_item.added'
                 and e[1]['item']['type'] == 'function_call']
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0][1]['item']['call_id'], 'call_real')
        self.assertEqual(added[0][1]['item']['id'], 'fc_call_real')
        deltas = [e for e in events if e[0] == 'response.function_call_arguments.delta']
        self.assertTrue(deltas)
        for d in deltas:
            self.assertEqual(d[1]['item_id'], 'fc_call_real')

class TestConnectionPool(unittest.TestCase):
    def test_acquire_returns_connection(self):
        pool = proxy.ConnectionPool()
        conn = pool.acquire('example.com', timeout=5)
        self.assertIsInstance(conn, http.client.HTTPSConnection)
        try: conn.close()
        except: pass

    def test_release_and_reuse(self):
        pool = proxy.ConnectionPool()
        conn = pool.acquire('example.com', timeout=5)
        pool.release(conn)
        conn2 = pool.acquire('example.com', timeout=5)
        try: conn2.close()
        except: pass

    def test_max_idle_discards(self):
        pool = proxy.ConnectionPool(max_idle=1)
        c1 = pool.acquire('a.com', timeout=5)
        c2 = pool.acquire('b.com', timeout=5)
        pool.release(c1)
        pool.release(c2)
        self.assertEqual(len(pool._pool), 1)

    def test_close_all(self):
        pool = proxy.ConnectionPool()
        conn = pool.acquire('example.com', timeout=5)
        pool.release(conn)
        pool.close_all()
        self.assertEqual(len(pool._pool), 0)

    def test_dead_connection_not_reused(self):
        pool = proxy.ConnectionPool()
        conn = pool.acquire('example.com', timeout=5)
        conn.sock = None
        pool.release(conn)
        conn2 = pool.acquire('example.com', timeout=5)
        self.assertIsNot(conn, conn2)
        try: conn2.close()
        except: pass
        pool.close_all()

    def test_acquire_with_multiple_dead_connections(self):
        pool = proxy.ConnectionPool()
        c1 = pool.acquire('example.com', timeout=5)
        c2 = pool.acquire('example.com', timeout=5)
        c1.sock = None
        c2.sock = None
        pool.release(c1)
        pool.release(c2)
        c3 = pool.acquire('example.com', timeout=5)
        self.assertIsNot(c3, c1)
        self.assertIsNot(c3, c2)
        # The pool should have cleaned up both dead connections
        self.assertEqual(len(pool._pool), 0)
        try: c3.close()
        except: pass
        pool.close_all()

    def test_thread_safety(self):
        pool = proxy.ConnectionPool()
        errors = []
        def worker():
            try:
                for _ in range(20):
                    conn = pool.acquire('example.com', timeout=5)
                    pool.release(conn)
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(errors), 0)
        pool.close_all()

class TestHTTPIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socket
        s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s1.bind(('127.0.0.1', 0)); cls.mock_port = s1.getsockname()[1]; s1.close()
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.bind(('127.0.0.1', 0)); cls.proxy_port = s2.getsockname()[1]; s2.close()

        class MockUpstream(http.server.BaseHTTPRequestHandler):
            _models_healthy = True

            def do_POST(self_inner):
                length = int(self_inner.headers.get('Content-Length', 0))
                body = json.loads(self_inner.rfile.read(length))
                if self_inner.path.endswith('/chat/completions'):
                    if 'reasoning' in body:
                        self_inner.send_response(400)
                        self_inner.send_header('Content-Type', 'application/json')
                        self_inner.end_headers()
                        self_inner.wfile.write(json.dumps({'error': 'unsupported parameter: reasoning'}).encode())
                        return
                    messages = body.get('messages', [])
                    last_content = messages[-1].get('content') if messages else ''
                    if last_content == 'trigger-400':
                        self_inner.send_response(400)
                        self_inner.send_header('Content-Type', 'application/json')
                        self_inner.end_headers()
                        self_inner.wfile.write(json.dumps({'error': 'bad request', 'reason': 'unsupported parameter: reasoning'}).encode())
                        return
                    if last_content == 'trigger-500':
                        self_inner.send_response(500)
                        self_inner.send_header('Content-Type', 'application/json')
                        self_inner.end_headers()
                        self_inner.wfile.write(json.dumps({'error': 'internal server error'}).encode())
                        return
                    if body.get('stream'):
                        self_inner.send_response(200)
                        self_inner.send_header('Content-Type', 'text/event-stream')
                        self_inner.end_headers()
                        for c in [
                            {'id': 't123', 'choices': [{'delta': {'role': 'assistant'}, 'finish_reason': None}]},
                            {'id': 't123', 'choices': [{'delta': {'content': 'Hi'}, 'finish_reason': None}]},
                            {'id': 't123', 'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
                        ]:
                            self_inner.wfile.write(f'data: {json.dumps(c)}\n\n'.encode())
                        self_inner.wfile.write(b'data: [DONE]\n\n')
                        # Real GLM closes the SSE connection after [DONE]; the proxy's
                        # stream reader relies on EOF to finish. Without this the
                        # keep-alive socket never closes and the proxy hangs.
                        self_inner.close_connection = True
                    else:
                        resp = {'id': 't123', 'created': 123, 'model': body.get('model', 'glm-5.1'),
                                'choices': [{'message': {'role': 'assistant', 'content': 'Hello!'}, 'finish_reason': 'stop'}],
                                'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}}
                        b = json.dumps(resp).encode()
                        self_inner.send_response(200)
                        self_inner.send_header('Content-Type', 'application/json')
                        self_inner.send_header('Content-Length', str(len(b)))
                        self_inner.end_headers()
                        self_inner.wfile.write(b)
                else:
                    self_inner.send_response(404); self_inner.end_headers()

            def do_GET(self_inner):
                if self_inner.path.endswith('/models'):
                    if not MockUpstream._models_healthy:
                        self_inner.send_response(503)
                        self_inner.end_headers()
                        return
                    b = json.dumps({'data': []}).encode()
                    self_inner.send_response(200)
                    self_inner.send_header('Content-Type', 'application/json')
                    self_inner.send_header('Content-Length', str(len(b)))
                    self_inner.end_headers()
                    self_inner.wfile.write(b)
                else:
                    self_inner.send_response(404); self_inner.end_headers()

            def log_message(self, fmt, *args): pass

        cls.mock_server = http.server.ThreadingHTTPServer(('127.0.0.1', cls.mock_port), MockUpstream)
        cls.mock_thread = threading.Thread(target=cls.mock_server.serve_forever, daemon=True)
        cls.mock_thread.start()
        cls._orig_base = proxy.API_BASE
        cls._orig_key = proxy.API_KEY
        proxy.API_BASE = f'http://127.0.0.1:{cls.mock_port}/v4'
        proxy.API_KEY = 'test-key'
        cls.proxy_server = proxy.ThreadedHTTPServer(('127.0.0.1', cls.proxy_port), proxy.ProxyHandler)
        cls.proxy_thread = threading.Thread(target=cls.proxy_server.serve_forever, daemon=True)
        cls.proxy_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.proxy_server.shutdown()
        cls.mock_server.shutdown()
        proxy.POOL.close_all()
        proxy.API_BASE = cls._orig_base
        proxy.API_KEY = cls._orig_key

    def _req(self, method, path, body=None):
        conn = hc.HTTPConnection('127.0.0.1', self.proxy_port, timeout=10)
        hdrs = {'Content-Type': 'application/json'}
        data = json.dumps(body).encode() if body else None
        if data: hdrs['Content-Length'] = str(len(data))
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        b = resp.read()
        conn.close()
        return resp.status, b, resp.getheader('Content-Type'), resp.getheader('Content-Length')

    def test_health_check(self):
        status, body, _, _ = self._req('GET', '/health')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['status'], 'ok')

    def test_non_streaming(self):
        status, body, ct, cl = self._req('POST', '/responses',
            {'model': 'glm-5.1', 'input': 'hello', 'stream': False})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data['object'], 'response')
        self.assertEqual(data['status'], 'completed')
        self.assertEqual(data['output'][0]['content'][0]['text'], 'Hello!')

    def test_content_length_header(self):
        status, body, ct, cl = self._req('POST', '/responses',
            {'model': 'glm-5.1', 'input': 'hi', 'stream': False})
        self.assertIsNotNone(cl, 'Content-Length header missing')
        self.assertEqual(int(cl), len(body))

    def test_streaming(self):
        status, body, ct, _ = self._req('POST', '/responses',
            {'model': 'glm-5.1', 'input': 'hello', 'stream': True})
        self.assertEqual(status, 200)
        self.assertIn('text/event-stream', ct or '')
        raw = body.decode('utf-8')
        self.assertIn('response.created', raw)
        self.assertIn('response.output_text.delta', raw)
        self.assertIn('response.completed', raw)
        self.assertIn('[DONE]', raw)

    def test_404_unknown_path(self):
        status, _, _, _ = self._req('GET', '/unknown')
        self.assertEqual(status, 404)

    def test_forward_models(self):
        status, body, _, _ = self._req('GET', '/models')
        self.assertEqual(status, 200)
        self.assertIn('data', json.loads(body))

    def test_model_mapping_in_request(self):
        status, body, _, _ = self._req('POST', '/responses',
            {'model': 'gpt-4o', 'input': 'hello', 'stream': False})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['status'], 'completed')

    def test_concurrent_requests(self):
        results, errors = [], []
        def do_req(i):
            try:
                conn = hc.HTTPConnection('127.0.0.1', self.proxy_port, timeout=10)
                b = json.dumps({'model': 'glm-5.1', 'input': f'req {i}', 'stream': False})
                conn.request('POST', '/responses', b, {'Content-Type': 'application/json'})
                resp = conn.getresponse(); data = resp.read(); conn.close()
                results.append((resp.status, json.loads(data)))
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=do_req, args=(i,)) for i in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(errors), 0, f'Errors: {errors}')
        self.assertEqual(len(results), 5)
        for status, data in results:
            self.assertEqual(status, 200)
            self.assertEqual(data['status'], 'completed')

    def test_reasoning_fallback(self):
        status, body, _, _ = self._req('POST', '/responses',
            {'model': 'glm-5.1', 'input': 'hello', 'stream': False, 'reasoning': {'effort': 'high'}})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data['status'], 'completed')

    def test_upstream_error_400(self):
        status, body, _, _ = self._req('POST', '/responses',
            {'model': 'glm-5.1', 'input': 'trigger-400', 'stream': False})
        self.assertEqual(status, 400)
        data = json.loads(body)
        self.assertIn('error', data)

    def test_upstream_error_500(self):
        status, body, _, _ = self._req('POST', '/responses',
            {'model': 'glm-5.1', 'input': 'trigger-500', 'stream': False})
        self.assertEqual(status, 500)
        data = json.loads(body)
        self.assertIn('error', data)

    def test_request_body_too_large(self):
        original_max = proxy.MAX_BODY_SIZE
        try:
            proxy.MAX_BODY_SIZE = 10
            status, body, _, _ = self._req('POST', '/responses',
                {'model': 'glm-5.1', 'input': 'x' * 20, 'stream': False})
            self.assertEqual(status, 413)
            self.assertIn('too large', json.loads(body)['error'])
        finally:
            proxy.MAX_BODY_SIZE = original_max

    def test_health_check_degraded(self):
        original = proxy.API_BASE
        try:
            proxy.API_BASE = 'http://127.0.0.1:1/v4'
            status, body, _, _ = self._req('GET', '/health')
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body)['status'], 'degraded')
        finally:
            proxy.API_BASE = original

    def test_health_check_upstream_503(self):
        handler_cls = self.__class__.mock_server.RequestHandlerClass
        handler_cls._models_healthy = False
        try:
            status, body, _, _ = self._req('GET', '/health')
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body)['status'], 'degraded')
        finally:
            handler_cls._models_healthy = True

class TestReasoningFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socket
        s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s1.bind(('127.0.0.1', 0)); cls.mock_port = s1.getsockname()[1]; s1.close()
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.bind(('127.0.0.1', 0)); cls.proxy_port = s2.getsockname()[1]; s2.close()

        class MockUpstream(http.server.BaseHTTPRequestHandler):
            def do_POST(self_inner):
                length = int(self_inner.headers.get('Content-Length', 0))
                body = json.loads(self_inner.rfile.read(length))
                if self_inner.path.endswith('/chat/completions'):
                    if 'reasoning' in body:
                        b = json.dumps({'error': 'unsupported parameter: reasoning'}).encode()
                        self_inner.send_response(400)
                        self_inner.send_header('Content-Type', 'application/json')
                        self_inner.send_header('Content-Length', str(len(b)))
                        self_inner.end_headers()
                        self_inner.wfile.write(b)
                        return
                    resp = {'id': 'rf123', 'created': 123, 'model': body.get('model', 'glm-5.1'),
                            'choices': [{'message': {'role': 'assistant', 'content': 'Hello without reasoning'}, 'finish_reason': 'stop'}],
                            'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}}
                    b = json.dumps(resp).encode()
                    self_inner.send_response(200)
                    self_inner.send_header('Content-Type', 'application/json')
                    self_inner.send_header('Content-Length', str(len(b)))
                    self_inner.end_headers()
                    self_inner.wfile.write(b)
                else:
                    self_inner.send_response(404); self_inner.end_headers()
            def log_message(self, fmt, *args): pass

        cls.mock_server = http.server.ThreadingHTTPServer(('127.0.0.1', cls.mock_port), MockUpstream)
        cls.mock_thread = threading.Thread(target=cls.mock_server.serve_forever, daemon=True)
        cls.mock_thread.start()
        cls._orig_base = proxy.API_BASE
        cls._orig_key = proxy.API_KEY
        proxy.API_BASE = f'http://127.0.0.1:{cls.mock_port}/v4'
        proxy.API_KEY = 'test-key'
        cls.proxy_server = proxy.ThreadedHTTPServer(('127.0.0.1', cls.proxy_port), proxy.ProxyHandler)
        cls.proxy_thread = threading.Thread(target=cls.proxy_server.serve_forever, daemon=True)
        cls.proxy_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.proxy_server.shutdown()
        cls.mock_server.shutdown()
        proxy.POOL.close_all()
        proxy.API_BASE = cls._orig_base
        proxy.API_KEY = cls._orig_key

    def test_reasoning_fallback_with_timeout(self):
        conn = hc.HTTPConnection('127.0.0.1', self.proxy_port, timeout=10)
        body = json.dumps({'model': 'glm-5.1', 'input': 'hello', 'stream': False, 'reasoning': {'effort': 'high'}}).encode()
        conn.request('POST', '/responses', body, {'Content-Type': 'application/json', 'Content-Length': str(len(body))})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(data)['status'], 'completed')

class TestUpstreamRequestRetry(unittest.TestCase):
    def test_retries_on_pooled_connection_timeout(self):
        h = proxy.ProxyHandler.__new__(proxy.ProxyHandler)
        class PooledConn:
            def __init__(self, host='x'):
                self.host = host
                self.calls = 0
            def request(self, *a, **k):
                self.calls += 1
                raise socket.timeout('timed out')
            def getresponse(self):
                pass
            def close(self):
                pass

        class FreshConn:
            def __init__(self, host='x'):
                self.host = host
                self.calls = 0
            def request(self, *a, **k):
                self.calls += 1
            def getresponse(self):
                class FakeResp:
                    status = 200
                    def getheader(self, name, default=None):
                        return 'application/json'
                return FakeResp()
            def close(self):
                pass

        fresh_conn = FreshConn()
        orig_https = http.client.HTTPSConnection
        orig_http = http.client.HTTPConnection
        try:
            http.client.HTTPSConnection = lambda *a, **k: fresh_conn
            http.client.HTTPConnection = lambda *a, **k: fresh_conn
            conn = PooledConn()
            conn2, resp = h._upstream_request(conn, 'example.com', '/v4', {}, {}, True, is_pooled=True)
            self.assertEqual(resp.status, 200)
            self.assertIsNot(conn2, conn)
            self.assertEqual(conn.calls, 1)
            self.assertEqual(conn2.calls, 1)
        finally:
            http.client.HTTPSConnection = orig_https
            http.client.HTTPConnection = orig_http

    def test_no_retry_on_fresh_connection_timeout(self):
        h = proxy.ProxyHandler.__new__(proxy.ProxyHandler)
        class FakeConn:
            calls = 0
            def request(self, *a, **k):
                FakeConn.calls += 1
                raise socket.timeout('timed out')
            def close(self):
                pass
        conn = FakeConn()
        with self.assertRaises(socket.timeout):
            h._upstream_request(conn, 'example.com', '/v4', {}, {}, True, is_pooled=False)


# ---------------------------------------------------------------------------
# Upstream proxy support: env resolution, URL parsing, NO_PROXY, pool behavior
# ---------------------------------------------------------------------------

class TestProxySupport(unittest.TestCase):
    """Coverage for the GLM_HTTP_PROXY / GLM_HTTPS_PROXY support."""

    _PROXY_VARS = (
        'GLM_HTTP_PROXY', 'GLM_HTTPS_PROXY',
        'HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
        'ALL_PROXY', 'all_proxy', 'NO_PROXY', 'no_proxy',
    )

    def _clear_proxy_env(self, env):
        for v in self._PROXY_VARS:
            env.pop(v, None)

    # -- _get_proxy_url priority chain --

    def test_glm_proxy_takes_priority(self):
        with patch.dict(os.environ, {}, clear=False) as env:
            self._clear_proxy_env(env)
            env['GLM_HTTPS_PROXY'] = 'http://glm:8080'
            env['HTTPS_PROXY'] = 'http://fallback:8080'
            self.assertEqual(proxy._get_proxy_url('https'), 'http://glm:8080')

    def test_falls_back_to_standard_env(self):
        with patch.dict(os.environ, {}, clear=False) as env:
            self._clear_proxy_env(env)
            env['HTTPS_PROXY'] = 'http://std:8080'
            self.assertEqual(proxy._get_proxy_url('https'), 'http://std:8080')

    def test_falls_back_to_all_proxy(self):
        with patch.dict(os.environ, {}, clear=False) as env:
            self._clear_proxy_env(env)
            env['ALL_PROXY'] = 'http://all:8080'
            self.assertEqual(proxy._get_proxy_url('https'), 'http://all:8080')
            self.assertEqual(proxy._get_proxy_url('http'), 'http://all:8080')

    def test_empty_string_treated_as_unset(self):
        with patch.dict(os.environ, {}, clear=False) as env:
            self._clear_proxy_env(env)
            env['GLM_HTTPS_PROXY'] = '   '
            env['HTTPS_PROXY'] = 'http://real:8080'
            self.assertEqual(proxy._get_proxy_url('https'), 'http://real:8080')

    def test_lowercase_env_var_supported(self):
        with patch.dict(os.environ, {}, clear=False) as env:
            self._clear_proxy_env(env)
            env['https_proxy'] = 'http://lower:8080'
            self.assertEqual(proxy._get_proxy_url('https'), 'http://lower:8080')

    def test_nothing_set_returns_empty(self):
        with patch.dict(os.environ, {}, clear=False) as env:
            self._clear_proxy_env(env)
            self.assertEqual(proxy._get_proxy_url('https'), '')
            self.assertEqual(proxy._get_proxy_url('http'), '')

    # -- _parse_proxy_url --

    def test_parse_plain_url(self):
        host, port, user, pwd = proxy._parse_proxy_url('http://proxy.local:3128')
        self.assertEqual((host, port, user, pwd), ('proxy.local', 3128, '', ''))

    def test_parse_default_port_http(self):
        _, port, _, _ = proxy._parse_proxy_url('http://proxy.local')
        self.assertEqual(port, 80)

    def test_parse_default_port_https(self):
        _, port, _, _ = proxy._parse_proxy_url('https://proxy.local')
        self.assertEqual(port, 443)

    def test_parse_credentials(self):
        host, port, user, pwd = proxy._parse_proxy_url('http://alice:s3cr3t@proxy.local:8080')
        self.assertEqual((host, port, user, pwd), ('proxy.local', 8080, 'alice', 's3cr3t'))

    def test_parse_rejects_unsupported_scheme(self):
        with self.assertRaises(ValueError):
            proxy._parse_proxy_url('socks5://proxy.local:1080')

    def test_parse_rejects_missing_host(self):
        with self.assertRaises(ValueError):
            proxy._parse_proxy_url('http://:8080')

    # -- _should_use_proxy (NO_PROXY) --

    def test_no_proxy_match_bypasses(self):
        with patch.dict(os.environ, {}, clear=False) as env:
            self._clear_proxy_env(env)
            env['NO_PROXY'] = 'example.com,internal.local'
            self.assertFalse(proxy._should_use_proxy('example.com'))
            self.assertFalse(proxy._should_use_proxy('internal.local'))

    def test_no_proxy_no_match_uses_proxy(self):
        with patch.dict(os.environ, {}, clear=False) as env:
            self._clear_proxy_env(env)
            env['NO_PROXY'] = 'internal.local'
            self.assertTrue(proxy._should_use_proxy('api.bigmodel.cn'))

    def test_no_proxy_unset_uses_proxy(self):
        with patch.dict(os.environ, {}, clear=False) as env:
            self._clear_proxy_env(env)
            self.assertTrue(proxy._should_use_proxy('api.bigmodel.cn'))

    # -- ConnectionPool.release: proxied connections are not pooled --

    def test_release_proxied_connection_not_pooled(self):
        # A tunneled (proxied) connection must not be reused: http.client
        # re-issues CONNECT on reuse, so release() closes it instead.
        pool = proxy.ConnectionPool()
        conn = pool.acquire('example.com', timeout=5)
        conn._tunnel_host = 'example.com'  # simulate set_tunnel
        before = len(pool._pool)
        pool.release(conn)
        self.assertEqual(len(pool._pool), before,
                         "proxied connection must not enter the pool")
        try:
            conn.close()
        except Exception:
            pass
        pool.close_all()

    def test_release_direct_connection_pooled(self):
        # A direct (non-tunneled) connection is still pooled normally.
        pool = proxy.ConnectionPool()
        conn = pool.acquire('example.com', timeout=5)
        self.assertFalse(getattr(conn, '_tunnel_host', None))
        pool.release(conn)
        self.assertEqual(len(pool._pool), 1)
        pool.close_all()


# ---------------------------------------------------------------------------
# Additional test cases for comprehensive coverage
# ---------------------------------------------------------------------------

class TestConvertResponsesToChatExtended(unittest.TestCase):
    """Extended edge-case tests for Responses->Chat conversion."""

    def _convert(self, body):
        return proxy.convert_responses_to_chat(body)

    def test_input_none_treated_as_missing(self):
        r = self._convert({'model': 'glm-5.1', 'input': None})
        self.assertEqual(r['messages'], [])

    def test_input_integer_ignored(self):
        r = self._convert({'model': 'glm-5.1', 'input': 42})
        self.assertEqual(r['messages'], [])

    def test_input_float_ignored(self):
        r = self._convert({'model': 'glm-5.1', 'input': 3.14})
        self.assertEqual(r['messages'], [])

    def test_instructions_whitespace_only_ignored(self):
        r = self._convert({'model': 'glm-5.1', 'instructions': '   ', 'input': 'hi'})
        self.assertFalse(any(m['role'] == 'system' for m in r['messages']))

    def test_multiple_input_text_parts_joined(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': 'Hello'},
                {'type': 'input_text', 'text': 'World'},
            ]}
        ]})
        self.assertEqual(len(r['messages']), 1)
        self.assertEqual(r['messages'][0]['content'], 'Hello World')

    def test_empty_input_text_skipped(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': ''},
                {'type': 'input_text', 'text': 'actual content'},
            ]}
        ]})
        self.assertEqual(r['messages'][0]['content'], ' actual content')

    def test_empty_content_list_no_message(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'message', 'role': 'user', 'content': []}
        ]})
        self.assertEqual(r['messages'], [])

    def test_function_call_missing_fields_defaults(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'function_call'},
        ]})
        tc = r['messages'][0]
        self.assertEqual(tc['role'], 'assistant')
        self.assertEqual(tc['tool_calls'][0]['id'], '')
        self.assertEqual(tc['tool_calls'][0]['function']['name'], '')
        self.assertEqual(tc['tool_calls'][0]['function']['arguments'], '{}')

    def test_function_call_output_missing_fields_defaults(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'function_call_output'},
        ]})
        self.assertEqual(r['messages'][0], {'role': 'tool', 'tool_call_id': '', 'content': ''})

    def test_developer_role_in_dict_messages(self):
        r = self._convert({'model': 'glm-5.1', 'input': {
            'messages': [{'role': 'developer', 'content': 'Be careful'}]
        }})
        self.assertEqual(r['messages'][0]['role'], 'system')
        self.assertEqual(r['messages'][0]['content'], 'Be careful')

    def test_unknown_content_type_not_dict_ignored(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'message', 'role': 'user', 'content': [
                'string_content',
                42,
                {'type': 'input_text', 'text': 'valid'},
            ]}
        ]})
        self.assertEqual(len(r['messages']), 1)
        self.assertEqual(r['messages'][0]['content'], 'valid')

    def test_tool_choice_object_passthrough(self):
        r = self._convert({'model': 'glm-5.1', 'input': 'test',
                          'tool_choice': {'type': 'function', 'function': {'name': 'exec'}}})
        self.assertEqual(r['tool_choice']['function']['name'], 'exec')

    def test_unknown_item_type_ignored(self):
        r = self._convert({'model': 'glm-5.1', 'input': [
            {'type': 'reasoning', 'summary': 'thinking...'},
            {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'ok'}]},
        ]})
        self.assertEqual(len(r['messages']), 1)

    def test_empty_dict_input_no_messages_no_content(self):
        r = self._convert({'model': 'glm-5.1', 'input': {}})
        self.assertEqual(r['messages'], [])

    def test_dict_input_messages_developer_role(self):
        r = self._convert({'model': 'glm-5.1', 'input': {
            'messages': [
                {'role': 'developer', 'content': 'sys'},
                {'role': 'user', 'content': 'hello'},
            ]
        }})
        self.assertEqual(len(r['messages']), 2)
        self.assertEqual(r['messages'][0]['role'], 'system')
        self.assertEqual(r['messages'][1]['role'], 'user')


class TestConvertChatToResponsesExtended(unittest.TestCase):
    """Extended edge-case tests for Chat->Responses conversion."""

    def test_content_none_with_tool_calls(self):
        """Tool-call-only response where content is explicitly None."""
        r = proxy.convert_chat_to_responses({
            'id': 'chatcmpl-tn', 'created': 1, 'model': 'glm-5.1',
            'choices': [{'message': {
                'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'call_x', 'type': 'function',
                                'function': {'name': 'exec', 'arguments': '{}'}}],
            }, 'finish_reason': 'tool_calls'}],
        })
        self.assertEqual(len(r['output']), 1)
        self.assertEqual(r['output'][0]['type'], 'function_call')
        self.assertEqual(r['status'], 'completed')

    def test_whitespace_only_content_ignored(self):
        r = proxy.convert_chat_to_responses({
            'id': 'c1', 'created': 1, 'model': 'glm-5.1',
            'choices': [{'message': {'role': 'assistant', 'content': '   '}, 'finish_reason': 'stop'}],
        })
        self.assertEqual(r['output'], [])

    def test_multiple_tool_calls(self):
        r = proxy.convert_chat_to_responses({
            'id': 'c2', 'created': 1, 'model': 'glm-5.1',
            'choices': [{'message': {
                'role': 'assistant', 'content': None,
                'tool_calls': [
                    {'id': 'call_1', 'type': 'function', 'function': {'name': 'exec', 'arguments': '{"cmd":"ls"}'}},
                    {'id': 'call_2', 'type': 'function', 'function': {'name': 'read', 'arguments': '{"file":"x"}'}},
                ],
            }, 'finish_reason': 'tool_calls'}],
        })
        self.assertEqual(len(r['output']), 2)
        self.assertEqual(r['output'][0]['name'], 'exec')
        self.assertEqual(r['output'][1]['name'], 'read')

    def test_text_and_tool_calls_ordered(self):
        r = proxy.convert_chat_to_responses({
            'id': 'c3', 'created': 1, 'model': 'glm-5.1',
            'choices': [{'message': {
                'role': 'assistant', 'content': 'Let me check.',
                'tool_calls': [{'id': 'call_1', 'type': 'function', 'function': {'name': 'exec', 'arguments': '{}'}}],
            }, 'finish_reason': 'tool_calls'}],
        })
        self.assertEqual(len(r['output']), 2)
        self.assertEqual(r['output'][0]['type'], 'message')
        self.assertEqual(r['output'][1]['type'], 'function_call')

    def test_no_message_key(self):
        r = proxy.convert_chat_to_responses({
            'id': 'c4', 'created': 1, 'model': 'glm-5.1',
            'choices': [{'finish_reason': 'stop'}],
        })
        self.assertEqual(r['output'], [])
        self.assertEqual(r['status'], 'completed')

    def test_empty_choices(self):
        r = proxy.convert_chat_to_responses({
            'id': 'c5', 'created': 1, 'model': 'glm-5.1',
            'choices': [],
        })
        self.assertEqual(r['output'], [])

    def test_usage_preserved(self):
        r = proxy.convert_chat_to_responses({
            'id': 'c6', 'created': 1, 'model': 'glm-5.1',
            'choices': [{'message': {'role': 'assistant', 'content': 'hi'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150},
        })
        self.assertEqual(r['usage']['total_tokens'], 150)

    def test_unknown_finish_reason_treats_as_completed(self):
        r = proxy.convert_chat_to_responses({
            'id': 'c7', 'created': 1, 'model': 'glm-5.1',
            'choices': [{'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'tool_use'}],
        })
        self.assertEqual(r['status'], 'completed')
        self.assertNotIn('incomplete_details', r)


class TestStreamingConversionExtended(unittest.TestCase):
    """Extended streaming edge-case tests."""

    def _make_handler(self):
        h = proxy.ProxyHandler.__new__(proxy.ProxyHandler)
        h._seq = 0
        h._item_id = None
        h._response_id = None
        h._created = None
        h._model = None
        h._full_content = ''
        h._content_part_id = None
        h._tool_calls = {}
        return h

    def _simulate(self, chunks):
        h = self._make_handler()
        events = []
        for chunk in chunks:
            line = f'data: {json.dumps(chunk)}'.encode()
            for converted in h._convert_stream_line(line):
                events.append(converted)
        for converted in h._convert_stream_line(b'data: [DONE]'):
            events.append(converted)
        return events

    def _parse(self, raw_events):
        parsed = []
        for raw in raw_events:
            text = raw.decode('utf-8')
            event_type = data = None
            for line in text.strip().split('\n'):
                if line.startswith('event: '):
                    event_type = line[7:]
                elif line.startswith('data: '):
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        data = line[6:]
            if event_type:
                parsed.append((event_type, data))
        return parsed

    def test_finish_reason_tool_calls_with_content(self):
        """Stream with both text content and tool calls, finished by tool_calls."""
        chunks = [
            {'id': 'chatcmpl-ttc', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'glm-5.1',
             'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]},
            {'id': 'chatcmpl-ttc', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'glm-5.1',
             'choices': [{'index': 0, 'delta': {'content': 'Running '}, 'finish_reason': None}]},
            {'id': 'chatcmpl-ttc', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'glm-5.1',
             'choices': [{'index': 0, 'delta': {'content': 'ls'}, 'finish_reason': None}]},
            {'id': 'chatcmpl-ttc', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'glm-5.1',
             'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': 'call_1', 'function': {'name': 'exec', 'arguments': '{"cmd":"ls"}'}}]}, 'finish_reason': None}]},
            {'id': 'chatcmpl-ttc', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'glm-5.1',
             'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]},
        ]
        events = self._parse(self._simulate(chunks))
        types = [e[0] for e in events]
        self.assertIn('response.output_text.delta', types)
        self.assertIn('response.function_call_arguments.delta', types)
        self.assertIn('response.function_call_arguments.done', types)
        # Check content assembled
        text_done = [e for e in events if e[0] == 'response.output_text.done']
        self.assertEqual(len(text_done), 1)
        self.assertEqual(text_done[0][1]['text'], 'Running ls')
        # Check tool args assembled
        args_done = [e for e in events if e[0] == 'response.function_call_arguments.done']
        self.assertEqual(len(args_done), 1)
        self.assertEqual(args_done[0][1]['arguments'], '{"cmd":"ls"}')

    def test_chunk_missing_id_uses_default(self):
        chunks = [
            {'object': 'chat.completion.chunk', 'created': 1, 'model': 'glm-5.1',
             'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]},
            {'object': 'chat.completion.chunk', 'created': 1, 'model': 'glm-5.1',
             'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
        ]
        events = self._parse(self._simulate(chunks))
        created = [e for e in events if e[0] == 'response.created']
        self.assertEqual(len(created), 1)
        resp_id = created[0][1]['response']['id']
        self.assertTrue(resp_id.startswith('resp_'))

    def test_sse_format_correctness(self):
        """Verify each converted event has proper SSE format: event + data."""
        events = self._simulate(SAMPLE_CHAT_STREAM_CHUNKS)
        for raw in events:
            text = raw.decode('utf-8')
            if text.startswith(':') or text.strip() == 'data: [DONE]' or text.strip() == '':
                continue
            # Should have event: line and data: line
            lines = text.strip().split('\n')
            self.assertTrue(any(l.startswith('event: ') for l in lines),
                          f"Missing 'event:' line in: {text[:100]}")
            self.assertTrue(any(l.startswith('data: ') for l in lines),
                          f"Missing 'data:' line in: {text[:100]}")
            self.assertTrue(text.endswith('\n\n'), f"SSE event should end with double newline: {repr(text[-10:])}")

    def test_empty_string_content_not_emitted(self):
        """Delta with empty string content should not produce text delta events."""
        chunks = [
            {'id': 'x', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'glm-5.1',
             'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]},
            {'id': 'x', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'glm-5.1',
             'choices': [{'index': 0, 'delta': {'content': ''}, 'finish_reason': None}]},
            {'id': 'x', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'glm-5.1',
             'choices': [{'index': 0, 'delta': {'content': 'actual'}, 'finish_reason': None}]},
            {'id': 'x', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'glm-5.1',
             'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
        ]
        events = self._parse(self._simulate(chunks))
        deltas = [e for e in events if e[0] == 'response.output_text.delta']
        # Empty string content is still truthy in Python, so it will emit deltas
        # but the full_content should be "actual" (empty string + "actual")
        text_done = [e for e in events if e[0] == 'response.output_text.done']
        self.assertEqual(text_done[0][1]['text'], 'actual')

    def test_sequence_numbers_unique(self):
        events = self._parse(self._simulate(SAMPLE_CHAT_STREAM_CHUNKS))
        seqs = [e[1]['sequence_number'] for e in events if e[1] and 'sequence_number' in e[1]]
        self.assertEqual(len(seqs), len(set(seqs)), "Sequence numbers should be unique")

    def test_completed_response_structure(self):
        events = self._parse(self._simulate(SAMPLE_CHAT_STREAM_CHUNKS))
        comp = [e for e in events if e[0] == 'response.completed']
        self.assertEqual(len(comp), 1)
        resp = comp[0][1]['response']
        self.assertEqual(resp['status'], 'completed')
        self.assertEqual(resp['object'], 'response')
        self.assertIn('output', resp)
        self.assertIn('id', resp)


class TestConnectionPoolExtended(unittest.TestCase):
    """Extended connection pool edge-case tests."""

    def test_max_idle_zero(self):
        """With max_idle=0, all released connections should be discarded."""
        pool = proxy.ConnectionPool(max_idle=0)
        conn = pool.acquire('example.com', timeout=5)
        pool.release(conn)
        self.assertEqual(len(pool._pool), 0)

    def test_release_closed_connection(self):
        """Releasing an already-closed connection should not raise."""
        pool = proxy.ConnectionPool()
        conn = pool.acquire('example.com', timeout=5)
        conn.close()
        pool.release(conn)  # Should not raise
        pool.close_all()

    def test_acquire_different_hosts(self):
        """Pool should manage connections for different hosts separately."""
        pool = proxy.ConnectionPool()
        # Treat fresh connections as alive so we can verify host-keyed storage.
        with patch.object(pool, '_conn_alive', return_value=True):
            c1 = pool.acquire('host-a.com', timeout=5)
            c2 = pool.acquire('host-b.com', timeout=5)
            pool.release(c1)
            pool.release(c2)
            self.assertEqual(len(pool._pool), 2)
            # Acquire host-a should reuse
            c3 = pool.acquire('host-a.com', timeout=5)
            self.assertIs(c3, c1)
            # host-b still in pool
            hosts_in_pool = [h for h, _, _ in pool._pool]
            self.assertIn('host-b.com', hosts_in_pool)
            try: c3.close()
            except: pass
            pool.close_all()

    def test_acquire_http_connection(self):
        """use_ssl=False should return HTTPConnection."""
        pool = proxy.ConnectionPool()
        conn = pool.acquire('example.com', timeout=5, use_ssl=False)
        self.assertIsInstance(conn, http.client.HTTPConnection)
        try: conn.close()
        except: pass

    def test_close_all_empty(self):
        """close_all on empty pool should not raise."""
        pool = proxy.ConnectionPool()
        pool.close_all()  # Should not raise

    def test_release_overflow_closes(self):
        """Releasing beyond max_idle should close the connection."""
        pool = proxy.ConnectionPool(max_idle=2)
        conns = [pool.acquire(f'h{i}.com', timeout=5) for i in range(4)]
        for c in conns:
            pool.release(c)
        self.assertEqual(len(pool._pool), 2)

    def test_concurrent_stress(self):
        """Higher concurrency stress test."""
        pool = proxy.ConnectionPool(max_idle=4)
        errors = []
        def worker(wid):
            try:
                for _ in range(50):
                    conn = pool.acquire(f'host-{wid}.example.com', timeout=5, use_ssl=False)
                    pool.release(conn)
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        pool.close_all()



class TestHTTPIntegrationExtended(unittest.TestCase):
    """Extended HTTP integration tests for error handling and edge cases."""

    @classmethod
    def setUpClass(cls):
        import socket as sk
        s1 = sk.socket(sk.AF_INET, sk.SOCK_STREAM)
        s1.bind(('127.0.0.1', 0)); cls.mock_port = s1.getsockname()[1]; s1.close()
        s2 = sk.socket(sk.AF_INET, sk.SOCK_STREAM)
        s2.bind(('127.0.0.1', 0)); cls.proxy_port = s2.getsockname()[1]; s2.close()

        class MockUpstream(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_POST(self_inner):
                length = int(self_inner.headers.get('Content-Length', 0))
                body_raw = self_inner.rfile.read(length) if length > 0 else b''
                try:
                    body = json.loads(body_raw) if body_raw else {}
                except json.JSONDecodeError:
                    err_b = json.dumps({'error': 'invalid json'}).encode()
                    self_inner.send_response(400)
                    self_inner.send_header('Content-Type', 'application/json')
                    self_inner.send_header('Content-Length', str(len(err_b)))
                    self_inner.end_headers()
                    self_inner.wfile.write(err_b)
                    return

                if self_inner.path.endswith('/chat/completions'):
                    messages = body.get('messages', [])
                    last_content = messages[-1].get('content') if messages else ''

                    if last_content == 'trigger-tool-call':
                        resp = {
                            'id': 'tc-resp', 'created': 1, 'model': 'glm-5.1',
                            'choices': [{'message': {
                                'role': 'assistant', 'content': None,
                                'tool_calls': [{'id': 'call_1', 'type': 'function',
                                                'function': {'name': 'exec', 'arguments': '{"cmd":"ls"}'}}]
                            }, 'finish_reason': 'tool_calls'}],
                            'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}
                        }
                        b = json.dumps(resp).encode()
                        self_inner.send_response(200)
                        self_inner.send_header('Content-Type', 'application/json')
                        self_inner.send_header('Content-Length', str(len(b)))
                        self_inner.end_headers()
                        self_inner.wfile.write(b)
                    elif body.get('stream'):
                        self_inner.send_response(200)
                        self_inner.send_header('Content-Type', 'text/event-stream')
                        self_inner.end_headers()
                        for c in [
                            {'id': 's1', 'choices': [{'delta': {'role': 'assistant'}, 'finish_reason': None}]},
                            {'id': 's1', 'choices': [{'delta': {'content': 'Streamed'}, 'finish_reason': None}]},
                            {'id': 's1', 'choices': [{'delta': {'content': ' response'}, 'finish_reason': None}]},
                            {'id': 's1', 'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
                        ]:
                            self_inner.wfile.write(f'data: {json.dumps(c)}\n\n'.encode())
                        self_inner.wfile.write(b'data: [DONE]\n\n')
                        # Real GLM closes the SSE connection after [DONE]; the proxy's
                        # stream reader relies on EOF to finish.
                        self_inner.close_connection = True
                    else:
                        resp = {'id': 'ext1', 'created': 1, 'model': body.get('model', 'glm-5.1'),
                                'choices': [{'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}],
                                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
                        b = json.dumps(resp).encode()
                        self_inner.send_response(200)
                        self_inner.send_header('Content-Type', 'application/json')
                        self_inner.send_header('Content-Length', str(len(b)))
                        self_inner.end_headers()
                        self_inner.wfile.write(b)
                else:
                    self_inner.send_response(404)
                    self_inner.send_header('Content-Length', '0')
                    self_inner.end_headers()

            def do_GET(self_inner):
                if self_inner.path.endswith('/models'):
                    b = json.dumps({'data': [{'id': 'glm-5.1'}]}).encode()
                    self_inner.send_response(200)
                    self_inner.send_header('Content-Type', 'application/json')
                    self_inner.send_header('Content-Length', str(len(b)))
                    self_inner.end_headers()
                    self_inner.wfile.write(b)
                else:
                    self_inner.send_response(404)
                    self_inner.send_header('Content-Length', '0')
                    self_inner.end_headers()

            def log_message(self, fmt, *args): pass

        cls.mock_server = http.server.ThreadingHTTPServer(('127.0.0.1', cls.mock_port), MockUpstream)
        cls.mock_thread = threading.Thread(target=cls.mock_server.serve_forever, daemon=True)
        cls.mock_thread.start()
        cls._orig_base = proxy.API_BASE
        cls._orig_key = proxy.API_KEY
        proxy.API_BASE = f'http://127.0.0.1:{cls.mock_port}/v4'
        proxy.API_KEY = 'test-key'
        cls.proxy_server = proxy.ThreadedHTTPServer(('127.0.0.1', cls.proxy_port), proxy.ProxyHandler)
        cls.proxy_thread = threading.Thread(target=cls.proxy_server.serve_forever, daemon=True)
        cls.proxy_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.proxy_server.shutdown()
        cls.mock_server.shutdown()
        proxy.POOL.close_all()
        proxy.API_BASE = cls._orig_base
        proxy.API_KEY = cls._orig_key

    def _req(self, method, path, body=None, raw_body=None, headers=None):
        conn = hc.HTTPConnection('127.0.0.1', self.proxy_port, timeout=5)
        hdrs = headers or {'Content-Type': 'application/json'}
        if body is not None:
            data = json.dumps(body).encode()
            hdrs['Content-Length'] = str(len(data))
        elif raw_body is not None:
            data = raw_body
            hdrs['Content-Length'] = str(len(data))
        else:
            data = None
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        b = resp.read()
        conn.close()
        return resp.status, b, resp.getheader('Content-Type'), resp.getheader('Content-Length')

    # -- Request validation tests --

    def test_invalid_json_body(self):
        """Non-JSON body should return 400."""
        status, body, _, _ = self._req('POST', '/responses', raw_body=b'not json at all')
        self.assertEqual(status, 400)
        data = json.loads(body)
        self.assertIn('invalid JSON', data['error'])

    def test_empty_json_object(self):
        """Empty JSON object {} should still work (no input)."""
        status, body, _, _ = self._req('POST', '/responses', body={})
        self.assertEqual(status, 200)

    def test_json_array_body(self):
        """JSON array body should return 400."""
        status, body, _, _ = self._req('POST', '/responses', raw_body=b'[1,2,3]')
        self.assertEqual(status, 400)
        data = json.loads(body)
        self.assertIn('JSON object', data['error'])

    def test_invalid_content_length_header(self):
        """Non-numeric Content-Length should return 400."""
        # Pass headers only (no body=) so _req does not overwrite the bogus
        # Content-Length with the real one — otherwise the proxy never sees 'abc'.
        status, body, _, _ = self._req('POST', '/responses',
                                       headers={'Content-Type': 'application/json', 'Content-Length': 'abc'})
        self.assertEqual(status, 400)

    def test_no_content_length_get(self):
        """GET requests without Content-Length should work."""
        status, body, _, _ = self._req('GET', '/health')
        self.assertEqual(status, 200)

    # -- Response format tests --

    def test_response_has_usage(self):
        """Non-streaming response should include usage."""
        status, body, _, _ = self._req('POST', '/responses',
            {'model': 'glm-5.1', 'input': 'hello', 'stream': False})
        data = json.loads(body)
        self.assertIn('usage', data)
        self.assertIn('total_tokens', data['usage'])

    def test_response_has_id_and_created(self):
        status, body, _, _ = self._req('POST', '/responses',
            {'model': 'glm-5.1', 'input': 'hello', 'stream': False})
        data = json.loads(body)
        self.assertTrue(data['id'])
        self.assertIsInstance(data['created'], int)

    def test_tool_call_response_format(self):
        """Tool call response should have proper function_call output."""
        status, body, _, _ = self._req('POST', '/responses',
            {'model': 'glm-5.1', 'input': 'trigger-tool-call', 'stream': False})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data['status'], 'completed')
        fc = [o for o in data['output'] if o['type'] == 'function_call']
        self.assertEqual(len(fc), 1)
        self.assertEqual(fc[0]['name'], 'exec')
        self.assertEqual(fc[0]['call_id'], 'call_1')

    # -- Streaming extended tests --

    def test_streaming_content_assembled(self):
        """Streaming should assemble content correctly."""
        status, body, ct, _ = self._req('POST', '/responses',
            {'model': 'glm-5.1', 'input': 'hello', 'stream': True})
        self.assertEqual(status, 200)
        raw = body.decode('utf-8')
        self.assertIn('Streamed response', raw)

    def test_streaming_has_all_event_types(self):
        """Streaming response should contain all required SSE event types."""
        status, body, ct, _ = self._req('POST', '/responses',
            {'model': 'glm-5.1', 'input': 'hello', 'stream': True})
        raw = body.decode('utf-8')
        for event_type in ['response.created', 'response.output_text.delta',
                          'response.output_text.done', 'response.content_part.done',
                          'response.output_item.done', 'response.completed']:
            self.assertIn(event_type, raw, f"Missing event type: {event_type}")
        self.assertIn('[DONE]', raw)

    # -- Method handling tests --

    def test_post_non_responses_forwarded(self):
        """POST to non-/responses path should be forwarded."""
        status, body, _, _ = self._req('POST', '/other')
        # mock returns 404 for unknown POST paths
        self.assertEqual(status, 404)

    # -- Forward path tests --

    def test_forward_models_has_data(self):
        """Forwarded /models should return upstream data."""
        status, body, _, _ = self._req('GET', '/models')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn('data', data)
        self.assertEqual(len(data['data']), 1)
        self.assertEqual(data['data'][0]['id'], 'glm-5.1')

    def test_forward_v4_models_stripped(self):
        """GET /v4/models should strip /v4 prefix before forwarding."""
        status, body, _, _ = self._req('GET', '/v4/models')
        self.assertEqual(status, 200)

    # -- Concurrent streaming test --

    def test_concurrent_mixed_requests(self):
        """Mix of streaming and non-streaming concurrent requests."""
        results, errors = [], []
        def do_stream(i):
            try:
                conn = hc.HTTPConnection('127.0.0.1', self.proxy_port, timeout=5)
                b = json.dumps({'model': 'glm-5.1', 'input': f'stream {i}', 'stream': True})
                conn.request('POST', '/responses', b, {'Content-Type': 'application/json'})
                resp = conn.getresponse(); data = resp.read(); conn.close()
                results.append(('stream', resp.status, data))
            except Exception as e:
                errors.append(('stream', e))

        def do_normal(i):
            try:
                conn = hc.HTTPConnection('127.0.0.1', self.proxy_port, timeout=5)
                b = json.dumps({'model': 'glm-5.1', 'input': f'normal {i}', 'stream': False})
                conn.request('POST', '/responses', b, {'Content-Type': 'application/json'})
                resp = conn.getresponse(); data = resp.read(); conn.close()
                results.append(('normal', resp.status, data))
            except Exception as e:
                errors.append(('normal', e))

        threads = []
        for i in range(3):
            threads.append(threading.Thread(target=do_stream, args=(i,)))
            threads.append(threading.Thread(target=do_normal, args=(i,)))
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(results), 6)
        stream_results = [r for r in results if r[0] == 'stream']
        normal_results = [r for r in results if r[0] == 'normal']
        for _, status, data in stream_results:
            self.assertEqual(status, 200)
            self.assertIn(b'response.created', data)
        for _, status, data in normal_results:
            self.assertEqual(status, 200)
            d = json.loads(data)
            self.assertEqual(d['status'], 'completed')



# ---------------------------------------------------------------------------
# Regression tests for the 5 review fixes
# ---------------------------------------------------------------------------

class TestReviewFixes(unittest.TestCase):
    """Regression coverage for the defects found during code review."""

    # -- shared streaming simulator ----------------------------------------

    def _simulate_stream(self, chunks_bytes):
        h = proxy.ProxyHandler.__new__(proxy.ProxyHandler)
        h._seq = 0
        h._item_id = None
        h._response_id = None
        h._created = None
        h._model = None
        h._full_content = ""
        h._content_part_id = None
        h._tool_calls = {}
        h._finish_emitted = False
        h._done_emitted = False
        events = []
        for line in chunks_bytes:
            for e in h._convert_stream_line(line):
                events.append(e.decode(errors='replace'))
        return events

    @staticmethod
    def _event_names(events):
        return [e.splitlines()[0] for e in events if e.startswith('event:')]

    # -- Fix #1: [DONE] without prior finish_reason ------------------------

    def test_done_without_finish_emits_finish_events(self):
        """When upstream skips finish_reason and sends [DONE] directly, the
        text/content_part/output_item done events must still be emitted."""
        content = b'data: ' + json.dumps({
            'id': 'x', 'created': 1, 'model': 'm',
            'choices': [{'index': 0, 'delta': {'content': 'hi'}, 'finish_reason': None}],
        }).encode()
        events = self._simulate_stream([content, b'data: [DONE]'])
        names = self._event_names(events)
        self.assertIn('event: response.output_text.done', names)
        self.assertIn('event: response.content_part.done', names)
        self.assertIn('event: response.output_item.done', names)
        self.assertIn('event: response.completed', names)
        self.assertIn('event: response.output_text.delta', names)
        self.assertTrue(any('[DONE]' in e for e in events))

    def test_done_with_finish_does_not_duplicate(self):
        """Normal flow (finish_reason then [DONE]) must emit output_text.done
        exactly once -- the [DONE] path must not re-emit finish events."""
        chunk = b'data: ' + json.dumps({
            'id': 'x', 'created': 1, 'model': 'm',
            'choices': [{'index': 0, 'delta': {'content': 'hi'}, 'finish_reason': 'stop'}],
        }).encode()
        events = self._simulate_stream([chunk, b'data: [DONE]'])
        names = self._event_names(events)
        self.assertEqual(names.count('event: response.output_text.done'), 1)
        self.assertEqual(names.count('event: response.content_part.done'), 1)
        self.assertEqual(names.count('event: response.output_item.done'), 1)
        self.assertEqual(names.count('event: response.completed'), 1)

    def test_done_only_without_init_emits_nothing_extra(self):
        """If [DONE] arrives before any content chunk (no _item_id), only the
        terminal completed event sequence fires -- no spurious finish events."""
        events = self._simulate_stream([b'data: [DONE]'])
        names = self._event_names(events)
        # No content_part.done / output_text.done without an item
        self.assertNotIn('event: response.output_text.done', names)
        self.assertNotIn('event: response.content_part.done', names)

    # -- Fix #2: tool_choice function form conversion ----------------------

    def test_tool_choice_function_form_converted(self):
        r = proxy.convert_responses_to_chat({
            'model': 'glm-5.2', 'input': 'hi',
            'tool_choice': {'type': 'function', 'name': 'foo'},
            'tools': [{'type': 'function', 'name': 'foo', 'parameters': {'type': 'object'}}],
        })
        self.assertEqual(r['tool_choice'],
                         {'type': 'function', 'function': {'name': 'foo'}})

    def test_tool_choice_string_forms_passthrough(self):
        for tc in ('auto', 'none', 'required'):
            r = proxy.convert_responses_to_chat({'model': 'glm-5.2', 'input': 'hi', 'tool_choice': tc})
            self.assertEqual(r['tool_choice'], tc)

    def test_tool_choice_none_passthrough(self):
        r = proxy.convert_responses_to_chat({'model': 'glm-5.2', 'input': 'hi', 'tool_choice': 'none'})
        self.assertEqual(r['tool_choice'], 'none')

    # -- Fix #3: created_at field (created kept for compat) ----------------

    def test_non_stream_has_created_at(self):
        r = proxy.convert_chat_to_responses({'id': 'abc', 'created': 123, 'model': 'm', 'choices': []})
        self.assertEqual(r.get('created_at'), 123)

    def test_non_stream_keeps_created_for_compat(self):
        r = proxy.convert_chat_to_responses({'id': 'abc', 'created': 123, 'model': 'm', 'choices': []})
        self.assertEqual(r.get('created'), 123)

    # -- Fix #4: non-stream id normalization -------------------------------

    def test_non_stream_id_gets_resp_prefix(self):
        r = proxy.convert_chat_to_responses({'id': 'abc', 'created': 1, 'model': 'm', 'choices': []})
        self.assertEqual(r['id'], 'resp_abc')

    def test_non_stream_id_already_prefixed_untouched(self):
        r = proxy.convert_chat_to_responses({'id': 'resp_xyz', 'created': 1, 'model': 'm', 'choices': []})
        self.assertEqual(r['id'], 'resp_xyz')

    def test_non_stream_empty_id_not_invented(self):
        r = proxy.convert_chat_to_responses({})
        self.assertEqual(r['id'], '')

    # -- Fix #5: reasoning=null must not enable retry path -----------------

    def test_reasoning_null_not_propagated(self):
        r = proxy.convert_responses_to_chat({'model': 'glm-5.2', 'input': 'hi', 'reasoning': None})
        self.assertNotIn('reasoning', r)

    def test_reasoning_xhigh_still_maps_to_max(self):
        r = proxy.convert_responses_to_chat({'model': 'glm-5.2', 'input': 'hi', 'reasoning': {'effort': 'xhigh'}})
        self.assertEqual(r['reasoning'], {'effort': 'max'})

    def test_reasoning_normal_effort_passthrough(self):
        for effort in ('low', 'medium', 'high'):
            r = proxy.convert_responses_to_chat({'model': 'glm-5.2', 'input': 'hi', 'reasoning': {'effort': effort}})
            self.assertEqual(r['reasoning'], {'effort': effort})

    def test_reasoning_absent_not_propagated(self):
        r = proxy.convert_responses_to_chat({'model': 'glm-5.2', 'input': 'hi'})
        self.assertNotIn('reasoning', r)



if __name__ == '__main__':
    unittest.main(verbosity=2)

