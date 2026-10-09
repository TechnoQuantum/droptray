"""Concurrent history saves must neither fail nor lose records (two uploads finishing together)."""
import os
import sys
import tempfile
import threading
import time

tmp = tempfile.mkdtemp()
os.environ["APPDATA"] = tmp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402

store = core.Store()
errors = []


def worker(n):
    for i in range(25):
        try:
            store.add_record({"name": f"f{n}-{i}", "size": 1, "seconds": 0.1, "peak": 10, "sender": "t",
                              "when": time.time(), "status": "received", "path": ""})
        except Exception as e:
            errors.append(e)


threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
[t.start() for t in threads]
[t.join() for t in threads]
assert not errors, errors
reloaded = core.Store()
assert len(reloaded.records) == 150 and reloaded.totals["files"] == 150, (len(reloaded.records), reloaded.totals)
print("concurrent history saves: 150/150 kept, no errors")
