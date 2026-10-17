# Studio One 5 — RE findings

Target: `/Applications/Studio One 5.app` (universal x86_64/arm64, build 2022-04-01).
Method: static only (strings, resource unpacking, REA `inspect_macho`). Nothing modified.

Legend: **[O]** observed in shipped files · **[I]** inference · **[?]** unknown

## Architecture
- [O] Native C++ on PreSonus "CCL" framework: `Frameworks/ccl{gui,net,security,system,text}.framework`, `ucnet.framework`.
- [O] `MacOS/studioapp` 51 MB, stripped (1 export). Decompiling low value; strings/resources more useful.
- [O] `PlugIns/jsengine.bundle` statically links SpiderMonkey from `firefox-85.0.2` (build path string). Modern JS (class, let/const) used in shipped scripts.
- [O] ~35 feature bundles in `PlugIns/` (vstservice, auservice, araservice, remoteservice, musicdevices, ...).

## Surface 1 — JS script packages (`Contents/scripts/*.package`)
- [O] Container: `PACKAGEF` magic, zlib blobs, trailing directory at last `Root\x01\0\0\0`.
  Directory entry `File`: u32, UTF-16LE name + `\0\0`, 8-byte date, 1 flag byte, u64 offset, u64 compressed size, u64 raw size.
  `Fold` entries not fully decoded yet (see `re/tools/s1pkg.py`).
- [O] Some extension packages are plain ZIP (e.g. `Extensions/studioonemacros/scripts/macros.package`).
- [O] `classfactory.xml` registers `ScriptClass` with `category` = `EditTask` | `FrameworkService` | `EditAddIn` | `Gadget` | `ExtensionHandler`, `sourceFile`, `functionName`.
- [O] `metainfo.xml`: `Package:ID`, `Security:ClassAuthorization`.
- [O] API used by shipped code:
  - `context.mainTrackList.getTrack(i)`, `track.mediaType`, `track.channel.findParameter(..)`, `track.channel.recordUnit.recordArmed`
  - `context.functions.{addMediaTrack, addTrack, moveEvent, resizeEvent, renameEvent, muteEvent, modifyPitch, modifyVelocity, quantizeEvent, insertNewClip, addMusicPart, createCrossFades, removeTrack, duplicateTrack, beginMultiple, endMultiple, executeImmediately}`
  - `context.parameters.addInteger/addString`, `context.runDialog()`, `context.iterator`, `context.editor`
  - `Host.GUI.Commands.{interpretCommand, findCommand, registerCommand, assignKey, lookupBindings}`
  - `Host.IO.{File, findFiles, openTextFile, createTextFile, openPackage, XmlTree}`, `Host.Url`
  - `Host.Signals.{signal, advise, unadvise}`, `Host.Objects.{registerObject, getObjectByUrl}`, `Host.Services.getInstance`
  - `Host.Engine.TrackFormats.findEqual("Mono"|"Stereo"|"Instrument")`
- [O] Object URLs: `object://studioapp/DocumentManager/ActiveDocument/...`, `object://hostapp/Configuration`.
- [I] `cclnet` exposes `Network`, `WebClient`, `XMLHttpRequest` names to scripting; `cclgui` has `addIdleTask`. Possible HTTP + polling from JS. **[?] not verified.**

## Surface 2 — Macros
- [O] `.studioonemacro` XML: `<Macro><CommandElement category=".." name=".."><CommandArgument name value/></CommandElement></Macro>`.
- [O] Executor `elements.js` calls `Host.GUI.Commands.interpretCommand(category, name, false, args)`.
- [O] `Resources/commands.xml` lists only 272 key-bound commands; full catalog lives in binaries. **[?]**

## Surface 3 — Control surfaces (`Contents/devices/**`)
- [O] `.surface.xml` mappings + TS-compiled JS; SDK `resource://com.presonus.musicdevices/sdk/controlsurfacecomponent.js`.
- [O] Elements: `<Command control command.category command.name command.args>`, `<Value param="volume|pan|mute|solo|recordArmed|...">`, `<Invoke onReceive>`, `Bank target="RemoteBank|MasterBank|Sends|Inserts|FocusChannel|MacroControls"`.
- [O] 155 distinct command bindings across shipped devices.

## Surface 4 — Remote (UCNet)
- [O] `remoteservice.bundle` + `ucnet.framework`: UDP discovery, TCP session, `application/json` / `application/ubjson`. Remote app treated as virtual surface (`remotedevice.surface.xml`).
- [I] Proprietary, undocumented. Not chosen as primary path.

## Surface 5 — Document format
- [O] `.song` = ZIP: `Song/song.xml` (MediaTrack, FolderTrack, MusicPart, TempoMap, TimeSignatureMap, MarkerTrack, ArrangerTrack, ChordTrack), `Song/mediapool.xml` (MusicClip/AudioClip → `media:///...`), `Devices/audiomixer.xml`, `mixerconsole.xml`, ..., `Performances/**/*.musicx`.
- [O] `.musicx` = UBJSON `{timeFormat, events:[{start, pitch, noteId, length, velocity(0..1)}]}`.

## Security / loading (step 1)
Method: Hopper via REA timed out (analysis 2'53" > 120 s bridge wait; Hopper is Demo). Fallback: `lipo -thin arm64` copy + `llvm-objdump` + ADRP/ADD string xref script + `dyld_info -fixups`. Addresses = arm64 slice.

- [O] `Host.Security.checkAccess(packageID, featureName)` used for edition gating (e.g. "Add Folder Track"), not code signing.
- [O] App's own `Contents/scripts/*.package` (PACKAGEF) and `Extensions/studioonemacros` (plain ZIP) carry **no** `signature.xml`.
- [O] User-installed extension `~/Library/Application Support/PreSonus/Studio One 5/Extensions/<id>/` carries `signature.xml` = `<PackageSignature><SignedData>` base64 list of `path: sha1-base64` lines + signature.
- [O] Package verifier strings at 0x1012f7e17.. : `PackageSignature`, `PackageSigner`, `PackageVerifier`, `Signature does not match for public key`, `Package vendor name does not match with public token`, `JWT`, `createPrivateTokenWithParentKey`, `sign(outPath, inPath, progress)`.
- [O] Verifier routine 0x10111993c; only direct caller is wrapper 0x1001aa038 (returns result unchanged); wrapper tail-called from 0x1001af94c (`x1 = [this+0x1a8]`). No vtable/fixup reference to 0x1001af94c found → real caller path **[?]**.
- [O] Server endpoints: `user/get_developer_token` (+ `vendorName`), `user/extensions/activate`. Account field `isDeveloper` near `AuthorizationPolicy` / `unrestricted` / `restricted-1` / `permissions`.
- [O] `(Developer Mode)` is an Extension Manager UI label (next to `(disabled)`, uninstall/update strings).
- [I] Chain: PreSonus authority → per-vendor developer token (JWT, from account flagged `isDeveloper`) → signs extension. "Developer Mode" = state shown for such dev extensions.
- [O] `noSignatures` is a **false lead**: time-ruler option (hide signature track), not package signing.
- [O] `Scanning Scripts...` / `Script folder not found at:` are in app startup; script folder = app `scripts/` (+ extensions' `scripts/`). No separate user Scripts path string found.
- [O, public] Community scripts (2017–2019 era) are zipped folders renamed `.package`, dropped into the app's `Scripts` folder, restart to load (Gearspace, Cockos forum, github.com/DjFix/studioone_functions). Implies unsigned ZIP packages in app `scripts/` load.
- [?] Whether an unsigned **extension** in the user Extensions folder loads, or is rejected / needs Developer Mode.
- [!] Putting files into `/Applications/Studio One 5.app` breaks the app's code signature (macOS). Needs explicit user decision later.

## API details used by the bridge (from shipped scripts)
- [O] `$USERCONTENT` = `~/Music/Studio One 5` (`local://$USERCONTENT/Macros` ↔ existing `Macros` folder).
- [O] Files: `Host.IO.findFiles(urlFolder, "*.ext")` → iterator `done()/next()`; `url.getName(false)`; `Host.IO.openTextFile(url)` → `endOfStream/readLine()/close()`; `Host.IO.createTextFile(url)` → `writeLine/close`; `Host.IO.File(url).exists()/copyTo/moveTo`.
- [O] Command args: flat array `[name, value, ...]` passed to `interpretCommand` (macros/elements.js).
- [O] Run another task: `context.editor.environment.runEditTask(Host.UID(classDescription.classID), args)`; `Host.Classes.getClassDescription("EditTask:<subCategory>:<name>")`.
- [O] New part: `functions.insertNewClip("Music", start, end, track)`; time objects from `functions.newMediaTime()` with `.musical`.
- [O] New note (musicedit/Util.js): `createEvent("Note")`, `insertEvent(part, note)`, `modifyPitch`, `modifyVelocity`, `freezeVelocity`, `resizeEvent(note, len)`, `moveEvent(note, start)`; music functions via `functions.root.createFunctions("MusicFunctions")`.
- [O] Channel param: `track.channel.findParameter("transpose").setValue(v, true)`.
- [?] No timer/idle API seen in any shipped JS → bridge is triggered by an edit task for now.

## Song format details (observed)
- `Song/song.xml`: one flat `List x:id="Tracks"` under `Attributes x:id="Root"`; folder membership = `MediaTrack@parentFolder` → `FolderTrack@trackID`. Parts in `List x:id="Events"` (`MusicPart start/length/offset/clipID`).
- `TempoMapSegment@tempo` = seconds per quarter (0.4615 → 130 BPM). `timeFormat="2"` positions in quarter notes.
- `Devices/audiomixer.xml`: `*Channel@gain` linear (0.316 ≈ -10 dB), `@pan` 0..1, `UID x:id="uniqueID"` = track `channelID` (audio); instrument tracks map via `AudioSynthChannel@label`.
- XML uses undeclared `x:` prefix (`x:id`); parse after rewriting.

## Live experiments (2026-10-17, Studio One 5.5.2 Build 86528, macOS arm64)
- [O] Unsigned extension in `~/Library/Application Support/PreSonus/Studio One 5/Extensions/<id>/` is listed but marked "not compatible with Studio One" until `installdata.xml` (`RequiredApp id="studioapp.*" minVersion="5.0.0.0" maxVersion="*"`) is present. With it, the extension loads; no signature needed.
- [O] FrameworkService `initialize` runs at startup; `Host.IO` text write works into `local://$USERCONTENT/AIBridge` (folder must already exist).
- [O] EditTasks from the package appear in the **Track** menu ("AI Bridge - Run Jobs", "AI Bridge - Dump Host API"); command category resolves as **"Track"** (`Host.GUI.Commands.findCommand("Track", name)`).
- [O] Real round trip: `ping`, `list_tracks` (track.name, mediaType, channel.channelType, color) returned correctly.
- [O] `Host.IO.File(url).moveTo(url)` did not move the file (no error).
- [O] Host dump: namespaces enumerate, native methods and `context` do not (empty).
- [O] **`Host.GUI.addIdleTask(scriptObject)` is unsafe**: at startup → crash (EXC_BAD_ACCESS 0x18 in cclgui+0x12a660 via studioapp+0xf88010); later → `onTimer` never called, then main thread spins in cclgui+0x12a594/+0x12a66c (`_sigtramp`) at 100% CPU. Also `commandCategory`/`alwaysEnabled` were added in the crashing builds (not isolated). Polling disabled in 0.3.2.
- [O] `Host.Console.writeLine` from a FrameworkService at startup did not crash in 0.1.0; not the crash cause.
- [O] User control surfaces: musicdevices.bundle scans app `devices/` then settings-folder `User Devices/` recursively for `*.device` (sub_b6f8 → sub_b914). Our `User Devices/AI Bridge/AI Bridge.device` (vendor="local") shows in External Devices → Add under folder **local**.
- [O] **Auto trigger works**: virtual CoreMIDI source "AI Bridge" → surface CC119 ch16 → `Track/AI Bridge - Run Jobs`. `daw_ping` round trip with no user action (0.3.4).
- [O] MusicTrack channel params: mute, solo, recordArmed, monitor, transpose, velocity. No volume/pan.
- [O] `environment.find("MixerConsole").getChannelList(1)`: `numChannels`, `getChannel(i)` list all channels (AudioSynth + MusicTrack). AudioSynth channels expose `volume` (1 = unity) and `pan` (0.5 = center). Lists 0 and 2 empty here. `track.channel.getDestinationChannel()` returned nothing for a MusicTrack.
- [O] Song file link: `musictrackdevice.xml` `MusicTrackChannel/Connection x:id="instrumentOut" objectID="{UID}/Input"` = `audiomixer.xml` `AudioSynthChannel/UID x:id="uniqueID"`. Live JS equivalent **[?]** (0.3.6 adds generic `probe` op to find it).
- [O] No live track → AudioSynth link: MusicTrack channel `find("instrumentOut"|"destination")` null, `getDestinationChannel()` undefined; channels expose only `name/label/channelType/input/find/findParameter/connectTo/getDestinationChannel`; no `color`.
- [O] **Volume/pan works (0.3.7)**: server reads `instrumentOut` from newest saved song → AudioSynth channel name (e.g. "Channel03") → bridge finds it in `getChannelList(1)` and `findParameter("volume"|"pan").setValue(v, true)`. AI Test set to volume 0.5, pan 0.25; read back the same.
- [O] Mixer display after set: volume 0.5 → "-6.0" dB (linear gain). pan 0.25 → "R50": **values below 0.5 pan right** (live parameter). Whether saved `audiomixer.xml@pan` uses the same direction **[?]**; song.py's sign is unverified.
- [O] **add_instrument works (0.4.0)**: `MusicPartFunctions.addInstrument("{classID string}")` → slot (e.g. "Inst04"), `connectTrackWithInstrument(track, slot, -1, false)`. Vital loaded on new track "AI Vital"; insert_notes on it works. Bug: `getInsertPosition()` ≠ new track index; fixed in 0.4.1 by keeping the track object.
- [O] Instrument slot path: `environment.find("Synths")` (title "Instruments", has `insertDeviceClass`) → `.find("Inst04")` (params: bypass; methods find/findParameter/openEditor) → children `find("Device")` (name = plug-in, e.g. "Vital"), `find("Presets")`, `find("Automation")`. Plug-in parameter names for `Device.findParameter` **[?]**: "0", "Macro 1", "Volume", "Filter 1 Cutoff" etc. all null.
- [O] Plug-in cache lists 37 AudioEffect classes (Pro EQ, Compressor, Room Reverb, Ampire, ...) and 13 AudioSynth entries; only native synth listed is Mojito (no Presence XT/Impact XT entries here).
- [O] Sound Set entries are encrypted (all start with the same bytes as the container header, e.g. a .mid does not start with "MThd"). Not decrypted on purpose; loops must be loaded by Studio One itself. No script API for importing an audio file found yet (EditFunctions has insertNewClip(mediaType, start, end, track) only).
- [O] **Effects work (0.5.0)**: `DeviceEditFunctions.insertDevice(channel.inserts, "{classID}")` → slot "FX01", "FX02"... on the AudioSynth channel. Param path `channel.inserts.find("FX01").find("Device").findParameter("lfgain")`.
- [O] Plug-in params: `value` is internal 0..10000 (min 0, max 10000), `string` = display text ("0.00", "110 Hz", "25.0 %"). Methods: `fromString(text)` sets by display units ("6" → "6.00" dB, "80 Hz" → "80.0 Hz"), `setNormalized/getNormalized` (0..1). Param IDs of native plug-ins = attribute names in factory presets (`presets/PreSonus/<plug-in>/*.preset` ZIP → data.fxpreset `Attributes x:id="ParameterData"`).
- [O] VST parameter naming: automation identity `param:///Synths/{slot uid}/vstp54`, metaIdentity `{classID}/vstp54`, title "Chorus Frequency" (Vital). Live: `Synths.find("Inst04").find("Device").findParameter("vstp54")` works (string "8 s", 0..10000) **after** the user made an automation lane for it; vstp0/1/2/100/500/800 null → proxies likely created only for exposed parameters **[?]**. `Host.Objects.getObjectByUrl("param:///...")` null. vstservice `sub_53c90` builds parameter info per VST3 index (`getParameterInfo`), string `vstp%d` at 0xd58c5.
- [O] No script-level file/loop import: MusicPartFunctions/AudioFunctions expose insertNewClip/addMediaTrack only; EditFunctions/MediaFunctions/ImportFunctions etc. don't exist.
- [O] **VST3 parameter titles (tools/vst3dump)**: a minimal VST3 host (MIT `vst3_pluginterfaces` headers only) lists `getParameterInfo` per plug-in. JUCE plug-ins need host `IMessage`/`IAttributeList` (`IHostApplication::createInstance`) and component↔controller `IConnectionPoint::connect` before the controller reports parameters. Vital: 2855 params (2080 are JUCE "MIDI CC n|m" proxies), IDs match Studio One `vstp<id>` exactly (id 54 "Chorus Frequency" = live vstp54 "8 s"; all 100 live-scanned ids/values matched). Useful counts: Vital 775, Surge XT 775, Splice INSTRUMENT 577, BBC SO 46 (Expression, Dynamics, Release, Vibrato, mic levels...), DecentSampler 3, SINE Player 1. Some plug-ins (Splice Sounds) log to stdout; vst3dump writes JSON to a dup of stdout.
- [O] `fromString` depends on the plug-in's own text parser: Vital "0.5" for Envelope 1 Attack → "16.0002 secs" (wrong); Oscillator 1 Level "0.8" → "0.8" (right). 0.5.3 falls back to a binary search on `setNormalized` reading `p.string`.
- [O] 0.5.3 live: `list_instruments` → Inst01 DecentSampler, Inst02 BBC Symphony Orchestra, Inst03 "BBC Symphony Orchestra 2" (device name = instance name), Inst04 Vital. Vital Envelope 1 Attack display "0.5" → "0.5 secs" via the search fallback. BBC Dynamics = vstp20 ("100%").
