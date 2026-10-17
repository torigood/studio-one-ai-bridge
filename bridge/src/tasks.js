include_file ("bridge.js");

//************************************************************************************************
// RunJobsTask - executes all pending inbox jobs with the edit context of the active song
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
		try { startPoller (); } catch(e) { log ("startPoller: " + e); }
		return Host.Results.kResultOk;
	}
}

//************************************************************************************************
// DumpTask - writes host-api.json for API discovery
//************************************************************************************************

function DumpTask ()
{
	this.interfaces = [Host.Interfaces.IEditTask];

	this.prepareEdit = function (context)
	{
		return Host.Results.kResultOk;
	}

	this.performEdit = function (context)
	{
		writeJson (null, "host-api.json", dumpObject (Host, "Host", 3));
		writeJson (null, "context-api.json", dumpObject (context, "context", 2));
		log ("wrote host-api.json and context-api.json");
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

function createDumpTask ()
{
	return new DumpTask;
}
