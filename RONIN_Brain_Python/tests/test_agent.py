"""Autonomous-agent regression tests; offline, no provider keys needed."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core import planner
from core.llm_handler import extract_tool_calls, parse_tool_tag_calls, strip_tool_tags
from core.schemas import AskRequest, ToolResultRequest
from core.tools_catalog import DEVICE_TOOLS, device_tool_schemas, is_device_tool


def context(memory_engine=None):
    pm = Mock()
    pm.active_name.return_value = None
    pm.model_for.return_value = None
    return SimpleNamespace(log=Mock(), state=Mock(), provider_manager=pm,
                           stats=SimpleNamespace(bump=AsyncMock(), record_tool_usage=AsyncMock()),
                           memory_engine=memory_engine or Mock(search_relevant=AsyncMock(return_value=[])))


class ToolProtocolTests(unittest.TestCase):
    def test_tool_tag_parsing(self):
        text = '<tool>{"tool": "open_app", "args": {"package": "com.google.android.youtube"}}</tool>'
        calls = parse_tool_tag_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "open_app")
        self.assertEqual(calls[0]["function"]["arguments"]["package"], "com.google.android.youtube")

    def test_bare_json_parsing(self):
        calls = parse_tool_tag_calls('{"tool": "read_screen", "args": {}}')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "read_screen")

    def test_native_tool_calls_normalized(self):
        message = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "1", "type": "function",
             "function": {"name": "click_xy", "arguments": '{"x": 100, "y": 200}'}}]}
        calls = extract_tool_calls(message)
        self.assertEqual(calls[0]["function"]["arguments"], {"x": 100, "y": 200})

    def test_strip_tool_tags_for_tts(self):
        text = '<tool>{"tool": "open_app", "args": {}}</tool> Done, Boss.'
        self.assertEqual(strip_tool_tags(text), "Done, Boss.")

    def test_device_tool_registry(self):
        self.assertTrue(is_device_tool("open_app"))
        self.assertTrue(is_device_tool("read_screen"))
        self.assertTrue(is_device_tool("click_xy"))
        self.assertFalse(is_device_tool("save_memory"))
        self.assertFalse(is_device_tool("search"))
        schemas = device_tool_schemas()
        names = {schema["function"]["name"] for schema in schemas}
        self.assertEqual(names, set(DEVICE_TOOLS))


class MemoryToolTests(unittest.TestCase):
    def test_save_and_retrieve_memory_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["RONIN_DB_PATH"] = str(Path(tmp) / "test.db")
            try:
                from tools import agent_memory
                saved = agent_memory.save_memory("personal", "Dark mode", "Boss prefers dark mode everywhere.")
                self.assertTrue(saved.get("saved"))
                found = agent_memory.retrieve_memory("dark mode preference")
                self.assertEqual(found["count"], 1)
                self.assertIn("dark mode", found["memories"][0]["content"].lower())
            finally:
                del os.environ["RONIN_DB_PATH"]


class TermuxToolTests(unittest.TestCase):
    def test_run_echo(self):
        from tools import termux_exec
        result = termux_exec.run_termux_command("echo hello-vyrx")
        self.assertEqual(result.get("returncode"), 0)
        self.assertIn("hello-vyrx", result.get("output", ""))

    def test_destructive_refused(self):
        from tools import termux_exec
        result = termux_exec.run_termux_command("rm -rf / --no-preserve-root")
        self.assertIn("error", result)

    def test_write_escapes_workspace_refused(self):
        from tools import termux_exec
        result = termux_exec.write_file("/tmp/evil.txt", "x")
        self.assertIn("error", result)


class ReActLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_brain_tool_loop_resolves_inline(self):
        """LLM calls save_memory (Brain tool) -> executed inline -> final speech."""
        first = {"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "save_memory",
                                         "arguments": json.dumps({"category": "personal", "title": "T", "content": "C"})}}]}}]}
        second = {"choices": [{"message": {"role": "assistant", "content": "Saved, Boss."}}]}
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "get_available_tools", return_value=[
                 {"type": "function", "function": {"name": "save_memory", "description": "m",
                                                  "parameters": {"type": "object", "properties": {}}}}]), \
             patch.object(planner, "execute_tool", return_value='{"saved": true}'), \
             patch.object(planner.asyncio, "sleep", AsyncMock()), \
             patch.object(planner, "complete", side_effect=[first, second]) as mock_complete:
            result = await planner._run_llm(AskRequest(message="remember that I like tea"), context())
            self.assertEqual(result.response, "Saved, Boss.")
            self.assertEqual(result.route, "agent_final")
            self.assertFalse(result.needs_tool_result)
            self.assertEqual(mock_complete.call_count, 2)

    async def test_device_tool_dispatch_and_callback(self):
        """LLM calls open_app -> pending action -> /agent/result continues -> final."""
        dispatch = {"choices": [{"message": {
            "role": "assistant",
            "content": '<tool>{"tool": "open_app", "args": {"package": "com.google.android.youtube"}}</tool>'}}]}
        final = {"choices": [{"message": {"role": "assistant", "content": "YouTube is open, Boss."}}]}
        ctx = context()
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner.asyncio, "sleep", AsyncMock()), \
             patch.object(planner, "complete", side_effect=[dispatch, final]):
            pending = await planner._run_llm(
                AskRequest(message="open YouTube", session_id="s-device-1"), ctx)
            self.assertTrue(pending.needs_tool_result)
            self.assertEqual(pending.route, "agent_action")
            self.assertIsNotNone(pending.action)
            self.assertEqual(pending.action.tool, "open_app")
            self.assertEqual(pending.action.args["package"], "com.google.android.youtube")

            done = await planner.continue_with_tool_result(
                ToolResultRequest(session_id="s-device-1", tool="open_app",
                                  result="App launched: YouTube", success=True,
                                  tool_call_id=pending.action.tool_call_id), ctx)
            self.assertFalse(done.needs_tool_result)
            self.assertEqual(done.route, "agent_final")
            self.assertIn("YouTube", done.response)

    async def test_callback_without_session_errors_cleanly(self):
        result = await planner.continue_with_tool_result(
            ToolResultRequest(session_id="no-such-session", tool="open_app", result="x"), context())
        self.assertEqual(result.error, "no_pending_action")

    async def test_memory_injected_into_prompt(self):
        seen: dict = {}

        def fake_complete(message, history, providers, system_prompt=None, **kwargs):
            seen["prompt"] = system_prompt or ""
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        engine = Mock()
        engine.search_relevant = AsyncMock(return_value=[
            {"category": "personal", "title": "Time format", "content": "Boss uses 24-hour time.",
             "importance": 5}])
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "get_available_tools", return_value=[]), \
             patch.object(planner, "complete", side_effect=fake_complete):
            await planner._run_llm(AskRequest(message="what time is it"), context(engine))
            self.assertIn("24-hour time", seen.get("prompt", ""))


if __name__ == "__main__":
    unittest.main()
