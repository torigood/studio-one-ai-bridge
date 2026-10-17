# Disclaimer

Studio One AI Bridge is an independent, unofficial project. It is not affiliated with,
endorsed by, or supported by PreSonus Audio Electronics or Fender Musical Instruments
Corporation. "Studio One" and "PreSonus" are trademarks of their respective owners and are
used here only to describe what this software works with.

- **Interoperability.** The bridge was built by studying how a legally obtained copy of
  Studio One 5 behaves and which file formats and scripting interfaces it uses, for the sole
  purpose of making it work together with independent software (AI assistants over MCP).
  Notes are in `docs/re-findings.md`.
- **No PreSonus files are distributed.** This repository contains no code, scripts,
  presets, device definitions or other files copied from Studio One. The command catalog used
  by `commands_search` is generated on your own machine from your own installation by
  `install/install.py` and is never committed.
- **Sound Sets are never decrypted.** Only the unencrypted directory (file names) of
  installed `.soundset` files is read so loops can be listed by name. Their encrypted
  contents are not decrypted, extracted or copied; Studio One itself loads them.
- **No license or copy protection is bypassed.** The bridge needs a working, licensed
  Studio One and loads as an ordinary user extension.
- **Use at your own risk.** The bridge edits the song that is open in Studio One. Save your
  work and keep backups. The software is provided "as is", without warranty (see `LICENSE`).
