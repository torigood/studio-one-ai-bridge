# Studio One AI Bridge: developer guide

How the bridge works, how to run it from a checkout, every tool, configuration, releases and tests.
For installing and using it, see the [README](../README.md).

**Tested only on Studio One 5.5.2, macOS (Apple Silicon / arm64).** Everything Studio One does that is not
in its shipped scripts was found by experiment and is written down in [re-findings.md](re-findings.md).

## How it works

```
AI client ──MCP (stdio, or HTTP for ChatGPT)──► MCP server (Python, standard library only)
                             ├─ song_*, library_*   read (and some write) files on disk
                             └─ daw_*               write a job to the mailbox ──┐
                                                                                 ▼
                                   ~/Music/Studio One 5/AIBridge/{inbox,outbox}
                                                                                 ▲
Studio One ── AI Bridge extension (JS) ── "AI Bridge - Run Jobs" ───────────────┘
      ▲
      └── virtual MIDI port "AI Bridge" (CC 119, ch 16) ── owned by a small MIDI daemon the MCP server starts
```

- **Live tools** (`daw_*`) need the AI Bridge extension in Studio One. The server drops a job file in a
  mailbox folder and presses a virtual MIDI button; the "AI Bridge" control surface maps that button to the
  extension's "Run Jobs" edit task, which runs the job in the open song and writes the result back. `daw_batch`
  (bridge op `batch`) runs several ops in one job.
- **Offline tools** (`song_*`, `library_*`, `audio_*`) read saved `.song` files (ZIP + XML), the plug-in list,
  presets and Sound Set file names. Some write the song file, for things the scripting API cannot create
  (CC, chords, markers, sections, sends to an existing FX channel, automation, tempo map, time signatures,
  presets, loops): if the song is open it is saved, closed (surface CC 116/115), written and reopened
  (~8 s, undo history lost), and the original is backed up first. `song_batch` does several in one reopen.
- **Audio analysis** (`audio_*`, mix checks) runs numpy code in the analysis pack's Python as a subprocess,
  so the server itself stays standard-library only.
- **ChatGPT** calls tools from OpenAI's servers, so `share` serves the same handlers over HTTP on 127.0.0.1
  behind an ngrok or Cloudflare tunnel with a secret path (`httpserver.py`, `share.py`).

## Install from a checkout (any MCP client)

Requirements: Studio One 5 on macOS, Python 3.9+ (the system `python3` works).

### 1. Get the code

```bash
git clone https://github.com/torigood/studio-one-ai-bridge.git
cd studio-one-ai-bridge
python3 -m unittest discover -s tests
```

### 2. Install the Studio One side

Quit Studio One first.

```bash
python3 server/run.py setup            # dry run: prints every file it would write
python3 server/run.py setup --apply
```

`setup` backs up what it touches into `~/Library/Application Support/studio-one-ai-bridge/backups/`, refuses
to run while Studio One is open and never modifies the Studio One application. It installs:

- the extension into `~/Library/Application Support/PreSonus/Studio One 5/Extensions/local.s1aibridge/`
- the "AI Bridge" control surface into `.../Studio One 5/User Devices/AI Bridge/`
- the device entry in `.../Studio One 5/MusicDevices.settings` that connects the surface to the "AI Bridge"
  MIDI port (what Options → External Devices → Add would write)
- a command catalog generated from **your** Studio One installation into
  `~/Library/Application Support/studio-one-ai-bridge/commands.json` (for `commands_search`; not shipped).
  Skip it with `--no-catalog`.

and starts the MIDI daemon that owns the "AI Bridge" port (fixed CoreMIDI unique ID, so Studio One finds it
again after restarts). `python3 server/run.py doctor` checks every piece; `uninstall --apply` removes them.

If `daw_ping` does not answer: `doctor`, or add the device by hand (**Options → External Devices → Add...**,
folder **local**, **AI Bridge**, **Receive From: AI Bridge**). Without the MIDI trigger
(`S1BRIDGE_MIDI=0`) each live request waits until you run **Track → AI Bridge - Run Jobs** by hand.

### 3. Register the MCP server with your AI client

```bash
python3 server/run.py connect --list
python3 server/run.py connect codex --apply      # dry run without --apply; JSON configs are backed up
```

Apps: `claude-code`, `codex`, `gemini` (Gemini CLI), `cursor`, `lmstudio`, `vscode` (GitHub Copilot agent
mode), `claude-desktop` (only without the `.mcpb`). `--remove --apply` unregisters. By hand, e.g. Claude Code:

```bash
claude mcp add studio-one -- python3 /path/to/studio-one-ai-bridge/server/run.py
```

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{"mcpServers": {"studio-one": {"command": "python3", "args": ["/path/to/studio-one-ai-bridge/server/run.py"]}}}
```

As a package: `uvx --from /path/to/studio-one-ai-bridge studio-one-ai-bridge` (the Studio One side still
comes from step 2). OpenAI Agents SDK:

```python
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

server = MCPServerStdio(params={"command": "python3", "args": ["/path/to/studio-one-ai-bridge/server/run.py"]})
async with server:
    agent = Agent(name="Producer", mcp_servers=[server])
    print((await Runner.run(agent, "Summarise my latest Studio One song")).final_output)
```

### ChatGPT sharing from a checkout

```bash
python3 server/run.py share                                       # this terminal, Cloudflare quick tunnel
python3 server/run.py share --set-ngrok-token                     # ngrok authtoken into the Keychain
python3 server/run.py share --ngrok-domain <name>.ngrok-free.app --enable   # fixed address, at login
python3 server/run.py share --status | --disable | --new-token
python3 server/run.py share --no-tunnel                           # http://127.0.0.1:8765/<secret>/mcp only
```

The background service (LaunchAgent `com.studio-one-ai-bridge.share`) restarts itself within about 20 s when
the server code changes, with the same address. Over the shared URL, `bridge_setup` and
`bridge_connect_client` are hidden and `bridge_share` can only report or turn sharing off.

## Optional pieces

### Loop length index (for `song_add_loop`)

A song refers to a Sound Set loop as `package://<sound set>/<path>`; the loop stays encrypted and Studio One
decodes it. Repeating a loop needs its length, which Studio One writes only when it saves a song using it:

1. Save and close a scratch song with an audio track (the next step adds ~2200 events to its first audio track).
2. `cd server && python3 -m s1bridge.loops fill "/path/to/that.song"`
3. Open it in Studio One, wait for it to load, **File > Save**, close it.
4. `python3 -m s1bridge.loops harvest` (lengths from all saved songs into `<data dir>/loops.json`).

Tempo comes from the song when stored, else a BPM number in the file name, else a guess (75-150 BPM).

### Analysis pack (audio analysis, melody, mix checks)

```bash
python3 server/run.py analysis-setup      # installed release: studio-one-bridge analysis-setup
```

Creates `~/Library/Application Support/studio-one-ai-bridge/analysis/` (a venv with numpy, scipy,
onnxruntime and librosa, about 420 MB) outside the app folder. `S1BRIDGE_ANALYSIS_PYTHON` can point at any
Python with numpy instead. Analysis runs at low CPU priority; results are cached in `analysis/cache/`.

- `audio_melody` tracks pitch with pYIN (librosa, ISC) and checks every note against the CREPE tiny neural
  tracker (MIT, ONNX Runtime); disagreements are marked `check`. On a hand-corrected a cappella reference pYIN
  matched 80% of notes; where both agreed 86% were right, where they disagreed 35%. Layered vocals are flagged
  with Spotify's Basic Pitch (Apache 2.0). Models ship in `server/s1bridge/models/` with their licenses;
  `tools/export_crepe_onnx.py` rebuilds the CREPE one.
- Private engines: a `.py` in `analysis/engines/` (or `S1BRIDGE_ENGINES_DIR`) defining
  `ENGINES = [{"name", "task": "f0" | "notes", "run": fn}]` adds engines for `audio_melody` (see `engines.py`).
  They stay on your Mac, so they may use models whose licenses forbid redistribution.
- Mix checks (`mix_analysis.py`): BS.1770 loudness, octave bands, masking. Studio One's Bounce To New Track
  includes the channel fader but not insert effects, so `daw_mix_fix` estimates its EQ cuts by filtering.

### VST3 parameter names

`tools/vst3dump/` builds a helper that reads parameter names of installed VST3 plug-ins (for
`daw_plugin_param` by title). It needs the Xcode command line tools and the VST3 `pluginterfaces` headers:

```bash
git clone https://github.com/steinbergmedia/vst3_pluginterfaces.git third_party/pluginterfaces
tools/vst3dump/build.sh
```

## Tools

| Tool | What it does |
|---|---|
| `song_list`, `song_summary`, `song_track_notes` | Saved songs; tempo, markers, tracks, parts, mixer; notes of a track |
| `song_analyze` | Key/range per track plus audio analysis, for arranging |
| `song_check_clashes` | Clashes with the vocal or between parts, notes outside the Chord Track chord, crowded registers, lockstep rhythm |
| `audio_analyze`, `audio_melody` | Level, spectrum, tempo, key of a WAV; notes of a sung or played line on the song's bars |
| `audio_mix_check`, `daw_mix_check`, `daw_mix_fix` | Loudness (LUFS), stereo, octave bands, masking between stems; level the stems and cut overlaps with Pro EQ |
| `library_instruments`, `library_effects`, `library_plugin_params` | Installed plug-ins with class IDs; parameter IDs |
| `library_loops`, `library_presets`, `library_sounds` | Sound Set loops; presets of an instrument; sounds across all instruments by words |
| `commands_search` | Search the generated command catalog |
| `bridge_doctor`, `bridge_setup`, `bridge_connect_client`, `bridge_share` | Check, install, connect another app, ChatGPT sharing |
| `daw_status`, `daw_ping` | Bridge health; warn when the installed bridge build differs from the server's |
| `daw_list_tracks`, `daw_add_track`, `daw_rename_track`, `daw_duplicate_track`, `daw_delete_track` | Tracks |
| `daw_list_events`, `daw_edit_event`, `daw_delete_event`, `daw_repeat_event` | Parts and clips: move, resize, mute, delete, repeat |
| `daw_insert_notes`, `daw_read_notes`, `daw_edit_notes` | Write, read and change notes |
| `daw_write_pattern`, `daw_write_drums` | Bass/pad/arp/stab parts from chords (voice-led); drums by genre or step groove |
| `daw_transform_notes`, `daw_copy_bars`, `daw_insert_bars` | Quantize, swing, humanize, transpose, velocity; copy bars; make room mid-song |
| `daw_read_chords`, `song_write_chords` | Chord Track (read live, write offline) |
| `song_write_markers`, `song_write_sections`, `song_set_time_signatures`, `song_set_tempo_map` | Markers, arranger sections, time signatures, tempo changes and ramps (offline) |
| `daw_transport`, `daw_transport_info`, `daw_set_tempo`, `daw_set_position`, `daw_set_loop` | Play/stop, transport state, tempo, playhead, loop |
| `daw_list_instruments`, `daw_add_instrument`, `daw_load_sound`, `song_load_preset` | Instruments in the song; load an instrument; instrument track + preset in one step; presets (incl. Vital `.vital`) |
| `daw_insert_effect`, `daw_list_effects`, `daw_bypass_effect`, `song_remove_effect`, `daw_apply_chain` | Effects; ready chains by role with shared reverb/delay |
| `daw_plugin_param`, `daw_scan_plugin_params`, `song_write_plugin_automation` | Plug-in parameters and their automation |
| `daw_list_mixer`, `daw_set_channel_param`, `song_write_automation`, `daw_duck` | Volume, pan, mute; mixer automation; kick ducking |
| `daw_add_send`, `daw_set_send`, `song_add_send`, `daw_add_bus`, `daw_set_output` | Sends, FX channels, buses, routing |
| `song_write_cc`, `song_add_loop`, `song_add_audio` | MIDI CC; Sound Set loops; your own audio files (offline) |
| `daw_bounce` | Render a track to audio and analyze it |
| `daw_snapshot` | Take, list and restore song snapshots |
| `daw_batch`, `song_batch` | Several live calls in one job; several song file edits with one reopen |
| `daw_command` | Run a Studio One command (File/Close, Quit, Revert, New, Open blocked) |
| `daw_inspect_track`, `daw_inspect_mixer`, `daw_probe`, `daw_dump_host`, `daw_call` | API discovery for development; listed only with `S1BRIDGE_DEV_TOOLS=1` (`daw_call` can edit the song) |

Shared conventions (song path, track by name or index, beats vs bars, `replace`, offline writes) are explained
once in the server instructions (`__main__.INSTRUCTIONS`), not in every schema.

## Configuration

Paths live in [config.py](../server/s1bridge/config.py) and can be overridden: `S1BRIDGE_APP`,
`S1BRIDGE_SETTINGS_DIR`, `S1BRIDGE_USER_CONTENT`, `S1BRIDGE_SONGS`, `S1BRIDGE_SOUNDSETS`, `S1BRIDGE_PRESETS`,
`S1BRIDGE_MAILBOX`, `S1BRIDGE_DATA_DIR`, `S1BRIDGE_CATALOG`, `S1BRIDGE_ANALYSIS_PYTHON`, `S1BRIDGE_MIDI`,
`S1BRIDGE_DEV_TOOLS`. The mailbox must be `<Studio One user content>/AIBridge`, because the extension opens
`local://$USERCONTENT/AIBridge`.

## Versions and releases

- **CalVer `YY.W.N`**: two-digit year, week of the year counted Sunday to Saturday (week 1 holds January 1),
  and the release number within that week (0, 1, ...). No zero padding. The version is written only in
  [server/s1bridge/version.py](../server/s1bridge/version.py) (and mirrored in `pyproject.toml`).
- **Bridge build id**: the bridge sources carry `@VERSION@` / `@BUILD@`; `setup` fills in the release and a hash
  of `bridge/src`. `daw_status`, `daw_ping` and `doctor` ask for a reinstall only when that hash differs, so
  a release that does not touch the bridge needs no Studio One restart.
- **Release**: `python3 packaging/release.py` computes the next version, runs the tests, builds and validates
  `dist/studio-one-ai-bridge-<version>.mcpb`. With `--publish --notes notes.md` it also commits, pushes and
  creates the GitHub release with the bundle and its SHA-256. The one-line installer fetches the latest release.

## Tests

```bash
python3 -m unittest discover -s tests        # or python3 -m pytest -q
```

Tests run against fake bridges, a node mock of the Studio One host (`tests/mock_host.js`, needs node) and
small generated songs; numpy-based tests skip without numpy (run them with the analysis pack's Python).
Nothing in the tests touches a real Studio One install.

## Windows (untested)

The code has best-guess Windows defaults but has never run there. The automatic trigger will not work as is:
`midi.py` creates the virtual port with macOS CoreMIDI. Windows would need a virtual MIDI port such as
[loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) named "AI Bridge" and code that sends CC 119
on channel 16 to it. Contributions welcome.

## Third-party software in the .mcpb

The bundle includes CPython 3.12 from [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
(Python Software Foundation License; the builds include OpenSSL, SQLite, zlib and libffi under their own
licenses) and the analysis models with their licenses. The repository's own code is MIT.

## Repository layout

```
server/s1bridge/   MCP server: JSON-RPC loop, tools, song parser and writers, mailbox, MIDI, analysis
server/run.py      entry point usable from any directory
bridge/src/        Studio One extension (JavaScript + XML)
surface/           "AI Bridge" control surface definition
packaging/         build_mcpb.py (.mcpb with Python), release.py (CalVer release), start.sh launcher
install/           install.sh: one-line installer
re/tools/          package reader and catalog extractor (run on your own installation)
tools/             vst3dump, CREPE export
docs/              this guide, ChatGPT guide, reverse-engineering notes, design notes
tests/             unit tests
```
