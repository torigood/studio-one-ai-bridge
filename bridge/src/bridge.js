// AI Bridge - shared code (mailbox + operations).
//
// Every Host/context call below copies a form used by Studio One's own
// shipped scripts (see docs/re-findings.md). Lines marked UNVERIFIED use
// names inferred from those scripts but not seen with these exact arguments.

const kBridgeVersion = "0.7.0";
const kProtocol = 1;
const kMailbox = "local://$USERCONTENT/AIBridge"; // ~/Music/Studio One 5/AIBridge

//************************************************************************************************
// Mailbox (files written by the MCP server, one JSON object per file, single line)
//************************************************************************************************

function mailboxUrl (sub, isFolder)
{
	let url = Host.Url (kMailbox, true);
	if(sub)
		url.descend (sub, isFolder);
	return url;
}

function readTextFile (url)
{
	let textFile = Host.IO.openTextFile (url);
	if(!textFile)
		return null;
	let text = "";
	while(!textFile.endOfStream)
		text += textFile.readLine ();
	textFile.close ();
	return text;
}

function writeTextFile (url, text)
{
	let textFile = Host.IO.createTextFile (url);
	if(!textFile)
		return false;
	textFile.writeLine (text);
	textFile.close ();
	return true;
}

function writeJson (sub, name, obj)
{
	let url = mailboxUrl (sub, true);
	url.descend (name, false);
	return writeTextFile (url, JSON.stringify (obj));
}

// Host.Console.writeLine crashed Studio One when called from a FrameworkService
// during startup (EXC_BAD_ACCESS in the Console window code, 2026-10-17).
// Log to a file in the mailbox instead; never to the Console.
var gLogLines = [];

function log (msg)
{
	gLogLines.push (new Date ().toISOString () + " " + msg);
	if(gLogLines.length > 50)
		gLogLines.shift ();
	try { writeJson (null, "bridge-log.json", gLogLines); } catch(e) {}
}

/** Returns [{url, job}] for every *.json in inbox, oldest name first. */
function hasResult (name)
{
	let url = mailboxUrl ("outbox", true);
	url.descend (name + ".json", false);
	return Host.IO.File (url).exists ();
}

/** Inbox *.json without a result in outbox, oldest name first.
 *  Results are the source of truth: File.moveTo did not move processed jobs
 *  in Studio One 5.5 (2026-10-17), so a job is "done" once outbox/<name>.json exists. */
function unresolvedJobPaths ()
{
	let paths = [];
	let inbox = mailboxUrl ("inbox", true);
	if(!Host.IO.File (inbox).exists ())
		return paths;
	let iter = Host.IO.findFiles (inbox, "*.json");
	while(!iter.done ())
	{
		let path = iter.next ();
		if(!hasResult (path.getName (false)))
			paths.push (path);
	}
	paths.sort ((a, b) => (a.getName (false) < b.getName (false) ? -1 : a.getName (false) > b.getName (false) ? 1 : 0));
	return paths;
}

/** Returns [{url, name, job}] for unresolved jobs. */
function pendingJobs ()
{
	return unresolvedJobPaths ().map (path => {
		let name = path.getName (false);
		let job = null;
		try { job = JSON.parse (readTextFile (path)); } catch(e) { job = { id: name, op: "invalid", error: String (e) }; }
		return { url: path, name: name, job: job };
	});
}

/** Used by the idle task. */
function hasPendingJobs ()
{
	return unresolvedJobPaths ().length > 0;
}

function finishJob (entry, result)
{
	// Result file is named after the inbox file, so hasResult () matches it.
	result.id = entry.job && entry.job.id ? entry.job.id : entry.name;
	result.protocol = kProtocol;
	result.finished = new Date ().toISOString ();
	writeJson ("outbox", entry.name + ".json", result);
}

//************************************************************************************************
// Poller - idle task started from the "Run Jobs" edit task (never at app startup).
// Host.GUI.addIdleTask / ITimerTask.onTimer: names from cclgui ScriptGUIHost and
// Host.Interfaces. UNVERIFIED: call frequency, onTimer arguments.
//************************************************************************************************

const kRunJobsName = "AI Bridge - Run Jobs";
// Edit tasks become commands; the category is not declared in classfactory.xml,
// so probe likely ones (TrackEdit tasks such as "Arm All Audio Tracks" are in "Track").
const kRunJobsCategories = ["Track", "Edit", "Event", "AI Bridge"];
const kPollIntervalMs = 300;

var gPoller = null;

function writePollerStatus (extra)
{
	let s = { version: kBridgeVersion, at: new Date ().toISOString () };
	for(let k in extra)
		s[k] = extra[k];
	writeJson (null, "poller.json", s);
}

function findRunJobsCategory ()
{
	for(let c of kRunJobsCategories)
	{
		try
		{
			if(Host.GUI.Commands.findCommand (c, kRunJobsName))
				return c;
		}
		catch(e) {}
	}
	return null;
}

function PollTask (category)
{
	this.interfaces = [Host.Interfaces.ITimerTask];
	this.category = category;
	this.last = 0;
	this.busy = false;
	this.ticks = 0;
	this.checks = 0;
	this.pendingSeen = 0;
	this.triggers = 0;
	this.lastResult = null;
	this.lastError = null;
	this.lastReport = 0;

	this.report = function (now)
	{
		this.lastReport = now;
		writePollerStatus ({ running: true, category: this.category, ticks: this.ticks, checks: this.checks,
		                     pendingSeen: this.pendingSeen, triggers: this.triggers,
		                     lastResult: this.lastResult, lastError: this.lastError });
	}

	this.onTimer = function ()
	{
		this.ticks++;
		let now = Date.now ();
		if(this.busy || now - this.last < kPollIntervalMs)
			return;
		this.last = now;
		this.busy = true;
		try
		{
			this.checks++;
			if(hasPendingJobs ())
			{
				this.pendingSeen++;
				this.triggers++;
				this.lastResult = String (Host.GUI.Commands.interpretCommand (this.category, kRunJobsName, false, null));
			}
		}
		catch(e)
		{
			this.lastError = String (e);
		}
		try
		{
			if(now - this.lastReport > 2000)
				this.report (now);
		}
		catch(e) {}
		this.busy = false;
	}
}

/** Polling is DISABLED. Passing a script object to Host.GUI.addIdleTask is unsafe in
 *  Studio One 5.5.2: registered at startup it crashed the app (0.2.0/0.2.1); registered
 *  later it never called onTimer and the main thread then spun in cclgui (+0x12a594,
 *  same function as the crash) at 100% CPU (0.3.1). Do not re-enable. 2026-10-17. */
function startPoller ()
{
	let status = { running: false, reason: "disabled: addIdleTask with a script object hangs/crashes Studio One 5.5" };
	writePollerStatus (status);
	return status;
}

//************************************************************************************************
// Track lookup
//************************************************************************************************

function trackInfo (track, index)
{
	let info = { index: index };
	try { info.name = track.name; } catch(e) {}              // name: used as oldTrack.name in ExplodePitches.js
	try { info.mediaType = track.mediaType; } catch(e) {}    // used in ArmAll.js
	try { info.channelType = track.channel ? track.channel.channelType : null; } catch(e) {}
	try { info.color = track.color; } catch(e) {}
	return info;
}

function findTrack (context, ref)
{
	let list = context.mainTrackList;
	for(let i = 0; i < list.numTracks; i++)
	{
		let t = list.getTrack (i);
		if((typeof ref === "number" && ref === i) || (typeof ref === "string" && t.name === ref))
			return t;
	}
	return null;
}

function mediaTime (functions, beats)
{
	let t = functions.newMediaTime ();   // AddArrangerSection.js
	t.musical = beats;                   // Insert Part.js: end.musical = start.musical + length
	return t;
}

//************************************************************************************************
// Operations. Each returns a plain object, or fail (msg) on error.
//************************************************************************************************

const Ops = {};

// Ops report errors by returning fail (msg), never by throwing: Studio One shows a modal
// "Scripting Error" dialog for a thrown Error even when it is caught (observed 0.3.2).
function fail (msg)
{
	return { __error: msg };
}

Ops.ping = function (context, args)
{
	return { pong: true, version: kBridgeVersion };
};

Ops.list_tracks = function (context, args)
{
	let list = context.mainTrackList;
	let out = [];
	for(let i = 0; i < list.numTracks; i++)
		out.push (trackInfo (list.getTrack (i), i));
	return { tracks: out };
};

Ops.command = function (context, args)
{
	// Same call the Macro toolbar makes (macros/elements.js MacroExecuter.onCommand).
	let flat = null;
	if(args.args)
	{
		flat = [];
		for(let k in args.args)
		{
			flat.push (k);
			flat.push (String (args.args[k]));
		}
	}
	let ok = Host.GUI.Commands.interpretCommand (args.category, args.name, false, flat);
	return { handled: !!ok };
};

function createTrack (context, args)
{
	// Create Tracks.js
	let functions = context.functions;
	let index = context.mainTrackList.getInsertPosition ();
	let name = args.name || "";
	let track = null;
	functions.executeImmediately = true;
	switch(args.type)
	{
	case "audio_mono":
	case "audio_stereo":
	case "instrument":
		{
			let fmt = Host.Engine.TrackFormats.findEqual (
				args.type === "audio_mono" ? "Mono" : args.type === "audio_stereo" ? "Stereo" : "Instrument");
			track = fmt ? functions.addMediaTrack (index, name, fmt) : null;
		}
		break;
	case "folder":     track = functions.addTrack ("FolderTrack", index, name); break;
	case "automation": track = functions.addTrack ("AutomationTrack", index, name); break;
	default: functions.executeImmediately = false; return fail ("unknown track type: " + args.type);
	}
	functions.executeImmediately = false;
	if(!track)
		return fail ("track was not created");
	return { track: track, index: index };
};

Ops.add_track = function (context, args)
{
	let r = createTrack (context, args);
	return r.__error ? r : { created: trackInfo (r.track, r.index) };
};

Ops.add_instrument = function (context, args)
{
	// Insert FX.js (InsertInstrumentTask): MusicPartFunctions.addInstrument (presetOrClassID)
	// then connectTrackWithInstrument (track, slot, -1, false). Add Tracks Dialog.js uses
	// environment.find ("Synths").insertDeviceClass (cls) for the same slot type.
	// UNVERIFIED: whether a class ID string is accepted or Host.UID (string) is needed.
	let track = null;
	if(args.track !== undefined)
	{
		track = findTrack (context, args.track);
		if(!track)
			return fail ("track not found: " + args.track);
	}
	else
	{
		// getInsertPosition () is not the new track's index in mainTrackList (observed 0.4.0),
		// so keep the object returned by addMediaTrack.
		let created = createTrack (context, { type: "instrument", name: args.name || "" });
		if(created.__error)
			return created;
		track = created.track;
	}
	let pf = context.functions.root.createFunctions ("MusicPartFunctions");
	if(!pf || typeof pf.addInstrument !== "function")
		return fail ("MusicPartFunctions.addInstrument not available");
	pf.executeImmediately = true;
	let slot = pf.addInstrument (args.class_id);
	let via = "string";
	if(!slot)
	{
		slot = pf.addInstrument (Host.UID (args.class_id));
		via = "Host.UID";
	}
	if(!slot)
	{
		pf.executeImmediately = false;
		return fail ("instrument was not created: " + args.class_id);
	}
	pf.connectTrackWithInstrument (track, slot, -1, false);
	pf.executeImmediately = false;
	return { track: trackInfo (track, -1), class_id_as: via, slot: probeProps (slot, ["name", "title", "label"]) };
};

const kParamCandidates = ["volume", "pan", "mute", "solo", "recordArmed", "monitor", "transpose",
	"velocity", "gain", "Volume", "Pan", "Mute", "Solo", "level", "balance", "inputGain", "on", "bypass"];

function probeParams (obj)
{
	let found = {};
	if(!obj || !obj.findParameter)
		return found;
	for(let n of kParamCandidates)
	{
		try
		{
			let p = obj.findParameter (n);
			if(p)
			{
				let v = null;
				try { v = p.value; } catch(e) {}
				found[n] = v;
			}
		}
		catch(e) {}
	}
	return found;
}

function probeProps (obj, names)
{
	let out = {};
	for(let n of names)
	{
		try
		{
			let v = obj[n];
			out[n] = v === undefined ? "undefined" : (v === null ? null : (typeof v === "object" ? "object" : v));
		}
		catch(e) { out[n] = "error: " + e; }
	}
	return out;
}

// Read-only discovery: which parameters/properties a track and its channel expose.
Ops.inspect_track = function (context, args)
{
	let track = findTrack (context, args.track);
	if(!track)
		return fail ("track not found: " + args.track);
	let ch = track.channel;
	let res = {
		track: probeProps (track, ["name", "mediaType", "color", "isEditLocked", "parentFolderID", "channel"]),
		trackParams: probeParams (track),
		channel: ch ? probeProps (ch, ["channelType", "mute", "solo", "volume", "pan", "recordUnit",
			"pitchNameList", "label", "name", "audioChannel", "outputChannel", "synthChannel", "instrument"]) : null,
		channelParams: probeParams (ch)
	};
	try { res.channelFindInserts = ch && ch.find ? (ch.find ("Inserts") ? "object" : null) : null; } catch(e) {}
	return res;
};

function describeChannel (ch)
{
	let d = probeProps (ch, ["label", "name", "channelType"]);
	d.params = probeParams (ch);
	return d;
}

// Read-only discovery of mixer channels (instrument outputs carry volume/pan, not MusicTrack channels).
// Create Tracks.js: environment.find ("MixerConsole").getChannelList (1) -> numSelectedChannels/getSelectedChannel.
// Methods are checked with typeof before calling so nothing throws.
Ops.inspect_mixer = function (context, args)
{
	let res = { lists: {} };
	let env = context.functions.root.environment;
	let console = env ? env.find ("MixerConsole") : null;
	res.console = console ? "object" : null;
	let mixer = env ? env.find ("AudioMixer") : null;
	res.audioMixer = mixer ? "object" : null;
	if(console && typeof console.getChannelList === "function")
	{
		for(let n = 0; n < 3; n++)
		{
			let cl = null;
			try { cl = console.getChannelList (n); } catch(e) { res.lists[n] = "error: " + e; continue; }
			if(!cl) { res.lists[n] = null; continue; }
			let info = probeProps (cl, ["numChannels", "numSelectedChannels", "count", "numItems"]);
			info.methods = {};
			for(let m of ["getChannel", "getSelectedChannel", "getChannelAt", "at"])
				info.methods[m] = typeof cl[m];
			info.channels = [];
			let total = typeof cl.numChannels === "number" ? cl.numChannels : 0;
			if(typeof cl.getChannel === "function")
				for(let i = 0; i < Math.min (total, 64); i++)
				{
					let ch = null;
					try { ch = cl.getChannel (i); } catch(e) {}
					if(ch)
						info.channels.push (describeChannel (ch));
				}
			res.lists[n] = info;
		}
	}
	if(args.track !== undefined)
	{
		let track = findTrack (context, args.track);
		let ch = track ? track.channel : null;
		if(ch)
		{
			res.trackChannelMethods = {};
			for(let m of ["getDestinationChannel", "getConnectedChannel", "getOutputChannel", "getSynthChannel", "connectTo"])
				res.trackChannelMethods[m] = typeof ch[m];
			if(typeof ch.getDestinationChannel === "function")
			{
				let dst = null;
				try { dst = ch.getDestinationChannel (); } catch(e) { res.destination = "error: " + e; }
				if(dst)
					res.destination = describeChannel (dst);
			}
		}
	}
	return res;
};

function describeValue (v)
{
	if(v === undefined) return "undefined";
	if(v === null) return null;
	if(typeof v === "object") return "object";
	if(typeof v === "function") return "function";
	return v;
}

// Generic read-only discovery so new API questions do not need a new build.
// args.root: "track" (args.track) | "trackChannel" | "mixerChannel" (args.list, args.index) | "environment"
// args.path: steps from root; a string is a property, {call: name, args: [...]} calls a method
//            (only if typeof is "function").
// args.names: properties/methods to report on the object the path ends at.
Ops.probe = function (context, args)
{
	let obj = null;
	let root = args.root || "trackChannel";
	if(root === "track" || root === "trackChannel")
	{
		let t = findTrack (context, args.track);
		if(!t)
			return fail ("track not found: " + args.track);
		obj = root === "track" ? t : t.channel;
	}
	else if(root === "mixerChannel")
	{
		let console = context.functions.root.environment.find ("MixerConsole");
		let cl = console.getChannelList (args.list !== undefined ? args.list : 1);
		if(!cl || args.index >= cl.numChannels)
			return fail ("mixer channel index out of range");
		obj = cl.getChannel (args.index);
	}
	else if(root === "environment")
		obj = context.functions.root.environment;
	else if(root === "object")
		obj = Host.Objects.getObjectByUrl (args.url);   // e.g. "://studioapp/DocumentManager" (basicactions.js)
	else if(root === "functions")
		obj = context.functions.root.createFunctions (args.url);   // e.g. "MusicPartFunctions"
	else
		return fail ("unknown root: " + root);

	let trail = [];
	for(let step of (args.path || []))
	{
		if(obj === null || obj === undefined)
			return { trail: trail, stopped: "null before step " + JSON.stringify (step) };
		if(typeof step === "string")
			obj = obj[step];
		else if(step && typeof obj[step.call] === "function")
			obj = obj[step.call].apply (obj, step.args || []);
		else
			return { trail: trail, stopped: "not a function: " + JSON.stringify (step) };
		trail.push (describeValue (obj));
	}
	let res = { trail: trail, value: describeValue (obj) };
	if(obj && typeof obj === "object")
	{
		res.names = {};
		for(let n of (args.names || []))
		{
			try { let v = obj[n]; res.names[n] = typeof v === "function" ? "function" : describeValue (v); }
			catch(e) { res.names[n] = "error: " + e; }
		}
		if(typeof obj.findParameter === "function")
			res.params = probeParams (obj);
	}
	return res;
};

// Events of a track in order (track.createIterator (): done/next/first/last/previous, observed 0.5.3).
function trackEvents (track, limit)
{
	let out = [];
	let it = track && typeof track.createIterator === "function" ? track.createIterator () : null;
	while(it && !it.done () && out.length < (limit || 1000))
		out.push (it.next ());
	return out;
}

const kEventProps = ["name", "start", "length", "offset", "speed", "tune", "isMuted", "mediaType", "trackID"];

function describeEvent (e, index)
{
	let d = probeProps (e, kEventProps);
	d.index = index;
	return d;
}

Ops.list_events = function (context, args)
{
	let track = findTrack (context, args.track);
	if(!track)
		return fail ("track not found: " + args.track);
	return { track: track.name, events: trackEvents (track).map (describeEvent) };
};

// Notes of a part in time order. part.createSequenceIterator () (ExplodePitches.js); part.createIterator ()
// returns null for music parts. note.start/length are quarter notes from the part start,
// note.startTime.musical is the song position (observed 0.6.0).
function partNotes (part)
{
	let out = [];
	let it = part.createSequenceIterator ();
	while(it && !it.done () && out.length < 10000)
		out.push (it.next ());
	return out;
}

function musicParts (track)
{
	return trackEvents (track).filter (e => typeof e.createSequenceIterator === "function")
		.sort ((a, b) => a.start - b.start);
}

function findPart (track, startBeats)
{
	for(let p of musicParts (track))
		if(Math.abs (p.start - startBeats) < 1e-6)
			return p;
	return null;
}

Ops.read_notes = function (context, args)
{
	let track = findTrack (context, args.track);
	if(!track)
		return fail ("track not found: " + args.track);
	let parts = musicParts (track);
	if(args.part_start !== undefined)
		parts = parts.filter (p => Math.abs (p.start - args.part_start) < 1e-6);
	return { track: track.name, parts: parts.map (p => {
		let notes = partNotes (p).map ((n, i) => {
			let d = { index: i, pitch: n.pitch, velocity: Math.round (n.velocity * 1000) / 1000,
				start: n.start, length: n.length };
			try { d.beat = n.startTime.musical; } catch(e) {}
			if(n.isMuted)
				d.muted = 1;
			return d;
		});
		return { start: p.start, length: p.length, offset: p.offset, muted: p.isMuted ? 1 : 0, notes: notes };
	}) };
};

// Edits refer to note indexes as returned by read_notes before this call (time order).
// MusicFunctions (ExplodePitches.js, musicedit/Util.js): modifyPitch, modifyVelocity, moveEvent (note, start),
// resizeEvent (note, length), deleteEvent (note); start/length relative to the part (observed 0.6.0).
Ops.edit_notes = function (context, args)
{
	let track = findTrack (context, args.track);
	if(!track)
		return fail ("track not found: " + args.track);
	let part = findPart (track, args.part_start);
	if(!part)
		return fail ("no instrument part starts at beat " + args.part_start + " on " + track.name);
	let notes = partNotes (part);
	for(let e of (args.edits || []))
		if(!(e.index >= 0 && e.index < notes.length))
			return fail ("note index out of range: " + e.index + " (part has " + notes.length + " notes)");
	let mf = context.functions.root.createFunctions ("MusicFunctions");
	mf.executeImmediately = true;
	let changed = 0, deleted = 0, added = 0;
	for(let e of (args.edits || []))
	{
		let n = notes[e.index];
		if(e["delete"])
		{
			mf.deleteEvent (n);
			deleted++;
			continue;
		}
		if(e.pitch !== undefined) mf.modifyPitch (n, e.pitch);
		if(e.velocity !== undefined) mf.modifyVelocity (n, e.velocity);
		if(e.length !== undefined) mf.resizeEvent (n, e.length);
		if(e.start !== undefined) mf.moveEvent (n, e.start);
		changed++;
	}
	for(let a of (args.add || []))
	{
		let note = mf.createEvent ("Note");
		mf.insertEvent (part, note);
		mf.modifyPitch (note, a.pitch);
		mf.modifyVelocity (note, a.velocity !== undefined ? a.velocity : 0.8);
		mf.resizeEvent (note, a.length);
		mf.moveEvent (note, a.start);
		added++;
	}
	mf.executeImmediately = false;
	return { track: track.name, part_start: part.start, changed: changed, deleted: deleted, added: added,
		notes: partNotes (part).length };
};

// Chord track: editor.model.chords.getChordTrack () (toolsets.js uses editor.model.chords); events expose
// start/length and chord {name ("D♯m7"), root, bass, type}. Chords cannot be created or changed from
// script (ChordFunctions.createEvent and chords.addChordEvent return undefined, chord.root is read-only; 0.6.0).
Ops.read_chords = function (context, args)
{
	let editor = context.editor;
	let chords = editor && editor.model ? editor.model.chords : null;
	let track = chords && typeof chords.getChordTrack === "function" ? chords.getChordTrack () : null;
	if(!track)
		return fail ("no chord track");
	return { chords: trackEvents (track).map (e => {
		let c = e.chord;
		return { start: e.start, length: e.length, name: c ? c.name : null };
	}).sort ((a, b) => a.start - b.start) };
};

// Transport panel parameters: environment.find ("TransportPanel") (device:///TransportPanel in ATOM.surface.xml).
// Observed 0.6.0: tempo (10..400) changes the tempo segment at the playhead; timeSignatureNum/Denom insert a
// signature change at the start of the playhead's bar; primaryTime (string "0003.01.01.00" when
// primaryTimeFormat is Bars) moves the playhead. args.set: [[name, value]] in order, a number uses
// setValue, a string fromString. args.restore_position puts the playhead back afterwards.
const kTransportParams = ["tempo", "timeSignatureNum", "timeSignatureDenom", "primaryTime", "primaryTimeFormat",
	"secondaryTime", "loop", "loopStart", "loopEnd", "punch", "precount"];

Ops.transport = function (context, args)
{
	let panel = context.functions.root.environment.find ("TransportPanel");
	if(!panel)
		return fail ("TransportPanel not found");
	function read ()
	{
		let out = {};
		for(let n of kTransportParams)
		{
			let p = panel.findParameter (n);
			if(p)
				out[n] = p.string;
		}
		return out;
	}
	let before = read ();
	for(let pair of (args.set || []))
	{
		let p = panel.findParameter (pair[0]);
		if(!p)
			return fail ("transport parameter not found: " + pair[0]);
		if(typeof pair[1] === "string")
			p.fromString (pair[1]);
		else
			p.setValue (pair[1], true);
	}
	if(args.restore_position && before.primaryTime)
		panel.findParameter ("primaryTime").fromString (before.primaryTime);
	let res = { before: before, after: read () };
	try
	{
		let tc = context.functions.root.timeContext;
		let sig = tc.getTimeSignature (args.at_beat || 0);
		res.time_signature_at = { beat: args.at_beat || 0, numerator: sig.numerator, denominator: sig.denominator };
		res.seconds_per_beat = tc.ppqToSeconds (1);
	}
	catch(e) {}
	return res;
};

// Generic method call for API experiments: objects are resolved by reference, so methods that
// take tracks/events/times can be tried without a new build.
// args.steps: [{as?, on: expr, call?: name, args?: [expr], get?: name, set?: name, value?: expr, names?: [..]}]
// expr: {var: name} | {track: ref} | {event: {track, index}} | {functions: name} ("" = context.functions)
//       | {time: beats} | {context: true} | anything else is a literal.
// Function sets are created once per job, so executeImmediately set on one is seen by later calls.
// Methods are called only if typeof is "function"; natives given wrong arguments return 0 (observed 0.5.3).
Ops.call = function (context, args)
{
	let vars = {}, sets = {};
	function functionsNamed (name)
	{
		if(!name)
			return context.functions;
		if(!sets[name])
			sets[name] = context.functions.root.createFunctions (name);
		return sets[name];
	}
	function resolve (x)
	{
		if(!x || typeof x !== "object" || Array.isArray (x))
			return Array.isArray (x) ? x.map (resolve) : x;
		if("var" in x) return vars[x["var"]];
		if("track" in x) return findTrack (context, x.track);
		if("event" in x) return trackEvents (findTrack (context, x.event.track))[x.event.index || 0];
		if("functions" in x) return functionsNamed (x.functions);
		if("time" in x) return mediaTime (context.functions, x.time);
		if("context" in x) return context;
		return x;
	}
	function report (v, names)
	{
		let d = { value: describeValue (v) };
		if(v && typeof v === "object")
		{
			d.props = {};
			for(let n of (names || kEventProps.concat (["musical", "seconds"])))
			{
				try
				{
					let p = v[n];
					if(p !== undefined)
						d.props[n] = typeof p === "function" ? "function" : describeValue (p);
				}
				catch(e) { d.props[n] = "error: " + e; }
			}
		}
		return d;
	}
	let results = [];
	for(let step of (args.steps || []))
	{
		let on = resolve (step.on);
		let v;
		if(on === null || on === undefined)
		{
			results.push ({ error: "target is " + on });
			break;
		}
		if(step.call !== undefined)
		{
			if(typeof on[step.call] !== "function")
			{
				results.push ({ error: "not a function: " + step.call });
				break;
			}
			try { v = on[step.call].apply (on, resolve (step.args || [])); }
			catch(e) { results.push ({ error: String (e) }); break; }
		}
		else if(step.set !== undefined)
		{
			try { on[step.set] = resolve (step.value); v = on[step.set]; }
			catch(e) { results.push ({ error: String (e) }); break; }
		}
		else if(step.get !== undefined)
			v = on[step.get];
		else
			v = on;
		if(step.as)
			vars[step.as] = v;
		results.push (report (v, step.names));
	}
	for(let k in sets)
		try { sets[k].executeImmediately = false; } catch(e) {}
	try { context.functions.executeImmediately = false; } catch(e) {}
	return { results: results };
};

function mixerChannelList (context)
{
	// Create Tracks.js: environment.find ("MixerConsole").getChannelList (1); list 1 holds all channels (observed).
	let console = context.functions.root.environment.find ("MixerConsole");
	let cl = console ? console.getChannelList (1) : null;
	let out = [];
	if(cl)
		for(let i = 0; i < cl.numChannels; i++)
			out.push (cl.getChannel (i));
	return out;
}

// ref: index, channel name ("Channel03") or label ("St-1"). Name wins over label; type filters both.
function findMixerChannel (context, ref, type)
{
	let list = mixerChannelList (context);
	if(typeof ref === "number")
		return list[ref] || null;
	let byLabel = null;
	for(let ch of list)
	{
		if(type && ch.channelType !== type)
			continue;
		if(ch.name === ref)
			return ch;
		if(!byLabel && ch.label === ref)
			byLabel = ch;
	}
	return byLabel;
}

Ops.list_mixer = function (context, args)
{
	return { channels: mixerChannelList (context).map ((ch, i) => {
		let d = describeChannel (ch);
		d.index = i;
		return d;
	}) };
};

Ops.set_channel_param = function (context, args)
{
	// TransposeTracks.js: channel.findParameter ("transpose").setValue (value, true)
	// Observed: volume 1 = unity (linear gain, 0.5 shows -6.0 dB), pan 0..1 with 0.5 = centre and 0.25 shown as R50.
	// MusicTrack channels have no volume/pan; those live on the instrument's AudioSynth channel.
	// The live API exposes no track -> AudioSynth link, so the server passes args.instrument_channel
	// (AudioSynth channel name taken from the saved song) or the caller names args.mixer_channel.
	let target = null;
	let via = null;
	if(args.mixer_channel !== undefined)
	{
		target = findMixerChannel (context, args.mixer_channel, null);
		if(!target)
			return fail ("mixer channel not found: " + args.mixer_channel);
		via = "mixer_channel";
	}
	else
	{
		let track = findTrack (context, args.track);
		if(!track || !track.channel)
			return fail ("track not found: " + args.track);
		if(track.channel.findParameter (args.param))
		{
			target = track.channel;
			via = "track";
		}
		else if(args.instrument_channel)
		{
			target = findMixerChannel (context, args.instrument_channel, "AudioSynth");
			via = "instrument_channel";
		}
		if(!target)
			return fail ("parameter '" + args.param + "' is not on track '" + args.track +
				"' and its instrument channel is unknown (save the song, or pass mixer_channel; see daw_list_mixer)");
	}
	let p = target.findParameter (args.param);
	if(!p)
		return fail ("parameter not found on " + target.label + ": " + args.param);
	p.setValue (args.value, true);
	return { channel: target.name, label: target.label, channelType: target.channelType, via: via,
		param: args.param, value: p.value };
};

// Audio channel for inserts/plug-ins: mixer_channel, else the track's channel, or for an
// instrument track (MusicTrack) its AudioSynth channel named by args.instrument_channel.
function resolveAudioChannel (context, args)
{
	if(args.mixer_channel !== undefined)
	{
		let ch = findMixerChannel (context, args.mixer_channel, null);
		return ch ? { channel: ch } : fail ("mixer channel not found: " + args.mixer_channel);
	}
	let track = findTrack (context, args.track);
	if(!track || !track.channel)
		return fail ("track not found: " + args.track);
	if(track.channel.channelType !== "MusicTrack")
		return { channel: track.channel };
	if(args.instrument_channel)
	{
		let ch = findMixerChannel (context, args.instrument_channel, "AudioSynth");
		if(ch)
			return { channel: ch };
	}
	return fail ("track '" + args.track + "' is an instrument track and its instrument channel is unknown " +
		"(save the song, or pass mixer_channel; see daw_list_mixer)");
}

Ops.insert_effect = function (context, args)
{
	// Insert FX.js (InsertChannelFXTask): DeviceEditFunctions.insertDevice (channel.inserts, presetOrClassID)
	let r = resolveAudioChannel (context, args);
	if(r.__error)
		return r;
	let folder = r.channel.inserts;
	if(!folder)
		return fail ("channel has no inserts: " + r.channel.label);
	let fx = context.functions.root.createFunctions ("DeviceEditFunctions");
	let slot = fx.insertDevice (folder, args.class_id);
	if(!slot)
		return fail ("effect was not inserted: " + args.class_id);
	return { channel: r.channel.name, label: r.channel.label, slot: probeProps (slot, ["name", "title"]) };
};

// Plug-in parameter access. Instruments: environment.find ("Synths").find (slot) (e.g. "Inst04").
// Effects: channel.inserts.find (slot). The parameter is looked up on the slot, then on its
// "Device" child (observed: slot.find ("Device") is the plug-in itself).
function findPluginSlot (context, args)
{
	if(args.instrument_slot)
	{
		let synths = context.functions.root.environment.find ("Synths");
		let slot = synths ? synths.find (args.instrument_slot) : null;
		return slot || fail ("instrument slot not found: " + args.instrument_slot);
	}
	let r = resolveAudioChannel (context, args);
	if(r.__error)
		return r;
	let slot = r.channel.inserts ? r.channel.inserts.find (args.slot) : null;
	return slot || fail ("insert not found on " + r.channel.label + ": " + args.slot);
}

function closeTo (a, b)
{
	return !isNaN (a) && Math.abs (a - b) <= Math.max (0.02 * Math.abs (b), 1e-3);
}

// Instrument slots ("Inst01".. ) with the plug-in loaded in each. Names are probed because the
// Synths folder has numChildren but no enumerator in the script API (observed 0.4.0).
Ops.list_instruments = function (context, args)
{
	let synths = context.functions.root.environment.find ("Synths");
	let out = [];
	for(let i = 1; i <= 64; i++)
	{
		let name = "Inst" + (i < 10 ? "0" : "") + i;
		let slot = synths.find (name);
		if(!slot)
			continue;
		let dev = slot.find ("Device");
		out.push ({ slot: name, plugin: dev ? dev.name : null });
	}
	return { instruments: out };
};

Ops.plugin_param = function (context, args)
{
	let slot = findPluginSlot (context, args);
	if(slot.__error)
		return slot;
	let p = slot.findParameter (args.param);
	let where = "slot";
	if(!p)
	{
		let dev = slot.find ("Device");
		p = dev ? dev.findParameter (args.param) : null;
		where = "device";
	}
	if(!p)
		return fail ("parameter not found: " + args.param);
	// Observed (0.5.0): plug-in params use an internal 0..10000 range; p.string is the display text.
	// p.fromString ("6") / ("80 Hz") sets by display units (Pro EQ lfgain -> "6.00", lffreq -> "80.0 Hz").
	let before = p.string;
	let method = null;
	if(args.display !== undefined)
	{
		p.fromString (String (args.display));
		method = "fromString";
		// Some plug-ins parse text badly (Vital: "0.5" for an attack time gave "16 secs").
		// If the target is a number and the display does not show it, search the normalized
		// value by reading the display back (assumes the display grows with the value).
		let target = parseFloat (args.display);
		if(!isNaN (target) && !closeTo (parseFloat (p.string), target))
		{
			let lo = 0, hi = 1;
			for(let i = 0; i < 30; i++)
			{
				let mid = (lo + hi) / 2;
				p.setNormalized (mid);
				let v = parseFloat (p.string);
				if(isNaN (v))
					break;
				if(v < target) lo = mid; else hi = mid;
			}
			p.setNormalized (hi);
			method = closeTo (parseFloat (p.string), target) ? "search" : "search (approximate)";
		}
	}
	else if(args.normalized !== undefined)
		p.setNormalized (args.normalized);
	else if(args.value !== undefined)
		p.setValue (args.value, true);
	let out = { slot: slot.name, param: args.param, found_on: where, before: before, display: p.string, raw: p.value };
	if(method)
		out.method = method;
	try { out.normalized = p.getNormalized (); } catch(e) {}
	return out;
};

// Third-party plug-ins: Studio One names VST parameters "vstp<ParamID>" (song.xml automation identity
// param:///Synths/{slot uid}/vstp54, title "Chorus Frequency" for Vital). Scan a range to see which exist.
const kParamLabelProps = ["title", "label", "shortName", "displayName", "caption", "description", "unit", "units",
	"paramTitle", "longName", "text"];

Ops.scan_params = function (context, args)
{
	let slot = findPluginSlot (context, args);
	if(slot.__error)
		return slot;
	let dev = slot.find ("Device") || slot;
	let prefix = args.prefix || "vstp";
	let from = args.from || 0, to = Math.min (args.to !== undefined ? args.to : 1000, (args.from || 0) + 5000);
	let found = [];
	for(let i = from; i <= to; i++)
	{
		let p = dev.findParameter (prefix + i);
		if(!p)
			continue;
		let item = { name: prefix + i, display: p.string };
		for(let k of kParamLabelProps)
		{
			try { let v = p[k]; if(v !== undefined && typeof v !== "function" && typeof v !== "object") item[k] = v; } catch(e) {}
		}
		found.push (item);
		if(found.length >= (args.limit || 500))
			break;
	}
	return { device: dev.name, scanned: [from, to], found: found };
};

Ops.insert_notes = function (context, args)
{
	// Part:  Insert Part.js  functions.insertNewClip ("Music", start, end, track)
	// Notes: musicedit/Util.js insertNewNote with MusicFunctions from ExplodePitches.js
	// UNVERIFIED: unit of note start/length (assumed quarter notes relative to part start).
	let functions = context.functions;
	let track = findTrack (context, args.track);
	if(!track)
		return fail ("track not found: " + args.track);
	let start = mediaTime (functions, args.start_beats);
	let end = mediaTime (functions, args.start_beats + args.length_beats);
	functions.executeImmediately = true;
	let part = functions.insertNewClip ("Music", start, end, track);
	functions.executeImmediately = false;
	if(!part)
		return fail ("part was not created");

	let mf = functions.root.createFunctions ("MusicFunctions");
	let count = 0;
	for(let n of (args.notes || []))
	{
		let note = mf.createEvent ("Note");
		mf.insertEvent (part, note);
		mf.modifyPitch (note, n.pitch);
		mf.modifyVelocity (note, n.velocity !== undefined ? n.velocity : 0.8);
		mf.resizeEvent (note, n.length_beats);
		mf.moveEvent (note, n.start_beats);
		count++;
	}
	return { track: track.name, notes: count };
};

Ops.dump_host = function (context, args)
{
	let dump = dumpObject (Host, "Host", args && args.depth ? args.depth : 3);
	writeJson (null, "host-api.json", dump);
	return { written: "host-api.json" };
};

function runJob (context, job)
{
	let op = Ops[job.op];
	if(!op)
		return { ok: false, error: "unknown op: " + job.op };
	try
	{
		let r = op (context, job.args || {});
		if(r && r.__error)
			return { ok: false, error: r.__error };
		return { ok: true, result: r };
	}
	catch(e)
	{
		return { ok: false, error: String (e) };
	}
}

//************************************************************************************************
// Host API discovery
//************************************************************************************************

function dumpObject (obj, path, depth)
{
	let node = { path: path, type: typeof obj, members: {} };
	if(depth <= 0 || obj === null || (typeof obj !== "object" && typeof obj !== "function"))
		return node;
	let names = {};
	try { for(let k in obj) names[k] = true; } catch(e) { node.enumError = String (e); }
	try { Object.getOwnPropertyNames (obj).forEach (k => names[k] = true); } catch(e) {}
	for(let k in names)
	{
		let v;
		try { v = obj[k]; } catch(e) { node.members[k] = { error: String (e) }; continue; }
		if(typeof v === "object" || typeof v === "function")
			node.members[k] = dumpObject (v, path + "." + k, depth - 1);
		else
			node.members[k] = { type: typeof v, value: v };
	}
	return node;
}
