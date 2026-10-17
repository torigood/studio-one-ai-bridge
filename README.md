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
      └── virtual MIDI port "AI Bridge" (CC 119, ch 16) ── created by the MCP server
```

- **Offline tools** (`song_*`, `library_*`, `audio_analyze`) need only Python. They read saved
  `.song` files, the plug-in list and Sound Set file names. They never write.
- **Live tools** (`daw_*`) need the AI Bridge extension installed in Studio One. The server
  drops a job file in a mailbox folder and presses a virtual MIDI button; a small control
  surface maps that button to the extension's "Run Jobs" command, which runs the job in the open
  song and writes the result back.

## Requirements

- Studio One 5 (tested 5.5.2) on macOS
- Python 3.9+ (the macOS system `python3` works). No third-party packages, except `numpy`
  for the optional audio analysis.
- An MCP client: Claude Code, Claude Desktop, the OpenAI Agents SDK, ...

## Install

### 1. Get the code

```bash
git clone https://github.com/<you>/studio-one-ai-bridge.git
cd studio-one-ai-bridge
python3 -m unittest discover -s tests
```

### 2. Install the Studio One side

Quit Studio One first.

```bash
python3 bridge/build.py
python3 install/install.py --surface
python3 install/install.py --surface --apply
```

`install.py` is a dry run unless you pass `--apply`. It prints every file it will write, backs up
what it touches into `install/backups/`, and never modifies the Studio One application itself.
It installs:

- the extension into `~/Library/Application Support/PreSonus/Studio One 5/Extensions/local.s1aibridge/`
- the "AI Bridge" control surface into `.../Studio One 5/User Devices/AI Bridge/` (`--surface`)
- a command catalog generated from **your** Studio One installation into
  `~/Library/Application Support/studio-one-ai-bridge/commands.json` (used by `commands_search`;
  it is not shipped with this repository). Rebuild it alone with
  `python3 install/install.py --catalog --apply`.

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

### 4. Connect the control surface (once)

1. Start your AI client first, so the MCP server is running and has created the virtual MIDI
   port **AI Bridge**. (Alternatively keep `python3 server/midi_port.py` running.)
2. Start Studio One.
3. **Studio One → Options → External Devices → Add...**, open the folder **local**, choose
   **AI Bridge**, set **Receive From** to **AI Bridge**, OK.
4. Open a song and ask the AI to run `daw_ping`.

If the MIDI trigger is not set up, each live request waits until you run
**Track → AI Bridge - Run Jobs** by hand. Set `S1BRIDGE_MIDI=0` to disable the virtual port.

### Optional: audio analysis

`audio_analyze` and the audio part of `song_analyze` need numpy. Either create `.venv` in the
checkout (`python3 -m venv .venv && .venv/bin/pip install numpy`), install the package with the
extra (`pip install "studio-one-ai-bridge[analysis]"` / `uvx --from "/path[analysis]" ...`), or
point `S1BRIDGE_ANALYSIS_PYTHON` at any Python that has numpy.

### Uninstall

```bash
python3 install/install.py --surface --uninstall --apply
```

Then remove the device under Options → External Devices.

## Tools

| Tool | What it does |
|---|---|
| `song_list` | Saved songs, newest first |
| `song_summary` | Tempo, markers, tracks, parts, mixer state of a saved song |
| `song_track_notes` | Notes of one track in a saved song |
| `song_analyze` | Key/range per track plus audio analysis, for arranging |
| `audio_analyze` | Level, spectrum balance, tempo and key estimate of a WAV file (numpy) |
| `library_instruments`, `library_effects` | Installed plug-ins with class IDs |
| `library_plugin_params` | Parameter IDs of native PreSonus plug-ins (from their presets) |
| `library_loops` | Loop names in installed Sound Sets (names only) |
| `commands_search` | Search the generated command catalog |
| `daw_status`, `daw_ping` | Bridge health |
| `daw_list_tracks`, `daw_list_mixer` | Tracks and mixer channels of the open song |
| `daw_add_track`, `daw_insert_notes` | Add tracks, write a part with notes |
| `daw_add_instrument`, `daw_insert_effect` | Load instruments and insert effects |
| `daw_set_channel_param` | Volume / pan |
| `daw_plugin_param`, `daw_scan_plugin_params` | Read/set plug-in parameters |
| `daw_command` | Run a Studio One command (dangerous ones blocked) |
| `daw_transport` | Play / stop |
| `daw_inspect_*`, `daw_probe`, `daw_dump_host` | Read-only API discovery, for development |

Tools marked `EXPERIMENTAL` in their description use API details that are not fully verified.

## Safety

- Save your song before letting an AI edit it. Each bridge operation is a normal Studio One edit
  (Edit → Undo works), but the bridge acts on whatever song is open.
- Offline tools open files read-only. Sound Set contents are never decrypted or read; only their
  file names are listed.
- `daw_command` refuses File/Close, Quit, Revert, New and Open.
- Nothing is written into Studio One folders without `install.py --apply`, and the application
  bundle is never modified unless you explicitly choose the `--target app-scripts` option.
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
bridge/build.py    builds bridge/dist/aibridge.package
surface/           "AI Bridge" control surface definition
install/install.py installer (dry run by default)
re/tools/          package reader and catalog extractor (run on your own installation)
docs/              reverse-engineering notes
tests/             python3 -m unittest discover -s tests
```

## License

MIT, see [LICENSE](LICENSE). Studio One is a trademark of its owner; see
[DISCLAIMER.md](DISCLAIMER.md).
