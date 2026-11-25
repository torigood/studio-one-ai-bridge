include_file ("bridge.js");

//************************************************************************************************
// RunJobsTask - executes all pending inbox jobs with the edit context of the active song.
// It runs only when pressed: the AI Bridge surface maps CC 119 to it. There is no poller:
// Host.GUI.addIdleTask with a script object crashed (at startup) or hung (later) Studio One 5.5.2.
//************************************************************************************************

function RunJobsTask ()
{
	this.interfaces = [Host.Interfaces.IEditTask];

	this.prepareEdit = function (context)
	{
		return Host.Results.kResultOk;
	}

	this.performEdit = function (context)
	{
		let jobs = pendingJobs ();
		for(let entry of jobs)
		{
			let result = entry.job.op === "invalid"
				? { ok: false, error: entry.job.error }
				: runJob (context, entry.job);
			finishJob (entry, result);
		}
		log ("ran " + jobs.length + " job(s)");
		return Host.Results.kResultOk;
	}
}

//************************************************************************************************
// OpenChatTask - opens the AI chat page in the browser. The chat agent (run.py chat) writes its URL to
// <mailbox>/chat_url.txt. Host.GUI.openUrl is used by Studio One's Macro panel with a file URL
// (re/extracted/macros/panel.js:916); an http URL works too (0.9.0 live, 2026-11-25).
//************************************************************************************************

function OpenChatTask ()
{
	this.interfaces = [Host.Interfaces.IEditTask];

	this.prepareEdit = function (context)
	{
		return Host.Results.kResultOk;
	}

	this.performEdit = function (context)
	{
		let url = null;
		try { url = readTextFile (mailboxUrl ("chat_url.txt", false)); } catch(e) {}
		url = url ? url.trim () : "";
		if(url.indexOf ("http://127.0.0.1:") === 0)
		{
			Host.GUI.openUrl (Host.Url (url));
			log ("opened chat");
		}
		else
			Host.GUI.alert ("The AI chat is not running. In Terminal: studio-one-bridge chat --enable");
		return Host.Results.kResultOk;
	}
}

//************************************************************************************************
// entry points
//************************************************************************************************

function createRunJobsTask ()
{
	return new RunJobsTask;
}

function createOpenChatTask ()
{
	return new OpenChatTask;
}
