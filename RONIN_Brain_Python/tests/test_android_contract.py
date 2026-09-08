"""Static contract checks, not a substitute for an Android build/device test."""
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "RONIN_Body_Kotlin/app/src/main"
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


if __name__ == "__main__":
    unittest.main()
