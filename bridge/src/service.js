include_file ("bridge.js");

//************************************************************************************************
// BridgeService - FrameworkService loaded at startup (pattern: macros/service.js)
// Only writes status.json. Keep startup minimal: registering an idle task here
// (0.2.0/0.2.1) crashed Studio One right after launch. Polling is started later,
// from the "AI Bridge - Run Jobs" edit task (see startPoller in bridge.js).
//************************************************************************************************

function BridgeService ()
{
	this.interfaces = [Host.Interfaces.IComponent];

	this.initialize = function (context)
	{
		try
		{
			writeJson (null, "status.json", {
				loaded: true,
				version: kBridgeVersion,
				protocol: kProtocol,
				started: new Date ().toISOString ()
			});
		}
		catch(e) {}
		return Host.Results.kResultOk;
	}

	this.terminate = function ()
	{
		try { writeJson (null, "status.json", { loaded: false, version: kBridgeVersion, stopped: new Date ().toISOString () }); } catch(e) {}
		return Host.Results.kResultOk;
	}
}

function createBridgeService ()
{
	return new BridgeService;
}
