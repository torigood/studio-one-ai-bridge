// Runs bridge/src/tasks.js against a fake Studio One Host, to test the mailbox
// protocol and op dispatch. It does NOT prove the real Studio One API behaves
// like this mock; it only checks our own glue code.
//
// Usage: node tests/mock_host.js <usercontent-dir> [run|dump|service]
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const userContent = path.resolve(process.argv[2]);
const mode = process.argv[3] || "run";
const src = path.join(__dirname, "..", "bridge", "src");
const calls = [];
const idleTasks = [];

class Url {
	constructor (u, isFolder) {
		this.p = u.replace("local://$USERCONTENT", userContent);
		this.isFolder = !!isFolder;
	}
	descend (name, isFolder) { this.p = path.join(this.p, name); this.isFolder = !!isFolder; }
	getName (withExt) { const b = path.basename(this.p); return withExt ? b : b.replace(/\.[^.]*$/, ""); }
}

function makePart (name, start, length, notes) {
	return { name, start, length, mediaType: "Music", notes,
		createSequenceIterator: () => { const l = notes.slice().sort((a, b) => a.start - b.start); let i = 0; return { done: () => i >= l.length, next: () => l[i++] }; } };
}

function makeTrack (name, mediaType) {
	const params = {};
	const events = [mediaType === "Music"
		? makePart(name + " 1", 0, 8, [{ pitch: 60, velocity: 0.8, start: 0, length: 1 }, { pitch: 64, velocity: 0.7, start: 1, length: 1 }])
		: { name: name + " 1", start: 0, length: 8, mediaType, speed: 1 }];
	return {
		name, mediaType, color: "FF000000", events,
		createIterator: () => { let i = 0; return { done: () => i >= events.length, next: () => events[i++] }; },
		channel: {
			channelType: mediaType === "Music" ? "MusicTrack" : "AudioTrack",
			findParameter: (n) => (params[n] = params[n] || { value: 0, setValue (v) { this.value = v; calls.push(["setValue", name, n, v]); } }),
		},
	};
}

const tracks = [makeTrack("Piano", "Music"), makeTrack("Vox", "Audio")];

const tparams = { tempo: 120, timeSignatureNum: 4, primaryTime: "0001.01.01.00" };
const transportPanel = { findParameter: (n) => (n in tparams ? {
	get string () { return String(tparams[n]); },
	setValue (v) { tparams[n] = v; calls.push(["transport", n, v]); },
	fromString (v) { tparams[n] = v; calls.push(["transport", n, v]); } } : null) };

function makeChannel (name, label, channelType, hasVolume) {
	const params = {};
	return { name, label, channelType, inserts: hasVolume ? {} : undefined,
		findParameter: (n) => (hasVolume || n !== "volume" ? (params[n] = params[n] || { value: 0,
			setValue (v) { this.value = v; calls.push(["setValue", label, n, v]); } }) : null) };
}
const mixerChannels = [makeChannel("Channel02", "St-1", "AudioSynth", true), makeChannel("Channel03", "St-1", "AudioSynth", true),
	makeChannel("Channel01", "Vox", "AudioTrack", true), makeChannel("Channel03", "Piano", "MusicTrack", false)];
const mixerConsole = { getChannelList: () => ({ numChannels: mixerChannels.length, getChannel: (i) => mixerChannels[i] }) };

const functions = {
	executeImmediately: false,
	newMediaTime: () => ({ musical: 0 }),
	addMediaTrack: (i, n, f) => { const t = makeTrack(n, f === "Instrument" ? "Music" : "Audio"); tracks.splice(i, 0, t); return t; },
	addTrack: (type, i, n) => { const t = makeTrack(n, type); tracks.splice(i, 0, t); return t; },
	addEventOnTrack: (t, e, s) => { const c = Object.assign({}, e, { start: s.musical }); t.events.push(c); calls.push(["addEventOnTrack", t.name, e.name, s.musical]); return c; },
	insertNewClip: (kind, s, e, t) => {
		calls.push(["insertNewClip", kind, s.musical, e.musical, t.name]);
		const p = makePart(t.name, s.musical, e.musical - s.musical, []);
		t.events.push(p);
		return p;
	},
	removeTrack: (t) => { const i = tracks.indexOf(t); if (i >= 0) tracks.splice(i, 1); calls.push(["removeTrack", t.name]); return true; },
	moveEvent: (e, b) => { e.start = b; calls.push(["moveEvent", b]); return true; },
	resizeEvent: (e, s, o, l) => { e.start = s; e.offset = o || 0; e.length = l || 0; calls.push(["resizeEvent", s, o, l]); return true; },
	muteEvent: (e, m) => { e.isMuted = m ? 1 : 0; return true; },
	removeEvent: (e) => { for (const t of tracks) { const i = t.events.indexOf(e); if (i >= 0) { t.events.splice(i, 1); calls.push(["removeEvent", t.name, e.start]); } } return true; },
	root: {
		environment: { find: (n) => (n === "TransportPanel" ? transportPanel : n === "MixerConsole" ? mixerConsole : null) },
		timeContext: { getTimeSignature: () => ({ numerator: 4, denominator: 4 }), ppqToSeconds: (b) => b * 0.5 },
		createFunctions: () => ({
			createEvent: () => ({ velocity: 0, start: 0, length: 0 }),
			insertEvent: (p, n) => { calls.push(["insertEvent"]); if (p.notes) p.notes.push(n); },
			deleteEvent: (n) => { for (const t of tracks) for (const e of t.events) if (e.notes) { const i = e.notes.indexOf(n); if (i >= 0) e.notes.splice(i, 1); } },
			modifyPitch: (n, v) => (n.pitch = v),
			modifyVelocity: (n, v) => (n.velocity = v),
			resizeEvent: (n, v) => (n.length = v),
			moveEvent: (n, v) => { n.start = v; calls.push(["note", n.pitch, n.start, n.length, n.velocity]); },
		}),
	},
};

const context = {
	functions,
	mainTrackList: { get numTracks () { return tracks.length; }, getTrack: (i) => tracks[i], getInsertPosition: () => tracks.length + 3 },
};

const Host = {
	Url: (u, f) => new Url(u, f),
	Results: { kResultOk: 0, kResultFailed: 1 },
	Interfaces: { IEditTask: "IEditTask", IComponent: "IComponent" },
	Console: { writeLine: (s) => console.error(s) },
	Engine: { TrackFormats: { findEqual: (n) => n } },
	GUI: { addIdleTask: (t) => idleTasks.push(t), removeIdleTask: () => {}, Commands: {
		findCommand: (c, n) => (c === "Track" && n === "AI Bridge - Run Jobs" ? { c, n } : null), interpretCommand: (c, n, chk, args) => { calls.push(["interpretCommand", c, n, args]); return true; } } },
	IO: {
		File: (url) => ({
			exists: () => fs.existsSync(url.p),
			moveTo: (dest) => { fs.renameSync(url.p, dest.p); return true; },
		}),
		findFiles: (folder, pattern) => {
			const ext = pattern.replace("*", "");
			const list = fs.readdirSync(folder.p).filter((n) => n.endsWith(ext)).map((n) => { const u = new Url(folder.p); u.descend(n); return u; });
			let i = 0;
			return { done: () => i >= list.length, next: () => list[i++] };
		},
		openTextFile: (url) => {
			const lines = fs.readFileSync(url.p, "utf8").split("\n");
			let i = 0;
			return { get endOfStream () { return i >= lines.length; }, readLine: () => lines[i++], close () {} };
		},
		createTextFile: (url) => {
			fs.mkdirSync(path.dirname(url.p), { recursive: true });
			let buf = "";
			return { writeLine: (s) => (buf += s + "\n"), close: () => fs.writeFileSync(url.p, buf) };
		},
	},
};

const sandbox = { Host, console, JSON, Date, Object, String, Error };
sandbox.include_file = (f) => vm.runInContext(fs.readFileSync(path.join(src, f), "utf8"), sandbox, { filename: f });
vm.createContext(sandbox);

if (mode === "service") {
	sandbox.include_file("service.js");
	const svc = vm.runInContext("createBridgeService()", sandbox);
	svc.initialize({});
} else {
	sandbox.include_file("tasks.js");
	const task = vm.runInContext(mode === "dump" ? "createDumpTask()" : "createRunJobsTask()", sandbox);
	task.prepareEdit(context);
	task.performEdit(context);
	// "runpoll": after a manual run, a job arriving later
	// is picked up by the idle task.
	if (mode === "runpoll") {
		calls.length = 0;
		fs.writeFileSync(path.join(userContent, "AIBridge", "inbox", "zz-late.json"), JSON.stringify({ id: "late", op: "ping" }));
		idleTasks.forEach((t) => t.onTimer());
	}
}
process.stdout.write(JSON.stringify(calls) + "\n");
