import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from wb_gateway.anthropic import anthropic_to_chat
from wb_gateway.protocol import contract, prepare, responses_to_chat, validate_chat
from wb_gateway.server import Gateway, create_app
from wb_gateway.storage import Fault


class SystemPromptTests(unittest.TestCase):
    def chat(self, **extra):
        return {"model": "test-model", "messages": [{"role": "user", "content": "你好"}], **extra}

    def test_no_default_prompt_in_either_region(self):
        with patch.dict(os.environ, {"WB_DEFAULT_SYSTEM_PROMPT": "unwanted preset"}):
            for region in ("cn", "intl"):
                with self.subTest(region=region):
                    body = self.chat()
                    before = copy.deepcopy(body)
                    self.assertEqual(prepare(validate_chat(body), region)["messages"], before["messages"])
                    self.assertEqual(body, before)

    def test_caller_system_content_is_preserved(self):
        for region in ("cn", "intl"):
            for content in ("", "\u200b", "  用户指定的身份\n", [{"type": "text", "text": "规则 A"}]):
                with self.subTest(region=region, content=content):
                    body = self.chat()
                    body["messages"].insert(0, {"role": "system", "content": content})
                    expected = content[0]["text"] if isinstance(content, list) else content
                    result = prepare(validate_chat(body), region)
                    self.assertEqual(result["messages"][0], {"role": "system", "content": expected})

    def test_developer_normalization_adds_no_explanatory_text(self):
        body = self.chat()
        body["messages"][:0] = [{"role": "developer", "content": "开发者规则"},
                                {"role": "system", "content": "系统规则"}]
        result = prepare(validate_chat(body), "intl")
        self.assertEqual(result["messages"][0]["content"], "系统规则\n开发者规则")
        body["messages"] = [{"role": "developer", "content": ""}, body["messages"][-1]]
        self.assertEqual(prepare(body, "cn")["messages"][0], {"role": "system", "content": ""})

    def test_json_format_forwarded_without_system_message(self):
        formats = [{"type": "json_object"}, {"type": "json_schema", "json_schema": {
            "name": "answer", "strict": True, "schema": {"type": "object", "properties": {}}}}]
        for region in ("cn", "intl"):
            for fmt in formats:
                with self.subTest(region=region, fmt=fmt):
                    body = self.chat(response_format=fmt)
                    result = prepare(validate_chat(body), region)
                    self.assertEqual(result["messages"], body["messages"])
                    self.assertEqual(result["response_format"], fmt)

    def test_api_adapters_preserve_absent_empty_and_custom_instructions(self):
        for region in ("cn", "intl"):
            for value in (None, "", "只遵循客户端规则"):
                for protocol in ("responses", "messages"):
                    with self.subTest(region=region, value=value, protocol=protocol):
                        if protocol == "responses":
                            body = {"model": "test-model", "input": "你好", "instructions": value}
                            chat = responses_to_chat(body)
                        else:
                            body = self.chat(max_tokens=64, system=value)
                            chat = anthropic_to_chat(body)
                        result = prepare(chat, region)
                        systems = [m["content"] for m in result["messages"] if m["role"] == "system"]
                        self.assertEqual(systems, [] if value is None else [value])

    def test_tools_thinking_and_history_survive_preparation(self):
        tool = {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}
        body = self.chat(tools=[tool], tool_choice={"type": "function", "function": {"name": "lookup"}},
                         reasoning_effort="high", thinking={"type": "enabled", "budget_tokens": 1024})
        body["messages"] += [{"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"q":"猫"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "结果"}]
        before = copy.deepcopy(body)
        result = prepare(validate_chat(body), "intl")
        self.assertEqual(result["messages"], before["messages"])
        self.assertEqual(result["tools"], [tool])
        self.assertEqual(result["tool_choice"], "required")
        self.assertEqual(result["reasoning_effort"], "high")
        self.assertEqual(result["thinking"], before["thinking"])
        self.assertEqual(body, before)

    def test_json_response_contract_still_rejects_invalid_output(self):
        request = self.chat(response_format={"type": "json_object"})
        for text in ("not JSON", "[]"):
            with self.subTest(text=text), self.assertRaises(Fault):
                contract({"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}, request)
        contract({"choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}]}, request)


class GatewayPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_public_apis_forward_prompt_free_requests_over_http(self):
        captured = []

        async def upstream(request):
            captured.append(await request.json())
            return web.json_response({"id": "fixture", "model": "test-model", "choices": [
                {"index": 0, "message": {"role": "assistant", "content": '{"ok":true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

        upstream_app = web.Application()
        upstream_app.router.add_post("/chat", upstream)
        async with TestServer(upstream_app) as official:
            with tempfile.TemporaryDirectory(prefix="wb-prompt-tests-") as data:
                root = Path(__file__).resolve().parents[1]
                gateway = Gateway(root, data_dir=data, config={"cn": {}, "intl": {}}, admin_key="test-admin")
                key = gateway.store.new_key({"name": "test-client"})["key"]
                model = {"id": "test-model", "supportsToolCall": True, "maxOutputTokens": 256}

                async def catalogs(scope, region, account):
                    record = {"id": "fixture-" + region, "region": region, "account": {}}
                    return [(record, [model])], []

                async def forward(region, path, record, method, body, headers, stream):
                    self.assertEqual(path, "/v2/chat/completions")
                    return await gateway.http.post(official.make_url("/chat"), json=body)

                gateway.catalogs_for = AsyncMock(side_effect=catalogs)
                gateway.refresh = AsyncMock(side_effect=lambda record: record)
                gateway.call = AsyncMock(side_effect=forward)
                schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

                async def run_cases(client):
                    for region, prefix in (("cn", "cn"), ("intl", "gl")):
                        for api in ("chat/completions", "responses", "messages"):
                            for stream in (False, True):
                                with self.subTest(region=region, api=api, stream=stream):
                                    body = {"model": prefix + "/test-model", "stream": stream}
                                    fmt = {"type": "json_schema", "name": "answer", "schema": schema}
                                    if api == "responses":
                                        body.update(input="你好", text={"format": fmt})
                                        if stream: body["instructions"] = "客户端规则"
                                    else:
                                        body["messages"] = [{"role": "user", "content": "你好"}]
                                        if api == "messages":
                                            body.update(max_tokens=64, output_config={"format": fmt})
                                            if stream: body["system"] = "客户端规则"
                                        else:
                                            body["response_format"] = {"type": "json_schema", "json_schema": {
                                                "name": "answer", "schema": schema}}
                                            if stream: body["messages"].insert(0, {"role": "system", "content": "客户端规则"})
                                    response = await client.post("/v1/" + api, json=body,
                                                                 headers={"Authorization": "Bearer " + key})
                                    text = await response.text()
                                    self.assertEqual(response.status, 200, text)
                                    self.assertIn("ok", text)
                                    self.assertEqual("text/event-stream" in response.headers["Content-Type"], stream)
                                    sent = captured[-1]
                                    user_content = [{"type": "text", "text": "你好"}] if api == "messages" else "你好"
                                    self.assertEqual(sent["messages"], ([{"role": "system", "content": "客户端规则"}] if stream else [])
                                                     + [{"role": "user", "content": user_content}])
                                    self.assertEqual(sent["response_format"]["json_schema"]["schema"], schema)
                    self.assertEqual(len(captured), 12)

                async with TestClient(TestServer(create_app(gateway))) as client:
                    await asyncio.wait_for(run_cases(client), timeout=30)


if __name__ == "__main__":
    unittest.main()
