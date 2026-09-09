"""Live agent stream (CoT + tool events + typewriter) regression tests.

Offline: no provider keys, no device, no network. The ReAct loop is exercised
with a faked ``complete()``, exactly as in ``test_agent.py``.
"""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core import planner
from core.schemas import AskRequest, ToolResultRequest
from core.streaming import (
    EVENT_ANSWER_END,
    EVENT_DONE,
    EVENT_LOG,
    EVENT_OBSERVATION,
    EVENT_SELF_CORRECTION,
    EVENT_STATE,
    EVENT_THOUGHT,
    EVENT_THINKING,
    EVENT_TOKEN,
    EVENT_TOOL_CALL,
    AgentEvent,
    AgentStreamer,
    TOOL_RESULT_BRIDGE,
    typewriter_chunks,
)


def context(memory_engine=None):
    pm = Mock()
    pm.active_name.return_value = "groq"
    pm.model_for.return_value = "llama-3.3-70b-versatile"
    state = Mock()
    # _record_provider_latency reads the current state snapshot back out.
    state.get.return_value = {"state": "thinking"}
    return SimpleNamespace(
        log=Mock(), state=state, provider_manager=pm,
        stats=SimpleNamespace(bump=AsyncMock(), record_tool_usage=AsyncMock()),
        memory_engine=memory_engine or Mock(search_relevant=AsyncMock(return_value=[])),
    )


def completion(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def tool_completion(name: str, arguments: dict, call_id: str = "call_1", content=None) -> dict:
    return {"choices": [{"message": {
        "role": "assistant", "content": content,
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)}}],
    }, "finish_reason": "tool_calls"}]}


def frames_of(recorder, event_type: str) -> list[dict]:
    return [event.data for event in recorder if event.type == event_type]


def parse_sse(text: str) -> list[tuple[str | None, dict]]:
    """Minimal SSE client: the same framing the Kotlin Body implements."""
    out: list[tuple[str | None, dict]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        name, payload = None, None
        for line in block.split("\n"):
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                payload = line[5:].strip()
        if payload:
            out.append((name, json.loads(payload)))
    return out


class FrameEncodingTests(unittest.TestCase):
    def test_event_encodes_as_sse_frame(self):
        frame = AgentEvent("tool_call", {"tool": "open_app"}, seq=7, ts=1.5).encode()
        self.assertTrue(frame.startswith("id: 7\nevent: tool_call\ndata: "))
        self.assertTrue(frame.endswith("\n\n"))
        data = json.loads(frame.split("data: ", 1)[1].rsplit("\n\n", 1)[0])
        self.assertEqual(data["type"], "tool_call")
        self.assertEqual(data["seq"], 7)
        self.assertEqual(data["tool"], "open_app")

    def test_typewriter_chunks_reassemble_and_stay_bounded(self):
        text = "Done, Boss. YouTube is open and playing now."
        chunks = typewriter_chunks(text)
        self.assertGreater(len(chunks), 4, "a single frame is not a typing effect")
        self.assertEqual("".join(part for part, _ in chunks), text)
        self.assertLessEqual(sum(delay for _, delay in chunks), 5.5)
        # Very long answers must not dribble in forever.
        long = typewriter_chunks("x" * 5000)
        self.assertLessEqual(sum(delay for _, delay in long), 5.5)
        self.assertEqual(typewriter_chunks(""), [])


class StreamedTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_brain_tool_turn_emits_cot_tool_and_typewriter(self):
        """thinking -> tool_call -> observation -> token* -> done on one stream."""
        recorder: list[AgentEvent] = []
        streamer = AgentStreamer(recorder=recorder, pace=0.0)
        answer = "Saved, Boss. I noted that you prefer dark mode."
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "get_available_tools", return_value=[
                 {"type": "function", "function": {"name": "save_memory", "description": "m",
                                                   "parameters": {"type": "object", "properties": {}}}}]), \
             patch.object(planner, "execute_tool", return_value='{"saved": true}'), \
             patch.object(planner, "RATE_LIMIT_PAUSE_SECONDS", 0.0), \
             patch.object(planner, "complete",
                          side_effect=[tool_completion("save_memory", {"category": "personal",
                                                                       "title": "Dark mode",
                                                                       "content": "Boss prefers dark mode"}),
                                       completion(answer)]):
            result = await planner.plan_request(AskRequest(message="remember I prefer dark mode"),
                                                context(), stream=streamer)

        self.assertEqual(result.response, answer)
        self.assertEqual(result.route, "agent_final")

        types = [event.type for event in recorder]
        for expected in (EVENT_THINKING, EVENT_TOOL_CALL, EVENT_OBSERVATION, EVENT_TOKEN, EVENT_DONE):
            self.assertIn(expected, types, f"missing {expected} in {types}")

        call = frames_of(recorder, EVENT_TOOL_CALL)[0]
        self.assertEqual(call["tool"], "save_memory")
        self.assertFalse(call["device"])
        self.assertEqual(call["args"]["title"], "Dark mode")

        observation = frames_of(recorder, EVENT_OBSERVATION)[0]
        self.assertTrue(observation["ok"])
        self.assertIn("saved", observation["result"])

        tokens = frames_of(recorder, EVENT_TOKEN)
        self.assertEqual("".join(chunk["text"] for chunk in tokens), answer)
        self.assertEqual([chunk["i"] for chunk in tokens], list(range(len(tokens))))
        self.assertEqual(frames_of(recorder, EVENT_DONE)[0]["response"], answer)
        self.assertEqual(frames_of(recorder, EVENT_DONE)[0]["route"], "agent_final")
        self.assertEqual(frames_of(recorder, EVENT_ANSWER_END)[0]["chars"], len(answer))
        # Sequence numbers are monotonic so a Body can detect a gap.
        self.assertEqual([event.seq for event in recorder], sorted(event.seq for event in recorder))

    async def test_failure_then_self_correction_is_visible(self):
        """A failing tool must surface as observation(ok=false) + self_correction."""
        recorder: list[AgentEvent] = []
        streamer = AgentStreamer(recorder=recorder, pace=0.0)
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "get_available_tools", return_value=[
                 {"type": "function", "function": {"name": "run_termux_command", "description": "t",
                                                   "parameters": {"type": "object", "properties": {}}}}]), \
             patch.object(planner, "execute_tool",
                          return_value=json.dumps({"error": "command not found: foo"})), \
             patch.object(planner, "RATE_LIMIT_PAUSE_SECONDS", 0.0), \
             patch.object(planner, "complete",
                          side_effect=[tool_completion("run_termux_command", {"command": "foo"}),
                                       completion("That tool is broken, Boss. I will use another way.")]):
            await planner.plan_request(AskRequest(message="run foo"), context(), stream=streamer)

        observation = frames_of(recorder, EVENT_OBSERVATION)[0]
        self.assertFalse(observation["ok"])
        correction = frames_of(recorder, EVENT_SELF_CORRECTION)[0]
        self.assertEqual(correction["tool"], "run_termux_command")
        self.assertIn("command not found", correction["reason"])
        # ``reflexion`` is the semantic alias the Body may render instead.
        self.assertEqual(correction["reflexion"], correction["reason"])
        self.assertIn("another way", frames_of(recorder, EVENT_DONE)[0]["response"])

    async def test_log_and_state_are_mirrored_onto_the_stream(self):
        """Everything ActionLog/StateManager record for this turn reaches the Body."""
        recorder: list[AgentEvent] = []
        streamer = AgentStreamer(recorder=recorder, pace=0.0)
        ctx = context()
        from core.action_log import ActionLog
        from core.state_manager import StateManager
        ctx.log, ctx.state = ActionLog(), StateManager()
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "get_available_tools", return_value=[]), \
             patch.object(planner, "complete", return_value=completion("ok Boss")):
            await planner.plan_request(AskRequest(message="hello"), ctx, stream=streamer)

        logs = frames_of(recorder, EVENT_LOG)
        self.assertTrue(any("Received request" in item["text"] for item in logs))
        states = frames_of(recorder, EVENT_STATE)
        self.assertIn("thinking", [item["state"] for item in states])
        self.assertEqual(states[-1]["state"], "idle")

    async def test_final_answer_is_streamed_chunk_by_chunk(self):
        """The typing effect: the answer arrives as many ordered token frames."""
        recorder: list[AgentEvent] = []
        streamer = AgentStreamer(recorder=recorder, pace=0.02)  # compressed cadence
        answer = ("Done, Boss. YouTube is open, the video is queued and I verified "
                  "the player state with a screen read before replying.")
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "get_available_tools", return_value=[]), \
             patch.object(planner, "complete", return_value=completion(answer)):
            await planner.plan_request(AskRequest(message="open youtube"), context(), stream=streamer)

        tokens = frames_of(recorder, EVENT_TOKEN)
        self.assertGreater(len(tokens), 6, "answer must arrive in chunks, not one blob")
        self.assertEqual("".join(chunk["text"] for chunk in tokens), answer)
        self.assertTrue(all(chunk["text"] for chunk in tokens), "empty token frames are noise")
        end = frames_of(recorder, EVENT_ANSWER_END)[0]
        self.assertEqual(end["chars"], len(answer))

    async def test_no_stream_means_zero_events(self):
        """The legacy single-shot contract is untouched (no recorder, no typewriter)."""
        streamer = AgentStreamer(recorder=None, pace=0.0)
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "get_available_tools", return_value=[]), \
             patch.object(planner, "complete", return_value=completion("plain answer")):
            result = await planner.plan_request(AskRequest(message="hello"), context())
            self.assertEqual(result.response, "plain answer")
            self.assertEqual(streamer.emitted, 0)


class DeviceDispatchInStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_device_tool_is_awaited_inside_the_stream(self):
        """tool_call(device) -> Body answers /agent/result -> observation -> final."""
        recorder: list[AgentEvent] = []
        streamer = AgentStreamer(recorder=recorder, pace=0.0)
        session = "s-stream-device"
        answer = "YouTube is open and playing, Boss."
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "RATE_LIMIT_PAUSE_SECONDS", 0.0), \
             patch.object(planner, "complete",
                          side_effect=[tool_completion("open_app", {"package": "com.google.android.youtube"},
                                                       content="Opening YouTube now."),
                                       completion(answer)]):
            turn = asyncio.create_task(planner.plan_request(
                AskRequest(message="open YouTube", session_id=session), context(), stream=streamer))
            # The Body sees the dispatch on the open stream, executes, calls back.
            dispatched = None
            for _ in range(2000):
                await asyncio.sleep(0.001)
                calls = frames_of(recorder, EVENT_TOOL_CALL)
                if calls:
                    dispatched = calls[0]
                    break
            self.assertIsNotNone(dispatched, "no tool_call frame reached the Body")
            self.assertTrue(dispatched["device"])
            self.assertEqual(dispatched["action"]["tool"], "open_app")
            self.assertEqual(dispatched["action"]["args"]["package"], "com.google.android.youtube")
            self.assertEqual(dispatched["thought"], "Opening YouTube now.")
            self.assertTrue(TOOL_RESULT_BRIDGE.resolve(session, {
                "tool": "open_app", "result": "App launched: YouTube", "success": True}))
            result = await asyncio.wait_for(turn, timeout=5)

        self.assertEqual(result.response, answer)
        self.assertFalse(result.needs_tool_result)
        observation = frames_of(recorder, EVENT_OBSERVATION)[0]
        self.assertTrue(observation["ok"])
        self.assertIn("App launched: YouTube", observation["result"])
        self.assertEqual(frames_of(recorder, EVENT_TOKEN)[0]["text"], answer)
        self.assertIsNone(planner._load_agent_session(session), "resume session must be cleared")

    async def test_failed_device_action_triggers_visible_self_healing(self):
        recorder: list[AgentEvent] = []
        streamer = AgentStreamer(recorder=recorder, pace=0.0)
        session = "s-stream-device-fail"
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "RATE_LIMIT_PAUSE_SECONDS", 0.0), \
             patch.object(planner, "complete",
                          side_effect=[tool_completion("click_node", {"text": "Subscribe"}),
                                       completion("Tried again a different way, Boss.")]):
            turn = asyncio.create_task(planner.plan_request(
                AskRequest(message="click subscribe", session_id=session), context(), stream=streamer))
            for _ in range(2000):
                await asyncio.sleep(0.001)
                if frames_of(recorder, EVENT_TOOL_CALL):
                    break
            TOOL_RESULT_BRIDGE.resolve(session, {"tool": "click_node", "result": "node not found",
                                                 "success": False})
            await asyncio.wait_for(turn, timeout=5)

        self.assertFalse(frames_of(recorder, EVENT_OBSERVATION)[0]["ok"])
        correction = frames_of(recorder, EVENT_SELF_CORRECTION)[0]
        self.assertEqual(correction["tool"], "click_node")
        self.assertTrue(correction["strategy"])

    async def test_missing_body_callback_times_out_without_hanging(self):
        """No callback ever: observe the timeout, self-correct, still answer."""
        recorder: list[AgentEvent] = []
        streamer = AgentStreamer(recorder=recorder, pace=0.0)
        session = "s-stream-timeout"
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "RATE_LIMIT_PAUSE_SECONDS", 0.0), \
             patch.object(planner, "TOOL_RESULT_TIMEOUT_SECONDS", 0.05), \
             patch.object(planner, "complete",
                          side_effect=[tool_completion("read_screen", {}),
                                       completion("Screen read timed out, Boss.")]):
            result = await asyncio.wait_for(
                planner.plan_request(AskRequest(message="what is on screen", session_id=session),
                                      context(), stream=streamer), timeout=5)

        self.assertEqual(result.response, "Screen read timed out, Boss.")
        self.assertIn("TIMEOUT", frames_of(recorder, EVENT_OBSERVATION)[0]["result"])
        self.assertEqual(frames_of(recorder, EVENT_SELF_CORRECTION)[0]["tool"], "read_screen")
        self.assertIsNone(planner._load_agent_session(session), "a late callback must not resume")

    async def test_legacy_body_still_gets_pending_action(self):
        """Without a stream the dispatch returns the old needs_tool_result payload."""
        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "complete",
                          return_value=tool_completion("open_app", {"package": "com.x"})):
            result = await planner.plan_request(AskRequest(message="open x", session_id="s-legacy"),
                                                context())
        self.assertTrue(result.needs_tool_result)
        self.assertEqual(result.route, "agent_action")
        self.assertEqual(result.action.tool, "open_app")
        self.assertEqual(result.response, "")


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def test_resolve_only_once(self):
        self.assertFalse(TOOL_RESULT_BRIDGE.resolve("nobody-waits", {"result": "x"}))

    async def test_register_resolve_release(self):
        loop = asyncio.get_running_loop()
        future = TOOL_RESULT_BRIDGE.register("s-1")
        self.assertEqual(TOOL_RESULT_BRIDGE.pending(), ["s-1"])
        self.assertTrue(TOOL_RESULT_BRIDGE.resolve("s-1", {"success": True, "result": "ok"}))
        payload = await asyncio.wait_for(future, timeout=1)
        self.assertEqual(payload["result"], "ok")
        self.assertFalse(TOOL_RESULT_BRIDGE.resolve("s-1", {"success": True}))
        TOOL_RESULT_BRIDGE.release("s-1")
        self.assertEqual(TOOL_RESULT_BRIDGE.pending(), [])

    async def test_endpoint_acks_when_stream_owns_the_loop(self):
        import main
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        TOOL_RESULT_BRIDGE._waiters["s-ack"] = future
        with patch.object(main, "continue_with_tool_result", AsyncMock()) as resume:
            ack = await main.agent_result(ToolResultRequest(session_id="s-ack", tool="open_app",
                                                            result="launched", success=True))
        self.assertTrue(ack.streamed)
        self.assertEqual(ack.response, "")
        resume.assert_not_awaited()
        self.assertEqual((await asyncio.wait_for(future, timeout=1))["tool"], "open_app")

    async def test_endpoint_falls_back_to_resume_without_stream(self):
        import main
        with patch.object(main, "continue_with_tool_result",
                          AsyncMock(return_value=planner.AskResponse(response="done",
                                                                     route="agent_final"))) as resume:
            answer = await main.agent_result(ToolResultRequest(session_id="s-no-stream", tool="open_app",
                                                               result="launched", success=True))
        self.assertEqual(answer.response, "done")
        self.assertFalse(answer.streamed)
        resume.assert_awaited_once()


class ProviderDeltaTests(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_deltas_stream_as_thought_events(self):
        """``on_delta`` from the provider is republished as ``thought`` frames."""
        recorder: list[AgentEvent] = []
        streamer = AgentStreamer(recorder=recorder, pace=0.0)
        forward = streamer.delta_forwarder(1)
        for piece in ("I should ", "open the app ", "first."):
            forward(piece)
        streamer.flush_delta(1, text="I should open the app first.")
        thoughts = frames_of(recorder, EVENT_THOUGHT)
        self.assertTrue(thoughts)
        streamed_text = "".join(item["delta"] for item in thoughts if not item["final"])
        self.assertEqual("I should open the app first.".startswith(streamed_text), True)
        final = thoughts[-1]
        self.assertTrue(final["final"])
        self.assertEqual(final["stream"], "t1")
        # The authoritative line text replaces whatever partial deltas arrived.
        self.assertEqual(final["text"], "I should open the app first.")
        self.assertEqual(final["delta"], "")
        self.assertEqual(thoughts[0]["step"], 1)

    async def test_thought_fallback_flushes_remainder_without_authoritative_text(self):
        recorder: list[AgentEvent] = []
        streamer = AgentStreamer(recorder=recorder, pace=0.0)
        forward = streamer.delta_forwarder(2)
        forward("partial reasoning ")
        forward("still pending")
        streamer.flush_delta(2)
        thoughts = frames_of(recorder, EVENT_THOUGHT)
        self.assertEqual("".join(item["delta"] for item in thoughts),
                         "partial reasoning still pending")
        self.assertTrue(thoughts[-1]["final"])
        self.assertNotIn("text", thoughts[-1])

    async def test_provider_failover_is_reported_as_self_correction(self):
        recorder: list[AgentEvent] = []
        streamer = AgentStreamer(recorder=recorder, pace=0.0)
        seen: dict = {}

        def fake_complete(*args, **kwargs):
            # Emulate the transport: first provider fails, second answers.
            kwargs["on_provider"]("groq", False)
            kwargs["on_provider"]("openai", True)
            seen["on_delta"] = kwargs.get("on_delta")
            return completion("Recovered from failover, Boss.")

        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "get_available_tools", return_value=[]), \
             patch.object(planner, "complete", side_effect=fake_complete):
            async def runner():
                await planner.plan_request(AskRequest(message="hi"), context(), stream=streamer)
            await runner()

        correction = frames_of(recorder, EVENT_SELF_CORRECTION)[0]
        self.assertIn("groq", correction["reason"])
        self.assertEqual(correction["strategy"], "failing over to the next configured provider")
        # A stream bound means the planner asks the provider for deltas.
        self.assertTrue(seen["on_delta"])

    async def test_no_stream_means_no_delta_request(self):
        seen: dict = {}

        def fake_complete(*args, **kwargs):
            seen["on_delta"] = kwargs.get("on_delta", "missing")
            return completion("ok")

        with patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "get_available_tools", return_value=[]), \
             patch.object(planner, "complete", side_effect=fake_complete):
            await planner.plan_request(AskRequest(message="hi"), context())
        self.assertIsNone(seen["on_delta"])


class StreamingTransportTests(unittest.TestCase):
    def test_delta_folding_reassembles_fragmented_tool_calls(self):
        from core import llm_handler as h

        def delta_chunk(body, finish=None):
            return {"choices": [{"delta": body, "finish_reason": finish}]}

        state = h.new_stream_state()
        chunks = [
            delta_chunk({"role": "assistant", "content": "Let me "}),
            delta_chunk({"content": "open it."}),
            # A tool call can arrive split across frames, id/name/args included.
            delta_chunk({"tool_calls": [{"index": 0, "id": "call_9",
                                         "function": {"name": "open_",
                                                      "arguments": '{"pack'}}]}),
            delta_chunk({"tool_calls": [{"index": 0,
                                         "function": {"name": "app",
                                                      "arguments": 'age": "com.x"}'}}]},
                        finish="tool_calls"),
        ]
        deltas = [h.fold_openai_delta(state, chunk)[0] for chunk in chunks]
        self.assertEqual(deltas[:2], ["Let me ", "open it."])
        built = h.build_completion(state)
        self.assertEqual(built["streamed"], True)
        message = built["choices"][0]["message"]
        calls = h.extract_tool_calls(message)
        self.assertEqual(calls[0]["function"]["name"], "open_app")
        self.assertEqual(calls[0]["function"]["arguments"], {"package": "com.x"})
        self.assertEqual(calls[0]["id"], "call_9")

    def test_streaming_unavailable_falls_back_to_buffered_call(self):
        """A provider that rejects ``stream`` must still answer the agent."""
        from core import llm_handler as h
        buffered = {"choices": [{"message": {"role": "assistant", "content": "fallback worked"}}]}
        provider = {"provider": "groq", "api_key": "k", "model": "m"}
        with patch.object(h, "_stream_post", side_effect=h.StreamingUnavailable("nope")), \
             patch.object(h, "_post", return_value=buffered) as post:
            out = h.complete("hi", [], [provider], on_delta=lambda piece: None)
        self.assertEqual(out["choices"][0]["message"]["content"], "fallback worked")
        self.assertNotIn("streamed", out, "fallback path is the plain provider payload")
        post.assert_called_once()

    def test_sse_payload_iterator_skips_comments_and_keeps_done(self):
        from core import llm_handler as h

        class FakeResponse:
            def iter_lines(self, decode_unicode=True):
                return iter([': keep-alive', 'data: {"a":1}', '', 'data: [DONE]', 'data: {"b":2}'])

        self.assertEqual(list(h.iter_sse_payloads(FakeResponse())), ['{"a":1}'])


class EndpointNegotiationTests(unittest.IsolatedAsyncioTestCase):
    async def _post(self, client, payload, headers=None):
        return await client.post("/ask_ronin", json=payload, headers=headers or {})

    async def test_accept_header_selects_sse_and_legacy_json_is_default(self):
        import httpx
        import main
        answer = "Streaming works, Boss."
        real_streamer = AgentStreamer

        def fast_streamer(**kwargs):
            kwargs.setdefault("pace", 0.0)
            return real_streamer(**kwargs)

        ctx = context()
        ctx.log, ctx.state = main.ACTION_LOG, main.STATE
        with patch.object(main, "CTX", ctx), \
             patch.object(planner, "AgentStreamer", fast_streamer), \
             patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "get_available_tools", return_value=[]), \
             patch.object(planner, "complete", return_value=completion(answer)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                        base_url="http://test") as client:
                legacy = await self._post(client, {"message": "hello"})
                streamed = await self._post(client, {"message": "hello"},
                                            {"Accept": "text/event-stream"})
                forced = await self._post(client, {"message": "hello", "stream": True})
                refused = await self._post(client, {"message": "hello", "stream": False},
                                          {"Accept": "text/event-stream"})
                protocol = await client.get("/agent/stream/protocol")

        # Default: the original monolithic JSON contract.
        self.assertEqual(legacy.headers["content-type"].split(";")[0], "application/json")
        self.assertEqual(legacy.json()["response"], answer)
        self.assertFalse(legacy.json()["streamed"])
        for response in (streamed, forced):
            self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            self.assertEqual(response.headers.get("x-accel-buffering"), "no")
            events = parse_sse(response.text)
            names = [name for name, _ in events]
            data = [payload for _, payload in events]
            for expected in ("start", "thinking", "token", "done"):
                self.assertIn(expected, names)
            self.assertEqual("".join(item["text"] for item in data if item["type"] == "token"), answer)
            done = [item for item in data if item["type"] == "done"][0]
            self.assertEqual(done["response"], answer)
            self.assertTrue(done["streamed"])
            self.assertEqual([item["seq"] for item in data], sorted(item["seq"] for item in data))
            # Every frame's `event:` line must agree with its payload `type`.
            for name, payload in events:
                self.assertEqual(name, payload["type"])
        self.assertEqual(refused.headers["content-type"].split(";")[0], "application/json")
        self.assertEqual(protocol.json()["media_type"], "text/event-stream")
        self.assertIn("tool_call", protocol.json()["events"])
        self.assertIn("self_correction", protocol.json()["events"])

    async def test_stream_endpoint_reports_agent_errors_in_band(self):
        """A transport blow-up must arrive as an error frame, not a broken pipe."""
        import httpx
        import main
        ctx = context()
        ctx.log, ctx.state = main.ACTION_LOG, main.STATE
        with patch.object(main, "CTX", ctx), \
             patch.object(planner, "recent_history", AsyncMock(return_value=[])), \
             patch.object(planner, "save_conversation", AsyncMock()), \
             patch.object(planner, "search_facts", AsyncMock(return_value=[])), \
             patch.object(planner, "get_available_tools", return_value=[]), \
             patch.object(planner, "complete", side_effect=planner.LLMError("upstream exploded")):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                        base_url="http://test") as client:
                response = await client.post("/ask_ronin/stream", json={"message": "hi"})
        events = [payload for _, payload in parse_sse(response.text)]
        kinds = [item["type"] for item in events]
        self.assertIn("done", kinds)
        # The contained failure keeps the "llm unavailable" wording the Body expects.
        done = [item for item in events if item["type"] == "done"][0]
        self.assertEqual(done["error"], "llm_unavailable")
        self.assertIn("temporarily unavailable", done["response"])
        self.assertNotIn("upstream exploded", response.text)


if __name__ == "__main__":
    unittest.main()
