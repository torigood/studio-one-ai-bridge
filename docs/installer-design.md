# One-click install — design

Date 2026-10-18. Design only, no code changed. Legend as in `re-findings.md`:
**[O]** observed · **[I]** inference · **[?]** unverified, test before relying on it.

## Goal

A Studio One 5 user on a Mac with a Claude subscription, who has never used a terminal, goes
from nothing to "the AI edits my song" in:

1. Download one file from GitHub Releases.
2. Double-click it. Claude Desktop shows **Install**.
3. Quit Studio One and tell Claude "Set up Studio One". Claude runs the setup tool.
4. Start Studio One, open a song, talk.

No terminal, no JSON editing, no Options → External Devices dialog.

## What has to land on the machine

| # | Piece | Where | Today |
|---|---|---|---|
| 1 | Extension (`aibridge.package` + `metainfo.xml` + `installdata.xml`) | `<S1 settings>/Extensions/local.s1aibridge/` | `install.py`, manual |
| 2 | Control surface (`AI Bridge.device`, `AI Bridge.surface.xml`) | `<S1 settings>/User Devices/AI Bridge/` | `install.py --surface` |
| 3 | Device registration (surface ↔ MIDI port) | `<S1 settings>/MusicDevices.settings` | **manual dialog** |
| 4 | MCP server (Python, stdlib only) + MIDI daemon | anywhere; daemon starts on demand | git clone |
| 5 | Client registration | Claude Desktop / Claude Code / other | manual JSON or `claude mcp add` |
| 6 | Command catalog | `<data dir>/commands.json` | `install.py` builds it |
| — | Optional: numpy, loop index, VST3 names | — | manual, stays optional |

`<S1 settings>` = `~/Library/Application Support/PreSonus/Studio One 5`.

## Finding: the device dialog can be skipped

**[O]** The External Devices dialog only writes one entry into `MusicDevices.settings`
(this machine, 2026-10-18):

```xml
<MusicDeviceDescription name="AI Bridge" receivePortID="Capture/FFFFFFFFF726E7E0::AI Bridge">
    <UID x:id="modelID" uid="{8C927C40-0146-4160-8F9C-2535B49B6F6F}"/>
    <UID x:id="instanceID" uid="{A1475657-C92B-324E-AE94-1AD4A57A7208}"/>
</MusicDeviceDescription>
```

- `modelID` = `classID` of `surface/AI Bridge.device`. Fixed.
- `receivePortID` = sign-extended CoreMIDI unique ID of our daemon source (`midi.UNIQUE_ID`,
  0xF726E7E0) + port name. Fixed, same on every machine.
- `instanceID` = random per install **[I]**; setup generates a new UUID.
- `Surface Data[1]/local AI Bridge.surfacedata` is an empty assignment file **[I]** Studio One
  creates itself.

So setup can append this entry while Studio One is not running. **[O]** Tested 2026-10-18:
entry and surfacedata removed, entry written by `s1bridge.devices.register` with a new
instanceID, Studio One started, `daw_ping` answered with no user action; Studio One kept the
entry on quit and recreated the surfacedata file (`re-findings.md`). Not yet tested: a machine
where `MusicDevices.settings` does not exist (first Studio One start ever). Studio One rewrites the file on quit, so writing while it runs is lost or
clobbered **[I]**; setup refuses while `studioapp` runs (as `install.py` already does).

Edge: if the port does not exist yet when Studio One starts, it re-binds live once the daemon
creates it (`re-findings.md:111`) **[O]**. Setup should still start the daemon before the user
starts Studio One.

## Delivery: three layers over one setup module

```
                  ┌─────────────────────────────────────────┐
                  │ s1bridge.setup  (Python, stdlib)        │
                  │  plan / apply / uninstall / doctor      │
                  └──────▲──────────────▲──────────────▲────┘
                         │              │              │
  .mcpb for Claude Desktop      CLI for everyone       (later) signed Mac app
  MCP tools bridge_setup,       python3 -m s1bridge    wraps the same module
  bridge_doctor                 setup --client ...
```

### Layer 0: `s1bridge.setup` (the only install logic)

Move `install/install.py` logic into the package so it works without a checkout:

- Ship `bridge/src/*` and `surface/*` as package data; build `aibridge.package` (a ZIP) at
  setup time, as `bridge/build.py` does now.
- Steps: extension, surface, device entry, catalog, client registration, start MIDI daemon.
- Keep today's guarantees: dry run by default on the CLI, backup of every touched file
  (move backups to `<data dir>/backups/`, the package dir may be read-only), refuse while
  Studio One runs, never touch the app bundle.
- Idempotent: re-running updates changed files, leaves an existing device entry alone.
- `doctor` checks, each with a plain-language fix:
  Studio One found and version · extension present and its version = bundled version ·
  surface present · device entry present · MIDI daemon running · mailbox folder writable ·
  `daw_ping` round trip · catalog present · numpy (optional).
- `uninstall`: extension, surface, device entry, data dir. Leaves Claude's own config to the
  client (Claude Desktop removes its extension itself).
- `install/install.py` becomes a thin wrapper so existing docs keep working.

### Layer 1: Claude Desktop extension (`.mcpb`) — the main path

**[O]** MCPB manifest (github.com/modelcontextprotocol/mcpb, `MANIFEST.md`): `server.type`
`node` / `python` / `binary` / `uv` (v0.4+); `compatibility.platforms`, `compatibility.runtimes`;
`user_config` with `directory` type and `${HOME}` defaults.

- `server.type: "python"`, `entry_point: server/run.py`. No dependencies to bundle (stdlib).
- `compatibility.platforms: ["darwin"]`, `runtimes.python: ">=3.9"`.
- `user_config`: optional Studio One app path and user content folder (defaults = today's
  `config.py` defaults), passed as `S1BRIDGE_APP` / `S1BRIDGE_USER_CONTENT`.
- New MCP tools:
  - `bridge_setup` — runs setup (no dry-run gate; the tool call itself is the user's approval
    in Claude Desktop). If Studio One runs, returns "quit Studio One, then ask again".
  - `bridge_doctor` — runs doctor, returns findings the model can explain.
- `daw_status` / `daw_ping`: when the bridge is missing or outdated, the error says
  "run bridge_setup" so the model offers it without being told.
- Build: `packaging/build_mcpb.py` writes the ZIP itself (stdlib, manifest generated from
  `tools.TOOLS` so the tool list stays in sync). Attach the `.mcpb` to each GitHub Release.
- **[O]** Claude Desktop 2.42304.0 ships no Python or uv in its bundle, so the `python` type runs
  the system `python3` (`mcp_config.command`).
- **[O]** Claude Desktop passes a `user_config` default such as `${HOME}/Music/Studio One 5`
  into `env` without substituting `${HOME}` (process environment, 2026-10-18). `config._env`
  expands `${HOME}` itself and treats any other `${...}` as unset.

Why this is the main path: double-click install, no config editing, no Gatekeeper prompt for an
unsigned app (the `.mcpb` is a bundle Claude Desktop unpacks, not an app we ship) **[I]**, and
it uses the user's existing Claude subscription.

**[?]** Which Python runs a `python` type extension. If Claude Desktop uses the system
`python3`, a Mac without Command Line Tools has only the `/usr/bin/python3` stub, which shows
the "install developer tools" dialog. Test on a clean macOS user / VM. Fallbacks, in order:
`uv` server type if Claude Desktop provides uv **[?]**; `binary` type with a bundled
python-build-standalone runtime (~30 MB) **[?]** quarantine behaviour on that executable.

### Layer 2: CLI for other clients

```bash
uvx studio-one-ai-bridge setup --client claude-desktop|claude-code|cursor|print
```

(or `python3 -m s1bridge setup ...` from a checkout). Client adapters:

| Client | How | |
|---|---|---|
| Claude Desktop | merge `mcpServers.studio-one` into `~/Library/Application Support/Claude/claude_desktop_config.json`, keep other servers, back up first | only if not using the `.mcpb` |
| Claude Code | `claude mcp add --scope user studio-one -- <python> <run.py>` | |
| Cursor and similar | merge into their `mcp.json` | **[?]** paths per client |
| `print` | print the command / JSON snippet | ChatGPT and anything else |

**[?]** Whether ChatGPT desktop can run a local stdio MCP server with a subscription. If it
only takes remote MCP URLs, it needs a local HTTP/SSE transport on the server, a separate task.

### Layer 3 (later): signed Mac app

Menu-bar app with "Install", "Doctor", "Uninstall" buttons over the same module. Needs an Apple
Developer ID ($99/year) for signing and notarization; unsigned apps get the Gatekeeper warning
that this design tries to avoid. Build only if the `.mcpb` path proves too narrow.

## Updates

- `daw_ping` already returns the bridge version (`bridge.js` `kBridgeVersion`) **[O]**.
  Server compares it with the bundled version; on mismatch `daw_status` says
  "bridge outdated: quit Studio One and run bridge_setup".
- New `.mcpb` = new server; the extension update still needs one Studio One restart (unchanged
  constraint).

## Out of scope

Windows (no CoreMIDI virtual port; needs loopMIDI or similar), Studio One 6/7, Intel Macs: no
change in this design beyond keeping paths in `config.py`. Loop index and VST3 helper stay
optional manual steps.

## Plan

| Step | Work | Done when |
|---|---|---|
| 0 | Live test: device entry written by a script binds the surface | **done 2026-10-18**, `server/s1bridge/devices.py` |
| 1 | `s1bridge.setup` + CLI (`setup`, `doctor`, `uninstall`), device entry, tests on temp dirs | **done 2026-10-18**: `server/s1bridge/setup.py`; sources still read from the checkout (no package data yet, an `.mcpb` can ship the checkout layout) |
| 2 | MCP tools `bridge_setup`, `bridge_doctor`; version check in `daw_status` | **done 2026-10-18**: tools in `tools.py`; `daw_status` has `bridge_expected` / `bridge_installed` / `problem`, `daw_ping` a `warning`; setup output kept off the MCP stdout |
| 3 | `.mcpb` manifest + build script; test on clean macOS user | **built 2026-10-18**: `packaging/build_mcpb.py` → `dist/*.mcpb`; unpacked bundle runs with `/usr/bin/python3` 3.9.6 and an empty environment (doctor, ping, setup dry run). Installed in Claude Desktop 2.42304.0 (open .mcpb → Install): extension `local.mcpb.kwansik.studio-one-ai-bridge`, 52 tools announced, server runs as `/usr/bin/python3 <ext>/server/run.py`; the installed copy answers `daw_ping` / `daw_list_tracks`. A Claude Desktop Chat conversation called `daw_ping` through the extension (pong, bridge results 13:34:30 and 13:36:22). Claude Desktop starts two server processes; only the first logs to `mcp-server-<name>.log`, so tool calls may not show there, check the mailbox outbox instead. Not yet: `mcpb validate`, clean macOS user without Command Line Tools |
| 4 | README "Quick start" for non-developers (4 steps above); developer install moves below | |
| 5 | Client adapters (Claude Code, Cursor, print) | |

Step 0 passed, so step 1 includes the device entry and the External Devices dialog drops out
of the user's steps.
