# Studio One AI Bridge

English | [한국어](README.ko.md)

Make music in **PreSonus Studio One 5** by talking to an AI: Claude, ChatGPT, Codex or any
[MCP](https://modelcontextprotocol.io) app. Say "add a walking bass over Am F C G" or "the pad is muddy, fix the
mix", and the AI does it in your open song.

> Unofficial. Not affiliated with PreSonus or Fender. See [DISCLAIMER.md](DISCLAIMER.md).
> Tested on Studio One 5.5.2 on macOS (Apple Silicon). Other versions, Intel Macs and Windows are untested.

## What you can ask

| | For example |
|---|---|
| **Write parts** | "Add a pad and a walking bass over Am F C G, 8 bars" · "Put a trap beat on the drum track with a fill every 4 bars" |
| **Edit** | "Quantize the hi-hats and add some swing" · "Transpose the chorus up a tone" · "Copy the verse to bar 33" · "Make room for 8 more bars at bar 17" |
| **Sounds** | "Find me a warm pad and load it" (Vital presets too) · "Put a vocal chain on the vocal" · "Make the bass duck under the kick" |
| **Mix** | "Check the mix" (loudness, what clashes with what) · "Fix the mix" (levels, EQ cuts where tracks overlap) |
| **Arrange** | "Write Intro, Verse, Chorus markers" · "Slow down to 90 BPM at bar 33" · "Write the chords on the Chord Track" |
| **Listen and analyze** | "What key is this song in?" · "Turn the sung melody into notes" · "Which parts clash with the vocal?" |
| **Go back** | "Take a snapshot first" … "Undo everything since the snapshot" |

The AI also reads your saved songs, lists your instruments, effects, presets and loops, and sets volume, pan,
sends and plug-in parameters. The full list of tools is in the [developer guide](docs/DEVELOPMENT.md#tools).

## Install

Pick the app you use:

| App | How | Notes |
|---|---|---|
| **Claude Desktop** | Double-click the `.mcpb` | Easiest. Nothing else to install |
| **ChatGPT** (web, desktop app) | One command + a connector | Plus/Pro. [Step-by-step guide (Korean)](docs/chatgpt-guide.md) |
| **Codex, Claude Code, Gemini CLI, Cursor, LM Studio, VS Code** | One command + `connect` | See below |

### Claude Desktop

1. Download `studio-one-ai-bridge-<version>.mcpb` from
   [Releases](https://github.com/torigood/studio-one-ai-bridge/releases), double-click it, click **Install**.
2. Save your song and **quit Studio One**, then ask Claude: "Set up Studio One."
3. Start Studio One, open a song and ask: "Check the connection to Studio One."

### ChatGPT

Quit Studio One, open Terminal (Applications > Utilities) and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/torigood/studio-one-ai-bridge/main/install/install.sh | sh
```

It installs everything (Python included), asks how to share with ChatGPT (a free ngrok account gives a fixed
address) and copies the connector URL. Then in ChatGPT: Settings → Apps & Connectors → Advanced →
Developer mode → Create, name `Studio One`, paste the URL, authentication **none**. The
[guide](docs/chatgpt-guide.md) walks through every screen.

### Codex and other apps

Run the same command (skip the ChatGPT question), then connect your app:

```bash
studio-one-bridge connect codex --apply     # or claude-code, gemini, cursor, lmstudio, vscode
```

## First steps

1. Open a song in Studio One (save it once, so it has a file).
2. Ask "Check the connection to Studio One." The AI answers with the bridge version.
3. Ask for something small: "Add a piano track and play a C major chord in bar 1."

Live edits are normal Studio One edits: **Edit → Undo** works. A few things can only be written into the song
file (chords, markers, CC, some sends, automation, presets, tempo changes); for those the song is saved, closed
and reopened by itself in about 8 seconds, and the undo history is lost. Ask the AI to **take a snapshot** before
larger changes, then you can go back in one step.

## Updating

- **Claude Desktop**: download the new `.mcpb` from [Releases](https://github.com/torigood/studio-one-ai-bridge/releases) and double-click it.
- **ChatGPT and other apps**: run the install command again. ChatGPT picks up new tools after you refresh the
  connector or start a new chat.
- When an update changes the part inside Studio One, the AI says so: quit Studio One and ask it to
  "set up Studio One" (or run `studio-one-bridge setup --apply`).

Versions are dates: `26.48.0` = 2026, week 48, first release that week.

## Safety

- The AI acts on whatever song is open. Save before you start; snapshots and backups help you go back
  (backups of every file edit are kept in `~/Library/Application Support/studio-one-ai-bridge/backups/`).
- While ChatGPT sharing is on, anyone with the connector URL can control Studio One and read your songs. Keep it
  private; `studio-one-bridge share --new-token` makes a new one, `share --disable` turns sharing off.
- Nothing is installed into Studio One without your setup command, and the Studio One app itself is never changed.
- Closing, quitting or discarding the song is blocked for the AI.

## Troubleshooting

| Problem | Try |
|---|---|
| The AI says Studio One does not answer | Is a song open, and no dialog open in Studio One? Then `studio-one-bridge doctor` (or ask "run the bridge doctor"); it says how to fix each piece |
| "Quit Studio One and run setup again" | Save, quit Studio One, ask "set up Studio One", start Studio One |
| New tools do not show up in ChatGPT | Refresh the connector or start a new chat |
| A mix check is silent for a track | The instrument makes no sound in a bounce (try playing it in Studio One) |
| Melody or mix analysis is not available | Install the analysis pack once: `studio-one-bridge analysis-setup` (about 420 MB) |

## Limits

- Studio One 5 on macOS only so far. Windows needs a virtual MIDI port and a test machine.
- Presets inside some plug-ins' own browsers (Surge XT, BBC Symphony Orchestra) cannot be loaded; Studio One
  presets, Vital `.vital` presets and `.vstpreset` / `.aupreset` files can.
- The mix check hears the channel fader but not the channel's insert effects; EQ cuts it makes are estimated.

## Uninstall

Quit Studio One, then `curl -fsSL https://raw.githubusercontent.com/torigood/studio-one-ai-bridge/main/install/install.sh | sh -s -- --uninstall`
(or `studio-one-bridge uninstall --apply` to remove only the Studio One side).

## For developers

How it works, running from a checkout, every tool, configuration, releases and tests:
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). Reverse-engineering notes: [docs/re-findings.md](docs/re-findings.md).

## License

MIT, see [LICENSE](LICENSE). Studio One is a trademark of its owner; see [DISCLAIMER.md](DISCLAIMER.md).
