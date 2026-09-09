"""Static contract checks, not a substitute for an Android build/device test."""
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "RONIN_Body_Kotlin/app/src/main"
JAVA = MAIN / "java/com/ronin/ai"
ANDROID = "{http://schemas.android.com/apk/res/android}"


class AndroidContractTests(unittest.TestCase):
    def test_manifest_components_exist(self):
        classes = set()
        for path in (MAIN / "java").rglob("*.kt"):
            text = path.read_text()
            package = re.search(r"^package ([\w.]+)", text).group(1)
            classes.update(package + "." + name for name in re.findall(r"\bclass (\w+)", text))
        manifest = ET.parse(MAIN / "AndroidManifest.xml")
        for element in manifest.iter():
            if element.tag not in {"application", "activity", "service", "receiver", "provider"}:
                continue
            name = element.get(ANDROID + "name")
            if not name or name.startswith("androidx."):
                continue
            if name.startswith("."):
                name = "com.ronin.ai" + name
            self.assertIn(name, classes)

    def test_manifest_file_resources_exist(self):
        manifest = ET.parse(MAIN / "AndroidManifest.xml")
        for element in manifest.iter():
            for value in element.attrib.values():
                if value.startswith(("@xml/", "@drawable/", "@mipmap/")):
                    self.assertTrue((MAIN / "res" / (value[1:] + ".xml")).exists(), value)
        for path in (MAIN / "res").rglob("*.xml"):
            ET.parse(path)

    def test_tool_preferences_reach_request_serialization(self):
        java = MAIN / "java/com/ronin/ai"
        self.assertIn("settingsRepo.setToolEnabled(id, it)", (java / "ui/tools/ToolsScreen.kt").read_text())
        self.assertIn("toolsEnabled = s.toolsEnabled", (java / "ui/chat/ChatController.kt").read_text())
        self.assertIn('"tools_enabled"', (java / "network/ApiClient.kt").read_text())


class RealtimeStreamContractTests(unittest.TestCase):
    """The Body must speak exactly the protocol the Brain streams.

    These are the parts a Gradle build cannot catch (the two languages drift
    silently): SSE frame shape, event vocabulary, and the endpoints the
    typewriter/terminal UI depends on.
    """

    @staticmethod
    def _read(relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_brain_streams_the_documented_events(self):
        from core.streaming import (
            EVENT_DONE, EVENT_OBSERVATION, EVENT_SELF_CORRECTION, EVENT_THINKING,
            EVENT_THOUGHT, EVENT_TOKEN, EVENT_TOOL_CALL, STREAM_EVENT_TYPES,
        )

        planner_src = self._read("RONIN_Brain_Python/core/planner.py")
        streaming_src = self._read("RONIN_Brain_Python/core/streaming.py")
        for constant in (EVENT_THINKING, EVENT_TOOL_CALL, EVENT_OBSERVATION,
                         EVENT_SELF_CORRECTION, EVENT_DONE):
            self.assertIn(constant, planner_src, f"planner never emits {constant}")
        for constant in (EVENT_TOKEN, EVENT_THOUGHT):
            self.assertIn(constant, streaming_src, f"stream layer never emits {constant}")
        # The loop must drive both: chunked answer + live provider reasoning deltas.
        self.assertIn("await stream.stream_answer(", planner_src)
        self.assertIn("delta_forwarder(", planner_src)
        self.assertIn("text/event-stream", streaming_src)
        self.assertIn("StreamingResponse", self._read("RONIN_Brain_Python/main.py"))
        self.assertIn("plan_request_stream", self._read("RONIN_Brain_Python/main.py"))
        # Legacy contract must stay reachable for old bodies/clients.
        self.assertIn('plan_request(request, CTX)', self._read("RONIN_Brain_Python/main.py"))

    def test_kotlin_decodes_exactly_the_brain_vocabulary(self):
        from core.streaming import STREAM_EVENT_TYPES

        codec = self._read("RONIN_Body_Kotlin/app/src/main/java/com/ronin/ai/network/AgentStream.kt")
        decoded = set(re.findall(r'^\s+"([a-z_]+)"[^\n]*->', codec, re.M))
        self.assertTrue(decoded, "no SSE event types decoded in AgentStream.kt")
        unknown = decoded - set(STREAM_EVENT_TYPES)
        self.assertFalse(unknown, f"Body decodes events the Brain never emits: {sorted(unknown)}")
        for required in ("thinking", "thought", "tool_call", "observation", "self_correction",
                         "token", "done", "log", "state", "error"):
            self.assertIn(required, decoded, f"Body ignores the {required} frame")

    def test_body_consumes_stream_and_types_instead_of_blocking(self):
        client = self._read("RONIN_Body_Kotlin/app/src/main/java/com/ronin/ai/network/ApiClient.kt")
        self.assertIn("text/event-stream", client)
        self.assertIn('line.startsWith("data:")', client, "SSE data frames are not parsed")
        self.assertIn('line.startsWith("event:")', client, "SSE event names are not parsed")
        self.assertIn("fun askStream(", client)
        self.assertIn("Flow<AgentEvent>", client)
        # A stream must not inherit the 45s JSON read timeout.
        self.assertRegex(client, r"streamClient[\s\S]{0,200}readTimeout")

        controller = self._read("RONIN_Body_Kotlin/app/src/main/java/com/ronin/ai/ui/chat/ChatController.kt")
        self.assertIn("ApiClient.askStream(", controller)
        for branch in ("is AgentEvent.Token", "is AgentEvent.ToolCall", "is AgentEvent.Observation",
                       "is AgentEvent.SelfCorrection", "is AgentEvent.Done", "is AgentEvent.Thought"):
            self.assertIn(branch, controller, f"controller ignores {branch}")
        # Typewriter: a bounded reveal cadence, not an instant paste.
        self.assertIn("TICK_MS", controller)
        self.assertIn("answerTarget", controller)
        self.assertIn("revealTick()", controller)
        # Device tools still round-trip through /agent/result.
        self.assertIn("ApiClient.submitToolResult(", controller)
        self.assertIn("CommandExecutor.executeAction(", controller)

    def test_thought_terminal_is_monospaced_and_collapsible(self):
        terminal = self._read("RONIN_Body_Kotlin/app/src/main/java/com/ronin/ai/ui/chat/ThoughtTerminal.kt")
        self.assertIn("FontFamily.Monospace", terminal)
        self.assertIn("onToggle", terminal, "terminal block must be collapsible")
        self.assertIn("THOUGHT_PROCESS.LOG", terminal)
        self.assertIn("BlockCursor", terminal, "live loop should show a blinking cursor")
        bubble = self._read("RONIN_Body_Kotlin/app/src/main/java/com/ronin/ai/ui/chat/ChatComponents.kt")
        self.assertIn("ThoughtTerminal(", bubble, "chat bubble never renders the terminal block")
        self.assertIn("typedAnswer(", bubble, "chat bubble never renders the typing effect")


if __name__ == "__main__":
    unittest.main()

