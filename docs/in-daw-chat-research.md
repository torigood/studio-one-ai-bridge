# In-DAW chat for Studio One 5 — research

Date 2026-10-17. Read-only research, no code changed. Legend as in `re-findings.md`:
**[O]** observed in shipped files / this machine · **[I]** inference · **[?]** unverified.

## TL;DR

- **[O]** A Studio One script can declare real GUI: a `Gadget` (own window, form from skin XML) and an `EditAddIn` panel. Forms can hold `EditBox` (text input, `multiline` option), `ListView` (rows from a script-built `Host:ListViewModel`), `Button`. Script reads typed text and gets button/text change callbacks (`paramChanged`). So a chat window (input + transcript + Send) is **buildable**.
- **[O]** Script has **no network and no process API**. So the panel cannot call the model. It must hand prompts to a host-side process through the mailbox files (already how the bridge works).
- **[I]** Reply refresh can reuse the **existing virtual-MIDI CC → `Run Jobs`** trigger, no idle task needed.
- **[?]** Not yet proven live: gadget loads and is modeless, typing works (focus, keyboard shortcuts, Korean IME), `paramChanged` fires on Enter, `ListViewModel` repaints from an edit-task call.
- Recommendation: build the host-side agent process first (needed by every UI option), with a native/web companion window as the first usable chat; then a minimal script Gadget as an experiment on top of the same files.

## 1. What UI a Studio One script can show

Source of truth: Studio One's own Macro extension (`/Applications/Studio One 5.app/Contents/Extensions/studioonemacros/scripts/macros.package`, a plain ZIP). `re/extracted/macros/*.js` has its scripts; its `skin/skin.xml`, `resources/gadget.xml` are **not** in `re/extracted` (only the `.js`), so skin lines below cite the package (`macros.package:skin/skin.xml:N`). Worth extracting into `re/extracted` later.

### 1.1 Declaring UI

| Piece | Evidence |
|---|---|
| `category="Gadget"` class + `ScriptMetaClass` with `Class:GadgetResource` url | `re/extracted/macros/classfactory.xml:15-19`, `:24-26` |
| Gadget resource names the skin form: `<Gadget themeName=.. formName="MacroOrganizer" iconName=.. menuIconName=../>` | `macros.package:resources/gadget.xml` |
| Docked toolbar panel: `category="EditAddIn"` + `formName` + `groupName="SongOnly.AddInPanel"` (also `MusicOnly`, `AudioOnly`) | `classfactory.xml:29-36`, `:41-48`, `:53-60` |
| Skin loaded via `Package:SkinFile value="skin/"` in package metainfo | `macros/metainfo.xml` |
| Class factory entry gets `args.packageInfo`; `kPackageID` used to find the theme: `Host.GUI.Themes.getTheme(kPackageID)` | `cclapp.js:65-68`, `:132-135` |
| Opening a gadget from script: `Host.GUI.Commands.interpretCommand("Gadgets", "Macro Organizer", false, Host.Attributes(new Array("State","1")))` | `macros/service.js:51` |
| Reach a live gadget object: `Host.Objects.getObjectByUrl("://hostapp/GadgetManager/Macro Organizer")` | `macros/shared.js:9` |
| Share state between service and gadget: `Host.Services.getInstance(classID).getMacroManager()` | `shared.js:3-6` |

### 1.2 Widgets and data binding

- **[O]** Form XML (`macros.package:skin/skin.xml`):
  - `MacroOrganizer` form: `windowstyle="dialogstyle sizable restorepos restoresize"`, contains `ListView name="fileList"` (`:124-127`), several `Button`, a `Link` (`:130-136`).
  - Single-line `EditBox` bound by name, `options="border immediate"` (`:154`); `immediate` is presumably "fire on every keystroke" **[?]**.
  - Multi-line text: `<EditBox name="text" ... options="multiline" style="NameEditor"/>` (`:198`).
- **[O]** Controller side (`CCL.Component`, `re/extracted/macros/cclapp.js:122-163`):
  - `this.paramList = ccl_new("CCL:ParamList")`, `paramList.controller = this`.
  - `paramList.addString("text")` → bound to the EditBox of that name (`renamer.js:123`, `editor.js:23-25`). Read/write with `paramList.lookup("text").string` (`renamer.js:138-139,148`).
  - `paramList.addParam("new")` → Button of that name; click arrives in `paramChanged(param)` switch on `param.name` (`organizer.js:12-16`, `:70-79`).
  - Transcript list: `ccl_new("Host:ListViewModel")`, `columns.addColumn(...)`, `newItem(title)`, `item.details.<col>`, `addItem`, `removeAll`, `changed()` (`organizer.js:19-23`, `:41-63`). Selection/open events: `CCL.kItemFocused` / `kItemOpened` in `notify`.
- **[O]** Modal dialogs: `Host.GUI.runDialog(theme, formName, controller)` (`organizer.js:67`, `renamer.js:158`, `editor.js:93`), `runDialogWithParameters` (`editor.js:253`), `Host.GUI.alert`, `Host.GUI.ask`.
- **[O]** Other UI: `Host.GUI.Desktop.getApplicationWindow()`, `CCL:FloatingSprite` overlay animation (`voodoo/voodoo.js:53-73`), menus via `menu.addCommandItem` (`renamer.js` buildMenu).
- **[O]** Open external things: `Host.GUI.openUrl(url)` (used with a file URL: `macros/panel.js:916`), `Host.GUI.showInBrowser(folder)` (`organizer.js:117`).

### 1.3 Can a chat panel be built from this?

- Input text: yes (EditBox + string param). **[?]** Enter-to-send: a `Button` named `send` is certain; Enter in a single-line EditBox probably commits the string param and fires `paramChanged` **[?]**.
- Transcript: yes, `ListView` rows (one row per message line; long text needs manual wrapping or one row per paragraph). A read-only multiline EditBox bound to one string would be simpler **[?]** (the `multiline` EditBox is editable in the Macro "Edit Names" dialog; read-only option unknown).
- Modeless: **[I]** gadgets are modeless windows (Macro Organizer is opened from the Gadgets menu while editing). The Organizer form is also used with the modal `runDialog` (`organizer.js:67`), so both modes exist for the same form. **[?]** not confirmed live that a Gadget stays open while the song plays/edits.
- Docked bottom panel: `EditAddIn` panel is built for command-bar buttons; hosting an EditBox there is **[?]**.
- Not found: custom drawing, markdown, streaming text widget, any auto-scroll API for ListView.

### 1.4 What the script cannot do

- **No network.** Dump of `Host` at runtime (`~/Music/Studio One 5/AIBridge/host-api.json`, written by `Dump Host API`, 2026-10-17) lists only `Interfaces, Results, Classes, Objects, Services, Console, Signals, Locales, SystemInfo, IO, FileTypes, Security, GUI, Graphics, Engine, Settings`. No `Network`, `WebClient`, `XMLHttpRequest`, `Process`. **[O]** (The names appear as strings in `cclnet.framework`, but are not exposed on `Host`; `re-findings.md:30` guess is therefore **false for the top-level namespace**. Whether `Host.Classes.createInstance("CCL:...")` can build a native web-client class is **[?]**, brute-forcing class IDs is a possible later probe.)
- **No process launch**; the only outward hooks are `Host.GUI.openUrl` and `Host.IO` file ops.
- **No timer.** `addIdleTask` with a script object crashes/hangs 5.5 (`re-findings.md:90`, `bridge/src/bridge.js:209-212`). `Host.sleep(ms)` exists (`voodoo.js:67`) but blocks the main thread. `Host.Signals.advise(topic, obj)` gives events from the app (e.g. `engine.trackedit`, `voodoo.js:16`), not a time tick **[?]** other topics.
- **No edit context in a Gadget.** The edit context (`context.functions`, tracks) is passed only to `EditTask.performEdit`. Gadget code reaches the song only via commands or by writing a job and having `Run Jobs` run it. **[I]**

## 2. Proposed architecture

```
 Studio One                                       Host side (macOS)
 ┌─────────────────────────────────┐              ┌─────────────────────────────────────┐
 │ AI Chat gadget (JS+skin)        │  write       │ s1bridge chat agent (Python)        │
 │  EditBox → [Send] ──────────────┼─ chat/in/ ──►│  FS watch / 200 ms poll             │
 │  ListView ◄─ reads chat/out ◄──┐│  <id>.json   │  loop: Messages API (stream)        │
 │                                 ││              │   ├ tool_use → s1bridge.tools       │
 │ "AI Bridge - Run Jobs" task ────┘│  chat/out/   │   │    (daw_*: mailbox + MIDI CC)   │
 │   (also refreshes the gadget)   │◄─<id>.jsonl ──│   └ text deltas → append to out     │
 └──────────────▲──────────────────┘              │  presses CC 119 on each flush       │
                │ CC 119 ch16 (virtual MIDI "AI Bridge" daemon)  └────────────────────────┘
                └─────────────────────────────────────────────────┘
```

**Who calls the model:** a host-side process, not the script and not the MCP server. The MCP server is a passive tool provider for an external client (`README.md` "How it works"). Options for the agent:

1. **Direct Anthropic Messages API** from Python `urllib` (keeps "stdlib only" in `pyproject.toml`), tools = the existing handlers in `server/s1bridge/tools.py`, same process, same mailbox. API key from macOS Keychain. Simplest; full control over approvals and streaming.
2. **Spawn `claude -p --output-format stream-json` with `--mcp-config` pointing to this MCP server**; uses the user's existing Claude login, no key handling. **[?]** flags and the terms for headless use of a subscription must be checked before relying on it.
3. Claude Agent SDK (Python/TS) with this MCP server as stdio tool source. **[?]** adds a dependency and packaging.

**Inbound (user → AI), no trigger needed:** the gadget's `paramChanged("send")` writes `AIBridge/chat/in/<time_ns>-<rand>.json` `{id, text, song}` using the same `Host.IO.createTextFile` pattern as `writeJson` in `bridge/src/bridge.js:45`. The agent watches that folder (FSEvents or a 200 ms poll). **[?]** `Host.IO.File.moveTo` does not work (`re-findings.md:88`), so mark consumed by a sibling result file like the jobs do (`bridge.js:73-75`).

**Tool calls:** the agent executes `daw_*` tools exactly as the MCP server does now: `Mailbox.enqueue` + `midi.press(119)` (`server/s1bridge/mailbox.py:38-49`, `midi.py:203`). So the existing live-ops, offline song edits (save/close/reopen via CC 116/115) and `audio_analyze` all work unchanged. Offline song writes close and reopen the song (~8 s): the chat window must survive that (it is a gadget, not part of the song, **[?]**; a companion app trivially does).

**Reply path without idle task:** the agent appends events to `chat/out/<id>.jsonl` (`text`, `tool_start`, `tool_result`, `done`) and presses CC 119. The surface runs `Track/AI Bridge - Run Jobs`; `RunJobsTask.performEdit` (`bridge/src/tasks.js:186-204`) first runs pending jobs, then calls a new `refreshChat()` that reads the new lines and updates the gadget's `ListViewModel` (+ `changed()`). The gadget object is reached with the `Host.Services.getInstance` singleton pattern (`shared.js:3-6`) or `getObjectByUrl("://hostapp/GadgetManager/AI Chat")` (`shared.js:9`). Streaming = the agent presses CC at ≤ 4 Hz while text is flowing. **[?]** every press is a full edit-task run: undo-history noise, 50-100 ms main-thread cost, needs an open song (context may be absent otherwise).

**Approvals:** destructive tools (`daw_delete_track`, `daw_delete_event`, offline writes that close the song) should pause the agent loop and show an Approve/Deny row in the chat. The agent writes an `approval` event; the gadget's Approve button writes `chat/in/<id>.approve.json`.

**Fallback UI that needs no script UI:** same agent + files, with a small native companion (see §4).

## 3. Risks

- **Startup crash class (high, known).** Early builds crashed Studio One at launch by touching idle tasks/command attributes from a startup service (`re-findings.md:90`). A new Gadget + skin adds a second code path loaded at startup. Mitigation: ship it as a **separate extension** (`local.s1aichat`) so a bad skin cannot break the bridge; keep the bridge extension unchanged until the chat works.
- **Modal vs modeless (high).** `runDialog` is modal and would block the main thread; the Run-Jobs edit task also runs on that thread. Use the Gadget path, never `runDialog`, for chat. **[?]** whether an open Gadget plus an edit task run in parallel without deadlock.
- **Text input in a DAW (high, unverified).** Typing `Space`, letters or digits may trigger Studio One key commands if focus handling differs; CCL EditBox normally takes focus **[I]**. **Korean/Japanese IME composition in CCL EditBox is unknown [?]**; this user writes Korean, so it needs an early live test. Copy/paste inside the EditBox **[?]**.
- **Main-thread cost / UI freeze.** All script runs on the UI thread; a long `ListViewModel` rebuild per CC press can stutter playback UI. Append only new rows.
- **Reply latency / dropped refresh.** CC press is lost if the MIDI daemon rebinding is in progress (`re-findings.md:111`); the agent should re-press if `chat/out` is not acknowledged (gadget writes `chat/ack`).
- **Security.** Prompt injection via song/track/marker names or tool output reaching the model, with tools that delete tracks/events. Mitigate with approvals (§2) and a read-only default mode. API key must not live in the mailbox (world-readable files: `-rw-rw-rw-` in `~/Music/Studio One 5/AIBridge`). Use Keychain + a localhost socket with a token if a web UI is used.
- **Cost/abuse.** Streaming + agent loops can spend tokens quickly; add per-session caps.
- **Install/distribution.** A script gadget installs like the bridge (unsigned extension + `installdata.xml`, `re-findings.md:84`). A native companion app needs code signing/notarization for friction-free install; a Python process started by the existing MCP server/daemon avoids that.
- **Version scope.** Everything verified only on 5.5.2 arm64.

## 4. Fallback: native companion

Small menu-bar / floating-window app (Swift `NSPanel` at `.floating` level, or a local web page opened in the browser) talking to the same agent process. Pros: guaranteed text input/IME/copy-paste, markdown rendering, streaming, no DAW stability risk, works while the song closes/reopens, can show tool-call details and approval buttons. Cons: a separate window (can be pinned above Studio One; Studio One fullscreen Spaces is awkward), install step, signing for distribution. It shares 100 % of the backend with the gadget, so building the agent first loses nothing.

Cheap hybrid **[?]**: add an EditTask "AI Bridge - Open Chat" that calls `Host.GUI.openUrl(Host.Url("http://127.0.0.1:<port>"))`. `openUrl` is proven only with a file URL (`panel.js:916`); an `http://` URL opening the default browser is likely **[?]** but untested. A surface button or a key command can then open the chat from inside the DAW.

## 5. Recommended first prototype

Goal: end-to-end "type a sentence → AI edits the open song → reply shown", with the least DAW risk, in this order.

1. **Chat agent, UI-less (1-2 days).** `python3 -m s1bridge.chat`: reads `chat/in/*.json`, runs the Messages API tool loop with the existing tool handlers, writes `chat/out/<id>.jsonl`, presses CC 119 on flush. Test with a CLI front-end (stdin/stdout) and the mock host in `tests/`. Decide API-key vs `claude -p` here.
2. **Local web chat page served by the agent** (`http.server` + SSE, opened via browser or `Host.GUI.openUrl`). This is already a usable "talk to the AI while composing" tool, and it proves the approval + streaming flow. No Studio One change.
3. **Gadget feasibility spike (separate extension `local.s1aichat`, ~½ day).** Static gadget: EditBox `prompt`, Button `send`, ListView `log`. Checks, each logged to a file in the mailbox:
   - gadget appears under Gadgets and stays open during play/edit;
   - typed text arrives in `paramChanged`; Enter behaviour; spaces and letters don't fire DAW shortcuts; **Korean IME works**; paste works;
   - `send` writes `chat/in/*.json`;
   - a `Run Jobs`-triggered `refreshChat()` adds ListView rows that repaint;
   - repeated refresh at 4 Hz leaves CPU/undo history acceptable.
   Go/no-go: if text input or IME fails, stay with the companion/web UI and drop the gadget.
4. Only after step 3 passes: wire the gadget to the real agent (§2 reply path) and add approvals.

Open questions that decide the design (all **[?]**): modeless gadget lifetime across song close/reopen; EditBox enter/IME; read-only multiline text for transcript; whether `Host.Classes.createInstance` exposes any network-capable CCL class (would remove the host process dependency for the model call, but API key handling inside a script would be worse anyway); whether Run Jobs can run with no song open (needed for refresh when the song is closing/reopening).
