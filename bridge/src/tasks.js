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
// entry points
//************************************************************************************************

function createRunJobsTask ()
{
	return new RunJobsTask;
}
