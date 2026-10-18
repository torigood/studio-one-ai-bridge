# Studio One AI Bridge

Let an AI assistant (Claude, ChatGPT/OpenAI agents, or any [MCP](https://modelcontextprotocol.io)
client) read and operate **PreSonus Studio One 5**: list and summarise your songs, add tracks,
write notes, load instruments and effects, set volume, pan and plug-in parameters.

> Unofficial. Not affiliated with PreSonus or Fender. See [DISCLAIMER.md](DISCLAIMER.md).

**Tested only on Studio One 5.5.2, macOS (Apple Silicon / arm64).** Other Studio One versions,
Intel Macs and Windows are untested. Expect rough edges: this is an experiment built on
reverse-engineered behaviour ([docs/re-findings.md](docs/re-findings.md)).

## How it works

```
AI client ──MCP (stdio)──► MCP server (Python, standard library only)
                             ├─ song_*, library_*   read files on disk (read-only)
                             └─ daw_*               write a job to the mailbox ──┐
                                                                                 ▼
                                   ~/Music/Studio One 5/AIBridge/{inbox,outbox}
                                                                                 ▲
Studio One ── AI Bridge extension (JS) ── "AI Bridge - Run Jobs" ───────────────┘
      ▲
      └── virtual MIDI port "AI Bridge" (CC 119, ch 16) ── owned by a small MIDI daemon the MCP server starts
```

- **Offline tools** (`song_*`, `library_*`, `audio_analyze`) need only Python. They read saved
  `.song` files, the plug-in list and Sound Set file names. They never write, except
  `song_write_cc`, `song_add_loop`, `song_write_chords` and `song_set_time_signatures` (Studio One's
  scripting API cannot create these). If the song is open, these save it, close it (surface CC 116/115),
  write and reopen it by themselves (~8 s, undo history is lost). Reinstall the surface (`--surface`)
  after updating, or the close step cannot happen.
- **Live tools** (`daw_*`) need the AI Bridge extension installed in Studio One. The server
  drops a job file in a mailbox folder and presses a virtual MIDI button; a small control
  surface maps that button to the extension's "Run Jobs" command, which runs the job in the open
  song and writes the result back.

## Requirements

- Studio One 5 (tested 5.5.2) on macOS
- Python 3.9+ (the macOS system `python3` works). No third-party packages, except `numpy`
  for the optional audio analysis.
- An MCP client: Claude Code, Claude Desktop, the OpenAI Agents SDK, ...

## Quick start: Claude Desktop

1. Get `studio-one-ai-bridge-<version>.mcpb` (build it with `python3 packaging/build_mcpb.py`, it
   lands in `dist/`) and double-click it. Claude Desktop shows the extension; click **Install**.
2. Save your song and quit Studio One, then ask Claude: "Set up Studio One." It runs
   `bridge_setup`, which installs the extension, the control surface and its device entry.
3. Start Studio One, open a song, and ask Claude for something, e.g. "Add a piano track".

The extension runs with the macOS `python3`. If macOS asks to install the Command Line Tools
the first time, accept; the server needs nothing else. The settings of the extension let you
change the Studio One app path and the user data folder (Preferences → Locations in Studio One).

## Install from a checkout (any MCP client)

### 1. Get the code

```bash
git clone https://github.com/<you>/studio-one-ai-bridge.git
cd studio-one-ai-bridge
python3 -m unittest discover -s tests
```

### 2. Install the Studio One side

Quit Studio One first.

```bash
python3 server/run.py setup
python3 server/run.py setup --apply
```

`setup` is a dry run unless you pass `--apply`. It prints every file it will write, backs up
what it touches into `~/Library/Application Support/studio-one-ai-bridge/backups/`, refuses to
run while Studio One is open, and never modifies the Studio One application itself. It installs:

- the extension into `~/Library/Application Support/PreSonus/Studio One 5/Extensions/local.s1aibridge/`
- the "AI Bridge" control surface into `.../Studio One 5/User Devices/AI Bridge/`
- the device entry in `.../Studio One 5/MusicDevices.settings` that connects the surface to the
  "AI Bridge" MIDI port (what Options → External Devices → Add would write)
- a command catalog generated from **your** Studio One installation into
  `~/Library/Application Support/studio-one-ai-bridge/commands.json` (used by `commands_search`;
  it is not shipped with this repository). Skip it with `--no-catalog`.

and starts the small MIDI daemon that owns the "AI Bridge" port.

Check everything at any time (with Studio One running and a song open it also pings the bridge):

```bash
python3 server/run.py doctor
```

The older `python3 install/install.py --surface [--apply]` still works and does the same.
Once the MCP server is registered (step 3), the AI can also do this for you with the
`bridge_doctor` and `bridge_setup` tools: quit Studio One and ask it to set up Studio One.

### 3. Register the MCP server with your AI client

Claude Code:

```bash
claude mcp add studio-one -- python3 /path/to/studio-one-ai-bridge/server/run.py
```

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "studio-one": {
      "command": "python3",
      "args": ["/path/to/studio-one-ai-bridge/server/run.py"]
    }
  }
}
```

Or run it as a package with [uv](https://docs.astral.sh/uv/) or pipx (the extension still has to
be installed from a checkout, step 2):

```bash
uvx --from /path/to/studio-one-ai-bridge studio-one-ai-bridge
```

OpenAI Agents SDK (Python):

```python
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

server = MCPServerStdio(params={"command": "python3",
                                "args": ["/path/to/studio-one-ai-bridge/server/run.py"]})
async with server:
    agent = Agent(name="Producer", mcp_servers=[server])
    print((await Runner.run(agent, "Summarise my latest Studio One song")).final_output)
```

### 4. Start Studio One

Start Studio One, open a song and ask the AI to run `daw_ping`. No device setup is needed:
`setup` registered the control surface, and the MIDI daemon keeps the **AI Bridge** port with a
fixed CoreMIDI unique ID, so Studio One finds it again after the AI client or the daemon restarts
(MCP servers start the daemon on demand).

If `daw_ping` stays queued, run `python3 server/run.py doctor`. To add the device by hand instead:
**Studio One → Options → External Devices → Add...**, folder **local**, **AI Bridge**,
**Receive From: AI Bridge**.

If the MIDI trigger is not set up, each live request waits until you run
**Track → AI Bridge - Run Jobs** by hand. Set `S1BRIDGE_MIDI=0` to disable the virtual port.

### Optional: loop length index (for `song_add_loop`)

A song refers to a Sound Set loop as `package://<sound set>/<path>`; the loop itself stays
encrypted and Studio One decodes it. Repeating a loop needs its length, which Studio One only
writes into a song when it saves one that uses the loop. Build the index once:

1. Save and close a song that has an audio track (any test song; back it up yourself or note
   that the next step adds ~2200 events to its first audio track).
2. `cd server && python3 -m s1bridge.loops fill "/path/to/that.song"`
3. Open it in Studio One, wait for it to load, **File > Save**, close it.
4. `python3 -m s1bridge.loops harvest` (reads lengths from all saved songs into
   `<data dir>/loops.json`; also run it later to pick up loops you placed by hand).
5. Restore the song from your backup.

Tempo comes from the song when Studio One stored one, else from a BPM number in the file name,
else it is guessed (75-150 BPM, power-of-two beats).

### Optional: audio analysis

`audio_analyze` and the audio part of `song_analyze` need numpy. Either create `.venv` in the
checkout (`python3 -m venv .venv && .venv/bin/pip install numpy`), install the package with the
extra (`pip install "studio-one-ai-bridge[analysis]"` / `uvx --from "/path[analysis]" ...`), or
point `S1BRIDGE_ANALYSIS_PYTHON` at any Python that has numpy.

### Optional: VST3 parameter names

`tools/vst3dump/` builds a small helper that reads parameter names of installed VST3 plug-ins.
It needs Xcode command line tools and the VST3 `pluginterfaces` headers, which are not included:

```bash
git clone https://github.com/steinbergmedia/vst3_pluginterfaces.git third_party/pluginterfaces
tools/vst3dump/build.sh
```

### Uninstall

Quit Studio One, then:

```bash
python3 server/run.py uninstall --apply
```

This removes the extension, the surface, its device entry and the catalog.

## Tools

| Tool | What it does |
|---|---|
| `song_list` | Saved songs, newest first |
| `song_summary` | Tempo, markers, tracks, parts, mixer state of a saved song |
| `song_track_notes` | Notes of one track in a saved song |
| `song_analyze` | Key/range per track plus audio analysis, for arranging |
| `song_write_cc` | Write MIDI CC (modulation, expression, ...) into a part of a saved song. If the song is open it is saved, closed and reopened automatically; the original is backed up first. |
| `song_add_loop` | Place a Sound Set `.audioloop` on an audio track of a saved song, repeated to a length. Auto close/reopen like song_write_cc; backed up first. Needs the loop length index (below). |
| `audio_analyze` | Level, spectrum balance, tempo and key estimate of a WAV file (numpy) |
| `library_instruments`, `library_effects` | Installed plug-ins with class IDs |
| `library_plugin_params` | Parameter IDs of native PreSonus plug-ins (from their presets) |
| `library_loops` | Loops in installed Sound Sets, with length/BPM once indexed |
| `commands_search` | Search the generated command catalog |
| `bridge_doctor` | Check every piece of the Studio One side and say how to fix what is missing |
| `bridge_setup` | Install or update the Studio One side (same as `setup --apply`; Studio One must be quit) |
| `daw_status`, `daw_ping` | Bridge health; both warn when the installed bridge does not match the server |
| `daw_list_tracks`, `daw_list_mixer` | Tracks and mixer channels of the open song |
| `daw_add_track`, `daw_insert_notes` | Add tracks, write a part with notes |
| `daw_rename_track`, `daw_duplicate_track`, `daw_delete_track` | Rename (with its instrument channel), duplicate with all parts/clips, delete |
| `daw_read_notes`, `daw_edit_notes` | Read the open song's notes; change, add, delete notes |
| `daw_read_chords`, `song_write_chords` | Chord track (read live, write offline with auto reopen) |
| `daw_transport_info`, `daw_set_tempo`, `daw_set_position`, `song_set_time_signatures` | Tempo, playhead, time signatures |
| `daw_bounce` | Render a track to audio and analyze it |
| `daw_list_events`, `daw_repeat_event` | Events on a track; repeat a loop or part back to back |
| `daw_add_instrument`, `daw_insert_effect` | Load instruments and insert effects |
| `daw_set_channel_param` | Volume / pan (instrument tracks: their instrument channel is found automatically, saving the song if needed) |
| `daw_plugin_param`, `daw_scan_plugin_params` | Read/set plug-in parameters |
| `daw_command` | Run a Studio One command (dangerous ones blocked) |
| `daw_transport` | Play / stop |
| `daw_inspect_*`, `daw_probe`, `daw_dump_host` | Read-only API discovery, for development |
| `daw_call` | Call script methods with tracks/events as arguments (API experiments; can edit the song) |

Tools marked `EXPERIMENTAL` in their description use API details that are not fully verified.

## Safety

- Save your song before letting an AI edit it. Each bridge operation is a normal Studio One edit
  (Edit → Undo works), but the bridge acts on whatever song is open.
- Offline tools open files read-only. Sound Set contents are never decrypted or read; only their
  file names are listed.
- `daw_command` refuses File/Close, Quit, Revert, New and Open.
- Nothing is written into Studio One folders without `setup --apply` (or `install.py --apply`), and
  the application bundle is never modified unless you explicitly choose the `--target app-scripts` option.
- The mailbox is a plain folder in your user directory; any local process can drop jobs into it.

## Configuration

All paths live in [server/s1bridge/config.py](server/s1bridge/config.py) and can be overridden
with environment variables: `S1BRIDGE_APP`, `S1BRIDGE_SETTINGS_DIR`, `S1BRIDGE_USER_CONTENT`,
`S1BRIDGE_SONGS`, `S1BRIDGE_SOUNDSETS`, `S1BRIDGE_PRESETS`, `S1BRIDGE_MAILBOX`,
`S1BRIDGE_DATA_DIR`, `S1BRIDGE_CATALOG`, `S1BRIDGE_ANALYSIS_PYTHON`, `S1BRIDGE_MIDI`.

The mailbox must be `<Studio One user content>/AIBridge`, because the extension opens
`local://$USERCONTENT/AIBridge`.

## Windows (untested)

The code has best-guess Windows defaults (`%APPDATA%\PreSonus\Studio One 5`,
`Documents\Studio One 5`, `C:\Program Files\PreSonus\Studio One 5`) but has never been run there.
The automatic trigger will not work as is: `server/s1bridge/midi.py` creates the virtual port with
macOS CoreMIDI. On Windows you would need a virtual MIDI port such as
[loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) named "AI Bridge" and code that
sends CC 119 on channel 16 to it. Until then, run **Track → AI Bridge - Run Jobs** by hand.
Contributions welcome.

## Repository layout

```
server/s1bridge/   MCP server: JSON-RPC loop, tools, song parser, mailbox, MIDI, config
server/run.py      entry point usable from any directory
bridge/src/        Studio One extension source (JavaScript + XML)
bridge/build.py    builds bridge/dist/aibridge.package (for inspection; setup builds it itself)
packaging/         build_mcpb.py: Claude Desktop extension (.mcpb) into dist/
surface/           "AI Bridge" control surface definition
install/install.py older installer interface; setup/doctor/uninstall live in server/s1bridge/setup.py
re/tools/          package reader and catalog extractor (run on your own installation)
docs/              reverse-engineering notes, installer design
tests/             python3 -m unittest discover -s tests
```

## License

MIT, see [LICENSE](LICENSE). Studio One is a trademark of its owner; see
[DISCLAIMER.md](DISCLAIMER.md).
