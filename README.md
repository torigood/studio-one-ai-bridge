# Studio One AI Bridge

English | [한국어](README.ko.md)

Let an AI assistant (Claude, Codex/OpenAI agents, Gemini CLI, or any [MCP](https://modelcontextprotocol.io)
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
  write and reopen it by themselves (~8 s, undo history is lost). Run setup again after updating,
  or the close step cannot happen.
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

1. Download `studio-one-ai-bridge-<version>.mcpb` from
   [Releases](https://github.com/torigood/studio-one-ai-bridge/releases) (or build it with
   `python3 packaging/build_mcpb.py`, it lands in `dist/`) and double-click it. Claude Desktop shows the extension; click **Install**.
2. Save your song and quit Studio One, then ask Claude: "Set up Studio One." It runs
   `bridge_setup`, which installs the extension, the control surface and its device entry.
3. Start Studio One, open a song, and ask Claude for something, e.g. "Add a piano track".

The extension brings its own Python (arm64 and Intel), so nothing else has to be installed. The settings of the extension let you
change the Studio One app path and the user data folder (Preferences → Locations in Studio One).

## ChatGPT (chat app)

**Step-by-step guide (Korean): [docs/chatgpt-guide.md](docs/chatgpt-guide.md).** Without a checkout or
Claude Desktop, one command installs the release (with its own Python), the Studio One side and
background sharing, and copies the connector URL (Studio One must be quit):

```bash
curl -fsSL https://raw.githubusercontent.com/torigood/studio-one-ai-bridge/main/install/install.sh | sh
```

It adds a `studio-one-bridge` command (`studio-one-bridge share --status`, `studio-one-bridge doctor`).
`sh -s -- --uninstall` removes it again. The rest of this section is the same from a checkout.

The ChatGPT app connects only to MCP servers on a public HTTPS address, so it cannot start this
server itself. `share` runs the server over HTTP on 127.0.0.1 and puts a free Cloudflare quick tunnel
(cloudflared, no account; downloaded once if it is not installed) in front of it:

```bash
python3 server/run.py share
```

It prints a connector URL `https://<words>.trycloudflare.com/<secret>/mcp`. In ChatGPT (Plus/Pro):
Settings → Apps & Connectors → Advanced → Developer mode on → Create (custom MCP server): name
`Studio One`, server URL = that URL, authentication **none**, confirm the warning. Then enable it in a
chat and ask, e.g., "Studio One에서 daw_ping 해줘".

- Anyone with the full URL can control Studio One and read your songs while `share` runs. Keep the
  URL private, stop with Ctrl+C when done, and run `share --new-token` if it leaked (the secret is
  kept in `<data dir>/share_token`).
- The quick tunnel address changes on every start: edit the connector's URL after restarting, or use
  a fixed ngrok address (below).
- `share --no-tunnel` serves only on `http://127.0.0.1:8765/<secret>/mcp` for local MCP-over-HTTP clients.

Why only ChatGPT needs this: Claude Desktop (also its chat) and Codex start the server on your Mac and
relay tool calls themselves; the ChatGPT chat calls connector tools from OpenAI's servers.

**In the background, on and off**

```bash
python3 server/run.py share --enable    # share now and at every login (LaunchAgent)
python3 server/run.py share --status    # on/off and the connector URL
python3 server/run.py share --disable   # stop, also at login
```

From Claude you can say "share Studio One with ChatGPT" / "stop sharing" (`bridge_share`).
In the background it restarts by itself within about 20 seconds when the server code changes (an
update, `git pull`), with the same address, so the ChatGPT connector needs no change. New tools show
up in ChatGPT after refreshing the connector or in a new chat.

**A fixed address with ngrok (free account)**: sign up at ngrok.com, copy *Your Authtoken*, and create
your free static domain under *Domains*. Then:

```bash
python3 server/run.py share --set-ngrok-token                     # paste the authtoken (stored in the Keychain)
python3 server/run.py share --ngrok-domain <name>.ngrok-free.app  # use it from now on
python3 server/run.py share --enable
```

The connector URL is then `https://<name>.ngrok-free.app/<secret>/mcp` and stays the same, so the ChatGPT
connector is set up once. `share --provider cloudflare` switches back.

## Install from a checkout (any MCP client)

### 1. Get the code

```bash
git clone https://github.com/torigood/studio-one-ai-bridge.git
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

Let setup do it (dry run without `--apply`; JSON configs are backed up first):

```bash
python3 server/run.py connect --list
python3 server/run.py connect codex --apply
```

Apps: `claude-code`, `codex` (OpenAI; included with ChatGPT plans), `gemini` (Gemini CLI),
`cursor`, `lmstudio`, `vscode` (GitHub Copilot agent mode), `claude-desktop` (only if you do
not use the `.mcpb`). `--remove --apply` unregisters. From the Claude Desktop extension, ask
Claude to "connect Codex too" (`bridge_connect_client`); the other app then runs the
extension's bundled Python. The ChatGPT chat app accepts only remote HTTPS MCP servers and
cannot start this local server; use Codex with a ChatGPT plan instead.

Or by hand. Claude Code:

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

If `daw_ping` does not answer, run `python3 server/run.py doctor`. To add the device by hand instead:
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

### Optional: analysis pack (melody from audio)

`audio_melody` turns a sung or played line in audio into notes on the song's bars. It tracks pitch
with pYIN (librosa, ISC) and, by default, checks every note against the CREPE tiny neural tracker
(MIT, ONNX Runtime): notes where the two disagree are marked `check` with the other engine's pitch.
On a hand-corrected K-pop a cappella reference, pYIN matched 80% of notes; where both engines agreed
86% were right, where they disagreed only 35%, so the marks point at the notes worth a listen.
`quality: "fast"` skips the cross-check. 75 s of vocal takes about 6 s on an M4. Install it once:

```bash
studio-one-ai-bridge analysis-setup
```

This makes `~/Library/Application Support/studio-one-ai-bridge/analysis/` (a venv with numpy, scipy,
onnxruntime and librosa, about 420 MB, plus the 2 MB CREPE tiny model) outside the app folder, so updates
keep it. To build the model yourself instead of downloading it: `python tools/export_crepe_onnx.py DIR`
(needs torch and torchcrepe), then `studio-one-ai-bridge analysis-setup --model-dir DIR`.

Layered vocals (doubles, harmonies) are checked with Spotify's Basic Pitch model (Apache 2.0, shipped in
`server/s1bridge/models/` with its NOTICE): each note moves to the octave that sounds loudest, other
pitches under it are listed as `also`, and `layered` lists the spans with two or more voices, where a
single-line result deserves a listen. Analysis runs at low CPU priority so playback keeps going, and
results are cached per file and range in `analysis/cache/`.

Private engines: a `.py` file in `analysis/engines/` (or `S1BRIDGE_ENGINES_DIR`) that defines
`ENGINES = [{"name", "task": "f0" | "notes", "run": fn}]` adds engines for `audio_melody`'s `engine`
argument (see `server/s1bridge/engines.py`). They stay on your Mac, so they can use models whose
licenses do not allow redistribution.

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
| `song_check_clashes` | Arrangement check against the vocal and the Chord Track: semitone/minor-9th clashes with the vocal or between parts, held notes outside the chord (avoid notes), parts crowding the vocal's register, parts in lockstep rhythm. The vocal can be an audio track (analysed with `audio_melody`) or a melody track |
| `audio_melody` | Notes of a vocal or solo line in audio, placed on the song's bars; can write them to an instrument track (analysis pack) |
| `library_instruments`, `library_effects` | Installed plug-ins with class IDs |
| `library_plugin_params` | Parameter IDs of native PreSonus plug-ins (from their presets) |
| `library_loops` | Loops in installed Sound Sets, with length/BPM once indexed |
| `commands_search` | Search the generated command catalog |
| `bridge_doctor` | Check every piece of the Studio One side and say how to fix what is missing |
| `bridge_setup` | Install or update the Studio One side (same as `setup --apply`; Studio One must be quit) |
| `bridge_connect_client` | Register this server with another AI app (Codex, Claude Code, Gemini CLI, Cursor, LM Studio, VS Code) |
| `bridge_share` | Turn ChatGPT sharing on or off, show the connector URL |
| `daw_status`, `daw_ping` | Bridge health; both warn when the installed bridge does not match the server |
| `daw_list_tracks`, `daw_list_mixer`, `daw_list_instruments` | Tracks, mixer channels and instruments of the open song |
| `daw_add_track`, `daw_insert_notes` | Add tracks, write a part with notes |
| `daw_rename_track`, `daw_duplicate_track`, `daw_delete_track` | Rename (with its instrument channel), duplicate with all parts/clips, delete |
| `daw_read_notes`, `daw_edit_notes` | Read the open song's notes; change, add, delete notes |
| `daw_read_chords`, `song_write_chords` | Chord track (read live, write offline with auto reopen) |
| `daw_transport_info`, `daw_set_tempo`, `daw_set_position`, `song_set_time_signatures` | Tempo, playhead, time signatures |
| `daw_set_loop` | Loop range by bars or beats, loop on/off |
| `song_write_markers` | Named song sections (Intro, Verse, Chorus...) in the Marker Track (offline with auto reopen) |
| `daw_add_send`, `daw_set_send` | New FX channel with an effect (reverb, delay...) and a send to it; send level and pan |
| `song_add_send` | Send to an FX channel or bus that already exists (offline with auto reopen) |
| `daw_add_bus`, `daw_set_output` | Group channels into a new bus; route a channel to a bus, FX channel or Main |
| `song_write_automation` | Volume, pan or mute automation (fades, rides) for a track (offline with auto reopen; sets the channel to Read) |
| `song_write_sections` | Arranger sections (Intro, Verse, Chorus...) shown on the Arranger Track (offline with auto reopen) |
| `song_set_tempo_map` | Tempo changes and ramps (ritardando) through the song (offline with auto reopen) |
| `song_add_audio` | Place your own WAV/AIFF (sample, vocal) on an audio track; copied into the song's Media folder (offline) |
| `song_write_plugin_automation` | Automate an effect or instrument parameter, e.g. a filter sweep (offline with auto reopen) |
| `library_presets`, `song_load_preset` | Find and load instrument presets (Mai Tai, Mojito, Impact, SampleOne, .vstpreset/.aupreset files; offline) |
| `daw_bounce` | Render a track to audio and analyze it |
| `daw_list_events`, `daw_repeat_event`, `daw_edit_event`, `daw_delete_event` | Events on a track; repeat a loop or part back to back; move, resize, mute; delete |
| `daw_add_instrument`, `daw_insert_effect`, `daw_list_effects`, `daw_bypass_effect`, `song_remove_effect` | Load instruments; insert, list, bypass and remove effects (removal is offline with auto reopen) |
| `daw_set_channel_param` | Volume / pan (instrument tracks: their instrument channel is found automatically, saving the song if needed) |
| `daw_plugin_param`, `daw_scan_plugin_params` | Read/set plug-in parameters |
| `daw_command` | Run a Studio One command (dangerous ones blocked) |
| `daw_transport` | Play / stop |
| `daw_inspect_track`, `daw_inspect_mixer`, `daw_probe`, `daw_dump_host` | Read-only API discovery, for development (listed only with `S1BRIDGE_DEV_TOOLS=1`) |
| `daw_call` | Call script methods with tracks/events as arguments (API experiments; can edit the song; listed only with `S1BRIDGE_DEV_TOOLS=1`) |

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

## Third-party software in the .mcpb

The Claude Desktop extension bundles CPython 3.12 from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone) (Python Software
Foundation License, see `python/darwin-*/lib/python3.12/LICENSE.txt` in the bundle; the builds
include libraries such as OpenSSL, SQLite, zlib and libffi under their own licenses). This
repository itself contains no third-party code.

## Repository layout

```
server/s1bridge/   MCP server: JSON-RPC loop, tools, song parser, mailbox, MIDI, config
server/run.py      entry point usable from any directory
bridge/src/        Studio One extension source (JavaScript + XML)
bridge/build.py    builds bridge/dist/aibridge.package (for inspection; setup builds it itself)
packaging/         build_mcpb.py: Claude Desktop extension (.mcpb, with Python) into dist/; start.sh launcher
surface/           "AI Bridge" control surface definition
install/           install.sh: one-line installer; install.py: older installer interface
re/tools/          package reader and catalog extractor (run on your own installation)
docs/              reverse-engineering notes, installer design
tests/             python3 -m unittest discover -s tests
```

## License

MIT, see [LICENSE](LICENSE). Studio One is a trademark of its owner; see
[DISCLAIMER.md](DISCLAIMER.md).
