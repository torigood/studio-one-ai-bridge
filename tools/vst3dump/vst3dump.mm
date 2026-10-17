// vst3dump: print the parameters of a VST3 plug-in as JSON (macOS).
//
//   vst3dump /Library/Audio/Plug-Ins/VST3/Vital.vst3
//
// For every audio module class in the bundle: create the component, its edit controller,
// pass the component state to the controller, then list getParameterInfo () for each
// parameter (id, title, units, step count, default) plus the default value as display text.
// Studio One addresses these as "vstp<id>" (see docs/re-findings.md).
//
// Only the MIT-licensed VST3 interface headers are used (third_party/pluginterfaces).

#define INIT_CLASS_IID
#include "pluginterfaces/base/funknown.h"
#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/base/ipluginbase.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivsthostapplication.h"
#include "pluginterfaces/vst/ivstmessage.h"

#include <CoreFoundation/CoreFoundation.h>
#include <atomic>
#include <map>
#include <cstdio>
#include <cstring>
#include <unistd.h>
#include <string>
#include <vector>

using namespace Steinberg;
using namespace Steinberg::Vst;

// ---- tiny COM helpers ------------------------------------------------------

#define SIMPLE_REFCOUNT                                                     \
	std::atomic<int32> refs {1};                                            \
	uint32 PLUGIN_API addRef () override { return ++refs; }                 \
	uint32 PLUGIN_API release () override                                   \
	{                                                                       \
		int32 r = --refs;                                                   \
		if(r == 0)                                                          \
			delete this;                                                    \
		return r;                                                           \
	}

struct AttrList : IAttributeList
{
	SIMPLE_REFCOUNT
	std::map<std::string, int64> ints;
	std::map<std::string, double> floats;
	std::map<std::string, std::vector<char16>> strings;
	std::map<std::string, std::vector<char>> bins;
	tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
	{
		if(FUnknownPrivate::iidEqual (iid, FUnknown::iid) || FUnknownPrivate::iidEqual (iid, IAttributeList::iid))
		{
			addRef ();
			*obj = this;
			return kResultOk;
		}
		*obj = nullptr;
		return kNoInterface;
	}
	tresult PLUGIN_API setInt (AttrID id, int64 v) override { ints[id] = v; return kResultOk; }
	tresult PLUGIN_API getInt (AttrID id, int64& v) override
	{
		auto it = ints.find (id);
		if(it == ints.end ()) return kResultFalse;
		v = it->second;
		return kResultOk;
	}
	tresult PLUGIN_API setFloat (AttrID id, double v) override { floats[id] = v; return kResultOk; }
	tresult PLUGIN_API getFloat (AttrID id, double& v) override
	{
		auto it = floats.find (id);
		if(it == floats.end ()) return kResultFalse;
		v = it->second;
		return kResultOk;
	}
	tresult PLUGIN_API setString (AttrID id, const TChar* str) override
	{
		std::vector<char16> v;
		for(int i = 0; str && str[i]; i++) v.push_back (str[i]);
		v.push_back (0);
		strings[id] = v;
		return kResultOk;
	}
	tresult PLUGIN_API getString (AttrID id, TChar* out, uint32 sizeInBytes) override
	{
		auto it = strings.find (id);
		if(it == strings.end ()) return kResultFalse;
		uint32 n = std::min<uint32> ((uint32)it->second.size (), sizeInBytes / sizeof (TChar));
		if(n == 0) return kResultFalse;
		memcpy (out, it->second.data (), n * sizeof (TChar));
		out[n - 1] = 0;
		return kResultOk;
	}
	tresult PLUGIN_API setBinary (AttrID id, const void* data, uint32 size) override
	{
		bins[id] = std::vector<char> ((const char*)data, (const char*)data + size);
		return kResultOk;
	}
	tresult PLUGIN_API getBinary (AttrID id, const void*& data, uint32& size) override
	{
		auto it = bins.find (id);
		if(it == bins.end ()) return kResultFalse;
		data = it->second.data ();
		size = (uint32)it->second.size ();
		return kResultOk;
	}
};

struct Message : IMessage
{
	SIMPLE_REFCOUNT
	std::string msgId;
	AttrList* attrs = new AttrList;
	~Message () { attrs->release (); }
	tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
	{
		if(FUnknownPrivate::iidEqual (iid, FUnknown::iid) || FUnknownPrivate::iidEqual (iid, IMessage::iid))
		{
			addRef ();
			*obj = this;
			return kResultOk;
		}
		*obj = nullptr;
		return kNoInterface;
	}
	FIDString PLUGIN_API getMessageID () override { return msgId.c_str (); }
	void PLUGIN_API setMessageID (FIDString id) override { msgId = id ? id : ""; }
	IAttributeList* PLUGIN_API getAttributes () override { return attrs; }
};

struct HostApp : IHostApplication
{
	SIMPLE_REFCOUNT
	tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
	{
		if(FUnknownPrivate::iidEqual (iid, FUnknown::iid) || FUnknownPrivate::iidEqual (iid, IHostApplication::iid))
		{
			addRef ();
			*obj = this;
			return kResultOk;
		}
		*obj = nullptr;
		return kNoInterface;
	}
	tresult PLUGIN_API getName (String128 name) override
	{
		const char* n = "vst3dump";
		for(int i = 0; ; i++)
		{
			name[i] = (char16)n[i];
			if(!n[i])
				break;
		}
		return kResultOk;
	}
	tresult PLUGIN_API createInstance (TUID cid, TUID iid, void** obj) override
	{
		// JUCE plug-ins pass their processor to the controller in an IMessage created by the host.
		if(FUnknownPrivate::iidEqual (cid, IMessage::iid) && FUnknownPrivate::iidEqual (iid, IMessage::iid))
		{
			*obj = static_cast<IMessage*> (new Message);
			return kResultOk;
		}
		if(FUnknownPrivate::iidEqual (cid, IAttributeList::iid) && FUnknownPrivate::iidEqual (iid, IAttributeList::iid))
		{
			*obj = static_cast<IAttributeList*> (new AttrList);
			return kResultOk;
		}
		*obj = nullptr;
		return kNotImplemented;
	}
};

struct MemStream : IBStream
{
	SIMPLE_REFCOUNT
	std::vector<char> data;
	int64 pos = 0;
	tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override
	{
		if(FUnknownPrivate::iidEqual (iid, FUnknown::iid) || FUnknownPrivate::iidEqual (iid, IBStream::iid))
		{
			addRef ();
			*obj = this;
			return kResultOk;
		}
		*obj = nullptr;
		return kNoInterface;
	}
	tresult PLUGIN_API read (void* buffer, int32 n, int32* got) override
	{
		int32 k = (int32)std::max<int64> (0, std::min<int64> (n, (int64)data.size () - pos));
		if(k > 0)
			memcpy (buffer, data.data () + pos, k);
		pos += k;
		if(got)
			*got = k;
		return kResultOk;
	}
	tresult PLUGIN_API write (void* buffer, int32 n, int32* done) override
	{
		if(pos + n > (int64)data.size ())
			data.resize (pos + n);
		memcpy (data.data () + pos, buffer, n);
		pos += n;
		if(done)
			*done = n;
		return kResultOk;
	}
	tresult PLUGIN_API seek (int64 p, int32 mode, int64* result) override
	{
		int64 base = mode == kIBSeekSet ? 0 : mode == kIBSeekCur ? pos : (int64)data.size ();
		pos = std::max<int64> (0, base + p);
		if(result)
			*result = pos;
		return kResultOk;
	}
	tresult PLUGIN_API tell (int64* p) override
	{
		if(p)
			*p = pos;
		return kResultOk;
	}
};

// ---- JSON output -------------------------------------------------------------

static std::string utf8 (const char16* s)
{
	std::string out;
	for(int i = 0; s[i]; i++)
	{
		uint32 c = s[i];
		if(c >= 0xD800 && c < 0xDC00 && s[i + 1] >= 0xDC00 && s[i + 1] < 0xE000)
			c = 0x10000 + ((c - 0xD800) << 10) + (s[++i] - 0xDC00);
		if(c < 0x80)
			out += (char)c;
		else if(c < 0x800)
			out += (char)(0xC0 | (c >> 6)), out += (char)(0x80 | (c & 0x3F));
		else if(c < 0x10000)
			out += (char)(0xE0 | (c >> 12)), out += (char)(0x80 | ((c >> 6) & 0x3F)), out += (char)(0x80 | (c & 0x3F));
		else
			out += (char)(0xF0 | (c >> 18)), out += (char)(0x80 | ((c >> 12) & 0x3F)),
				out += (char)(0x80 | ((c >> 6) & 0x3F)), out += (char)(0x80 | (c & 0x3F));
	}
	return out;
}

static std::string jstr (const std::string& s)
{
	std::string o = "\"";
	for(unsigned char c : s)
	{
		if(c == '"' || c == '\\')
			o += '\\', o += (char)c;
		else if(c < 0x20)
		{
			char b[8];
			snprintf (b, sizeof b, "\\u%04x", c);
			o += b;
		}
		else
			o += (char)c;
	}
	return o + "\"";
}

static std::string uidString (const TUID id)
{
	FUID f = FUID::fromTUID (id);
	char8 s[64];
	f.toRegistryString (s);  // {XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}
	return s;
}

// ---- main --------------------------------------------------------------------

// Plug-ins may log to stdout; JSON goes to a private copy of the original stdout.
static FILE* OUT = stdout;

typedef bool (*BundleEntryFn) (CFBundleRef);
typedef bool (*BundleExitFn) ();
typedef IPluginFactory* (*GetFactoryFn) ();

int main (int argc, char** argv)
{
	if(argc < 2)
	{
		fprintf (stderr, "usage: vst3dump <plugin.vst3>\n");
		return 2;
	}
	OUT = fdopen (dup (1), "w");
	dup2 (2, 1);
	CFStringRef path = CFStringCreateWithCString (nullptr, argv[1], kCFStringEncodingUTF8);
	CFURLRef url = CFURLCreateWithFileSystemPath (nullptr, path, kCFURLPOSIXPathStyle, true);
	CFBundleRef bundle = CFBundleCreate (nullptr, url);
	if(!bundle || !CFBundleLoadExecutable (bundle))
	{
		fprintf (OUT, "{\"error\": \"cannot load bundle\"}\n");
		return 1;
	}
	auto entry = (BundleEntryFn)CFBundleGetFunctionPointerForName (bundle, CFSTR ("bundleEntry"));
	auto exitFn = (BundleExitFn)CFBundleGetFunctionPointerForName (bundle, CFSTR ("bundleExit"));
	auto getFactory = (GetFactoryFn)CFBundleGetFunctionPointerForName (bundle, CFSTR ("GetPluginFactory"));
	if(entry && !entry (bundle))
	{
		fprintf (OUT, "{\"error\": \"bundleEntry failed\"}\n");
		return 1;
	}
	IPluginFactory* factory = getFactory ? getFactory () : nullptr;
	if(!factory)
	{
		fprintf (OUT, "{\"error\": \"no plugin factory\"}\n");
		return 1;
	}
	HostApp* host = new HostApp;

	fprintf (OUT, "{\"bundle\": %s, \"classes\": [", jstr (argv[1]).c_str ());
	bool firstClass = true;
	for(int32 ci = 0; ci < factory->countClasses (); ci++)
	{
		PClassInfo info;
		if(factory->getClassInfo (ci, &info) != kResultOk || strcmp (info.category, kVstAudioEffectClass) != 0)
			continue;
		IComponent* comp = nullptr;
		if(factory->createInstance (info.cid, IComponent::iid, (void**)&comp) != kResultOk || !comp)
			continue;
		comp->initialize (host);

		IEditController* ctrl = nullptr;
		bool separate = false;
		if(comp->queryInterface (IEditController::iid, (void**)&ctrl) != kResultOk || !ctrl)
		{
			TUID ccid;
			if(comp->getControllerClassId (ccid) == kResultOk &&
			   factory->createInstance (ccid, IEditController::iid, (void**)&ctrl) == kResultOk && ctrl)
			{
				ctrl->initialize (host);
				separate = true;
			}
		}

		IConnectionPoint* compCP = nullptr;
		IConnectionPoint* ctrlCP = nullptr;
		if(ctrl && separate)
		{
			comp->queryInterface (IConnectionPoint::iid, (void**)&compCP);
			ctrl->queryInterface (IConnectionPoint::iid, (void**)&ctrlCP);
			if(compCP && ctrlCP)
			{
				compCP->connect (ctrlCP);
				ctrlCP->connect (compCP);
			}
		}
		fprintf (OUT, "%s{\"name\": %s, \"class_id\": %s, \"params\": [", firstClass ? "" : ", ",
		        jstr (info.name).c_str (), jstr (uidString (info.cid)).c_str ());
		firstClass = false;
		if(ctrl)
		{
			MemStream* st = new MemStream;
			if(comp->getState (st) == kResultOk)
			{
				st->pos = 0;
				ctrl->setComponentState (st);
			}
			st->release ();

			int32 n = ctrl->getParameterCount ();
			fprintf (stderr, "class %s: separate=%d params=%d\n", info.name, (int)separate, (int)n);
			for(int32 i = 0; i < n; i++)
			{
				ParameterInfo pi {};
				if(ctrl->getParameterInfo (i, pi) != kResultOk)
					continue;
				String128 disp {};
				std::string d;
				if(ctrl->getParamStringByValue (pi.id, ctrl->getParamNormalized (pi.id), disp) == kResultOk)
					d = utf8 (disp);
				fprintf (OUT, "%s\n  {\"id\": %u, \"title\": %s, \"short\": %s, \"units\": %s, \"steps\": %d, "
				        "\"default\": %.6g, \"value\": %.6g, \"display\": %s, \"automatable\": %s, "
				        "\"readonly\": %s, \"hidden\": %s, \"program_change\": %s, \"bypass\": %s}",
				        i ? "," : "", (unsigned)pi.id, jstr (utf8 (pi.title)).c_str (),
				        jstr (utf8 (pi.shortTitle)).c_str (), jstr (utf8 (pi.units)).c_str (), pi.stepCount,
				        pi.defaultNormalizedValue, ctrl->getParamNormalized (pi.id), jstr (d).c_str (),
				        (pi.flags & ParameterInfo::kCanAutomate) ? "true" : "false",
				        (pi.flags & ParameterInfo::kIsReadOnly) ? "true" : "false",
				        (pi.flags & ParameterInfo::kIsHidden) ? "true" : "false",
				        (pi.flags & ParameterInfo::kIsProgramChange) ? "true" : "false",
				        (pi.flags & ParameterInfo::kIsBypass) ? "true" : "false");
			}
			if(compCP && ctrlCP)
			{
				compCP->disconnect (ctrlCP);
				ctrlCP->disconnect (compCP);
			}
			if(compCP) compCP->release ();
			if(ctrlCP) ctrlCP->release ();
			if(separate)
			{
				ctrl->terminate ();
			}
			ctrl->release ();
		}
		fprintf (OUT, "]}");
		comp->terminate ();
		comp->release ();
	}
	fprintf (OUT, "]}\n");
	fflush (OUT);
	factory->release ();
	host->release ();
	if(exitFn)
		exitFn ();
	// Some plug-ins leave threads behind; exit without running their static destructors.
	_exit (0);
}
