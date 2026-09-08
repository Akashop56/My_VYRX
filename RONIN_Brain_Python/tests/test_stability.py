"""Offline regression tests; no provider keys, Android device or live DB needed."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from core import planner
from core.schemas import AskRequest
from tools.system_control import command_for_request


def context():
    pm = Mock()
    pm.active_name.return_value = None
    return SimpleNamespace(log=Mock(), state=Mock(), provider_manager=pm,
                           stats=SimpleNamespace(bump=AsyncMock(), record_tool_usage=AsyncMock()))


class StabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_failure_then_recovery_over_http(self):
        import main
        ctx = context()
        success = {"choices": [{"message": {"content": "Recovered"}}]}
        with patch.object(main, "CTX", ctx), \
             patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "get_available_tools", return_value=[]), \
             patch.object(planner, "complete", side_effect=[planner.LLMError("secret upstream details"), success]):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                failed = await client.post("/ask_ronin", json={"message": "Hello"})
                self.assertEqual(failed.status_code, 200)
                self.assertEqual(failed.json()["error"], "llm_unavailable")
                self.assertNotIn("secret", failed.text)
                self.assertNotIn(unittest.mock.call("tasks_completed"), ctx.stats.bump.call_args_list)
                recovered = await client.post("/ask_ronin", json={"message": "Hello again"})
                self.assertEqual(recovered.status_code, 200)
                self.assertEqual(recovered.json()["response"], "Recovered")
                ctx.stats.bump.assert_awaited_once_with("tasks_completed")

    async def test_disabled_app_control_does_not_prepare_command(self):
        result = await planner._run_android_command(
            AskRequest(message="open YouTube", tools_enabled={"app_control": False}), context())
        self.assertIsNone(result.command)
        self.assertIn("disabled", result.response)

    async def test_tool_result_provider_failure_is_contained(self):
        completion = {"choices": [{"message": {"tool_calls": [
            {"id": "1", "function": {"name": "web_search", "arguments": "{}"}}
        ]}}]}
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "_filter_tools", return_value=[{"type": "function"}]), \
             patch.object(planner, "get_available_tools", return_value=[]), \
             patch.object(planner, "execute_tool", return_value='{"result":"ok"}'), \
             patch.object(planner.asyncio, "sleep", AsyncMock()), \
             patch.object(planner, "complete", side_effect=[completion, planner.LLMError("failed")]):
            result = await planner._run_llm(AskRequest(message="Hello"), context())
            self.assertEqual(result.error, "llm_unavailable")

    def test_launch_commands(self):
        for text, target in [("open YouTube", "YouTube"), ("launch app com.android.chrome", "com.android.chrome")]:
            command = command_for_request(text)
            self.assertEqual(command.action, "open_app")
            self.assertEqual(command.text, target)

    def test_disabled_tools_are_removed(self):
        tools = [{"type": "function", "function": {"name": "web_search"}}]
        self.assertEqual(planner._filter_tools(tools, {"web_search": False}), [])


if __name__ == "__main__":
    unittest.main()
