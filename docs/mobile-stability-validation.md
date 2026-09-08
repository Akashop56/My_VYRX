# Mobile UX and stability changes

## Scope
- Home: centered orb and stacked full-width model/summary cards. The page scrolls, with a bounded chat panel so its lazy message list never receives infinite height constraints.
- Tools and Settings: full-width vertical cards; horizontally scrollable tool categories; wrapping descriptions.
- Dashboard: stacked health/activity tiles, 12dp spacing, larger activity labels and wrapping text.
- The existing Settings/Tools persistence and `tools_enabled` JSON map contract are preserved. The application now owns one encrypted repository shared with voice input. Plaintext fallback was removed: encryption initialization failures are not silently downgraded. Devices with broken Keystore storage may need app data reset; old plaintext fallback preferences are not migrated.
- Backend tool filtering honors catalog IDs as well as the existing `search` / `command_for_request` aliases.
- Mic permission is awaited before starting a one-utterance microphone foreground service. Final recognition results return on the main thread to `ChatController.send(..., fromVoice = true)`. Errors are surfaced, and leaving the screen stops the service/recognizer.
- Explicit `open <app>` / `launch app <package>` commands use PackageManager launcher intents, independent of Accessibility. Missing/ambiguous apps surface an error, rather than launching a fuzzy match.
- `_run_llm` catches provider exhaustion, including after tool execution, and returns `error: llm_unavailable`. Failed requests do not increment completed tasks.
- All manifest components now resolve locally or to AndroidX; missing icon/network/device-admin resources were added. Launcher/accessibility/notification compatibility classes reuse existing implementations.

## Deliberate limitations
The remaining newly declared call, overlay, pattern, selection, boot, power, tile and device-admin components are inert compatibility stubs, not implemented features. Boot does not start a microphone, call monitor, or other restricted foreground service. Manifest declarations do not grant restricted permissions, roles, special access, or system privileges. Existing broad permission and cleartext-network declarations remain; these require a separate release/security review.

## Automated validation
From the repository root, in a virtualenv:

```sh
pip install -r RONIN_Brain_Python/tests/requirements.txt
PYTHONPATH=RONIN_Brain_Python python -m unittest discover -s RONIN_Brain_Python/tests -v
python -m compileall -q RONIN_Brain_Python/core RONIN_Brain_Python/tools
git diff --check
```

HTTP regression tests mock providers and persistence (no keys or live database). They check failure response + subsequent recovery, a failure after tool execution, disabled tools, and launch command parsing. Static Android checks cover component/resource presence and the tools request path; they are not Kotlin compilation or instrumentation tests.

## Required Android verification
The editing sandbox has no Java runtime or Android SDK, so `:app:assembleDebug` could not run. Before merging, build with JDK 17 / SDK 35:

```sh
cd RONIN_Body_Kotlin
./gradlew :app:assembleDebug :app:lintDebug
```

On an API 26 and API 35 device/emulator:
1. Cold launch using the launcher icon; visit all screens at 320dp and 360dp width and large font scale. Scroll Home to chat, show the keyboard, and verify all controls remain reachable.
2. Toggle each setting/tool, restart the app, and inspect the next `/ask_ronin` payload. Disable App Control and verify `open YouTube` returns no command.
3. Tap Mic on a fresh install: allow permission, speak, verify exactly one voice request. Repeat denying permission, with recognition unavailable, silence, cancellation, navigation away, and Voice Assistant disabled.
4. Verify the microphone notification disappears after success/error/cancellation. Confirm no recording starts on boot.
5. Open an installed app by exact label and package; try missing and ambiguous names; confirm a readable failure. Accessibility need not be enabled for launching.
6. Disable all provider keys or simulate upstream outage; confirm an error is displayed without killing the backend and a later valid request succeeds.
7. Enable Accessibility/notification access through Android settings and verify the manifest compatibility classes connect.
