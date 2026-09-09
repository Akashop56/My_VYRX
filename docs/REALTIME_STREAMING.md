# VYRX 2099 — Real-time CoT, Action Log & Streaming UX (Step 1)

> Goal: stop feeling like a chatbot that blocks and pastes an answer. The Body now
> watches the ReAct loop *as it runs* — reasoning, tool calls, observations,
> self-corrections — and the final answer is typed out instead of appearing at once.
>
> The loop itself is unchanged; this is a **new window into it**.

## 1. Transport

| | |
|---|---|
| Endpoint | `POST /ask_ronin` (alias: `POST /ask_ronin/stream`) |
| Media type | `text/event-stream` (SSE), one JSON object per frame |
| Negotiation | `Accept: text/event-stream` **or** `{"stream": true}` → SSE. Anything else → the original monolithic `AskResponse` JSON |
| Force JSON | `{"stream": false}` wins over the header (used by the Body's fallback path) |
| Keep-alive | `: keep-alive` comment every 10 s (defeats Termux/proxy buffering; headers also send `X-Accel-Buffering: no`) |
| Ordering | every frame carries `seq` (monotonic) so a client can detect gaps |
| Docs | `GET /agent/stream/protocol` returns the live event vocabulary |

Frame shape:

```
id: 7
event: tool_call
data: {"type":"tool_call","seq":7,"ts":1733814000.12,"step":1,"tool":"open_app", ...}
```

`data.type` always duplicates the `event:` line, so a client that only reads
`data:` (the Body's existing habit from `/api/events`) still works.

## 2. Event vocabulary

| `type` | emitted when | payload (beyond `type`/`seq`/`ts`) |
|---|---|---|
| `start` | request accepted | `request_id, session_id, message, input_mode, state{}` |
| `thinking` | a reasoning **phase** begins | `step, phase ∈ {plan,route,recall,prompt,call,reason,verify,answer}, text` |
| `thought` | the model is producing reasoning text *right now* | `step, stream, delta, final, text?` |
| `tool_call` | the agent decided to run a tool | `step, tool, label, args{}, device, thought?, action?` |
| `observation` | the tool came back | `step, tool, ok, ms, result` |
| `self_correction` | something failed and the agent is healing (alias: `reflexion`) | `step, tool?, reason, strategy, attempt, reflexion` |
| `token` | a chunk of the final answer | `i, text` |
| `answer_end` | server finished composing | `i, chars, tail` |
| `log` | the Brain's own action-log line for this turn | `time, level, text` |
| `state` | orb state change | `state, message, provider, model, response_ms, updated_at` |
| `done` | terminal frame | `response, route, steps, thought?, error?, needs_tool_result, streamed, elapsed_ms, command?, update_proposal?` |
| `error` | contained blow-up | `code, message, fatal, elapsed_ms` |

Two rules that keep the UI honest:

* **`thought` vs `token`.** `thought` is raw, unfinishing model prose (it may even
  contain a partial `<tool>` tag) → it belongs in the terminal, styled dim.
  `token` is *finalized*, tag-stripped, TTS-ready text → it belongs in the answer
  bubble. A turn never has to be rewound.
* **`thought` semantics.** Non-final frames append (`delta`); a final frame with
  `text` *replaces* the line (authoritative version, tool tags stripped). Exactly
  one of the two behaviours — no ambiguity for the client.

### Where the events come from

`thinking / tool_call / observation / self_correction` are emitted by
`core/planner.py` at the real decision points. `log` and `state` are free:
`ActionLog.log()` and `StateManager.set()` mirror whatever they already record onto
the stream bound to the current request via a `ContextVar`
(`core/streaming.py`), so no second instrumentation layer was bolted onto the loop
and every existing log line becomes visible in real time.

### Provider-level reasoning deltas

`core/llm_handler.complete(..., on_delta=cb)` re-requests the same call with
`stream: true` (OpenAI-family **and** Gemini `streamGenerateContent?alt=sse`),
forwards prose/`reasoning_content` deltas into `thought` frames, and folds the
stream back into the normal completion shape — fragmented `tool_calls` included.
A provider that rejects streaming raises `StreamingUnavailable` and the buffered
call is used instead, so streaming is never a correctness dependency.

## 3. Device tools now ride the same connection

Previously: LLM calls `open_app` → Brain returns a pending `AgentAction`, ending
the request → Body executes → `POST /agent/result` → *new* request resumes from a
saved session.

Now, while a stream is open: Brain emits `tool_call(device=true, action=…)`, parks
on `TOOL_RESULT_BRIDGE` (120 s), the Body executes and POSTs `/agent/result`, which
resolves the parked future → Brain emits `observation` and continues reasoning — all
on one connection, in order, with the terminal never going blank between steps.

`/agent/result` replies `{"streamed": true}` to acknowledge that the live stream
consumed the observation. No stream parked on that session? It falls back to
`continue_with_tool_result()` (the classic resume), so **old Bodies keep working
unmodified**.

Client disconnects mid-turn? The streamer is *detached*, not cancelled: frames stop,
but the agent finishes, persists the conversation, updates memory and stats anyway.

## 4. Typing effect

* Server: `AgentStreamer.stream_answer()` chunks the final text
  (`typewriter_chunks`) at a length-adaptive cadence — never under 0.7 s, never over
  5 s, so short answers still feel typed and long ones never dribble.
* Client: `ChatController` never pastes. A 32 ms ticker reveals
  `answerTarget.substring(0, revealed)` into the bubble, catching up proportionally
  when the network bursts ahead of the reveal. `done` is used only to reconcile, so
  the caret never jumps backwards.

## 5. Compose: the "Thought Process" terminal

`ui/chat/ThoughtTerminal.kt`, rendered **inside the active bubble**:

```
┌ ●●● THOUGHT_PROCESS.LOG [reasoning]      ● 3 steps · 4.4s ▾ ┐
│ > 07:02:41 ◇ Intent → llm (autonomous agent (ReAct))        │
│ > 07:02:41 ~ Analyzing the request… goal: launch YouTube     │
│ > 07:02:42 ▶ Executing open_app on the Body  package=com…   │
│ > 07:02:43 ✔ result ok · App launched: YouTube        405ms │
│ > Analyzing prompt… ▊                                        │
└──────────────────────────────────────────────────────────────┘
```

* Monospaced (`FontFamily.Monospace`), phosphor-green on near-black, CRT scanlines,
  window dots, per-level glyphs/colours: `◇` plan · `~` CoT · `▶` call · `✔` ok ·
  `✖` fail · `↺` self-correction · `◆` answer.
* Auto-scroll-free by design: it renders the last *N* lines (`… 12 earlier lines`
  for the rest) so it never nests a scroller inside a scrolling list.
* Expanded while the loop runs, auto-collapses when the answer starts typing
  (tap the header to pin it open). Finished bubbles keep their log snapshot and can
  be re-expanded.
* The header shows phase / step count / elapsed time; a blinking `▊` cursor marks
  liveness, reused at the end of the bubble text while the answer types.
* The Home screen orb reacts in the same frame the Brain thought it, because
  `state`/`log` frames are pushed into `BrainRepository` (deduped against the global
  `/api/events` feed).

## 6. Seeing it without a phone

```bash
cd RONIN_Brain_Python
python3 devtools/stream_preview.py "open youtube" --fake-llm        # scripted model, zero keys
python3 devtools/stream_preview.py "search for android 15 changes"  # against a live Brain
python3 devtools/stream_preview.py "hi" --fake-llm --raw            # also dump every frame as JSON
```

`--no-typing` prints answer chunks instantly; `--raw` interleaves each decoded frame as
compact JSON, which is the quickest way to check what the Brain actually framed.

It is a text-mode Body: same SSE client, same terminal rendering, and it answers
`tool_call(device=true)` by POSTing `/agent/result` like the real one.

## 7. Verifying

```bash
cd RONIN_Brain_Python
python3 -m pytest tests/ -q
```

`tests/test_streaming.py` covers frame encoding, chunk pacing bounds, the
`thinking → tool_call → observation → token* → done` order, provider failover
surfaced as `self_correction`, the parked device dispatch (success, failure and
timeout), "no stream ⇒ zero events", `Accept`-header negotiation and the legacy
JSON contract. `tests/test_android_contract.py` pins the two sides together: the
Kotlin decoder must handle exactly the vocabulary the Brain emits.

## 8. Next steps in the overhaul (not in this PR)

* **Step 2 — interrupt/steer:** the Body POSTs a `correction` frame mid-loop; the
  Brain injects it as a tool message and re-plans. Needs a `detach`-safe cancel
  token on the streamer.
* **Step 3 — resumable stream:** honour `Last-Event-ID` so a reconnect replays the
  missed `seq` window from a per-request ring buffer.
* **Step 4 — plan/act visualization:** `tool_call` already carries `step`; add
  `plan[]` to `thinking(phase="prompt")` and render chips instead of lines.
* Optional: drop a `JetBrainsMono_*.ttf` into `res/font` and swap
  `FontFamily.Monospace` for it — the terminal block is the only place to change.
