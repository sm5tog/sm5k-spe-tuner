import os, sys, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spe_bridge import FakeBridge, TkBridge


class _Root:
    def __init__(self): self.calls = []
    def after(self, ms, fn): self.calls.append((ms, fn)); return "id"


def test_tkbridge_drain_runs_posted_in_order_from_thread():
    b = TkBridge(_Root()); out = []
    t = threading.Thread(target=lambda: [b.post(lambda i=i: out.append(i)) for i in range(5)])
    t.start(); t.join()
    assert out == []
    b.drain()
    assert out == [0, 1, 2, 3, 4]


def test_tkbridge_schedule_delegates_to_root():
    r = _Root(); b = TkBridge(r)
    b.schedule(250, print)
    assert r.calls == [(250, print)]


def test_fakebridge_post_is_synchronous_and_schedule_is_deferred():
    b = FakeBridge(); out = []
    b.post(lambda: out.append("p"))
    b.schedule(100, lambda: out.append("s"))
    assert out == ["p"]
    b.run_scheduled()
    assert out == ["p", "s"]
