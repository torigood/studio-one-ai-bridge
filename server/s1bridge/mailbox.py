"""File mailbox shared with the in-DAW bridge (bridge/src/bridge.js).

Layout under the mailbox root (default ~/Music/Studio One 5/AIBridge on macOS, i.e.
Studio One's local://$USERCONTENT/AIBridge):
  inbox/<id>.json    job written here: {"id","op","args","created","protocol"}
  outbox/<id>.json   result written by the bridge: {"id","ok","result"|"error","finished"}
  done/              unused (File.moveTo did not work inside Studio One)
  status.json        written by the bridge service at startup / shutdown
  host-api*.json     API discovery dumps

Folders are created only when a job is enqueued.
"""
import json
import os
import time
import uuid

from . import config

PROTOCOL = 1


def default_root():
    return os.path.expanduser(os.environ.get("S1BRIDGE_MAILBOX") or config.MAILBOX)


class Mailbox:
    def __init__(self, root=None):
        self.root = root or default_root()

    def _dir(self, name):
        return os.path.join(self.root, name)

    def ensure(self):
        for d in ("inbox", "outbox", "done"):
            os.makedirs(self._dir(d), exist_ok=True)

    def enqueue(self, op, args=None):
        self.ensure()
        # The bridge runs jobs in file-name order, so ids must sort in enqueue order.
        job_id = "%020d-%s" % (time.time_ns(), uuid.uuid4().hex[:6])
        job = {"id": job_id, "op": op, "args": args or {}, "protocol": PROTOCOL,
               "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
        final = os.path.join(self._dir("inbox"), job_id + ".json")
        tmp = final + ".tmp"  # bridge only picks up *.json
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(job, ensure_ascii=False))
        os.replace(tmp, final)
        return job_id

    def result(self, job_id, consume=True):
        """Result for job_id, or None. A job is done once outbox/<id>.json exists;
        consuming it removes the inbox file so the bridge stops seeing it."""
        path = os.path.join(self._dir("outbox"), job_id + ".json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8-sig") as f:
            text = f.read().strip()
        try:
            data = json.loads(text)
        except ValueError:
            return None  # the bridge is still writing it (no atomic rename in Host.IO)
        if consume:
            job = os.path.join(self._dir("inbox"), job_id + ".json")
            if os.path.exists(job):
                os.remove(job)
        return data

    def wait(self, job_id, timeout):
        deadline = time.time() + max(0.0, timeout)
        while True:
            r = self.result(job_id)
            if r is not None or time.time() >= deadline:
                return r
            time.sleep(0.25)

    def cancel(self, job_id):
        path = os.path.join(self._dir("inbox"), job_id + ".json")
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def pending(self):
        """Inbox jobs that have no result yet."""
        d = self._dir("inbox")
        if not os.path.isdir(d):
            return []
        return sorted(n[:-5] for n in os.listdir(d) if n.endswith(".json")
                      and not os.path.exists(os.path.join(self._dir("outbox"), n)))

    def cleanup(self, keep_results=3600, stale_jobs=600):
        """Remove what nobody will read: results older than keep_results seconds (and their job files),
        and jobs older than stale_jobs seconds that never got a result (they would otherwise run the next
        time Studio One runs jobs). Returns the number of files removed. Errors are ignored."""
        now, removed = time.time(), 0
        inbox, outbox = self._dir("inbox"), self._dir("outbox")
        for folder, limit, needs_result in ((outbox, keep_results, False), (inbox, stale_jobs, None)):
            if not os.path.isdir(folder):
                continue
            for n in os.listdir(folder):
                if not n.endswith(".json"):
                    continue
                path = os.path.join(folder, n)
                try:
                    old = now - os.path.getmtime(path) > limit
                    answered = folder == inbox and os.path.exists(os.path.join(outbox, n))
                    if old or answered:
                        os.remove(path)
                        removed += 1
                        if folder == outbox:
                            job = os.path.join(inbox, n)
                            if os.path.exists(job):
                                os.remove(job)
                                removed += 1
                except OSError:
                    pass
        return removed

    def read_json(self, name):
        path = os.path.join(self.root, name)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8-sig") as f:
            return json.loads(f.read().strip())
