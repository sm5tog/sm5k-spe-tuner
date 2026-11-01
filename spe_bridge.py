"""Brygga mellan bakgrundstrådar och GUI-tråden. Bara GUI-tråden anropar drain()."""
import queue

POLL_MS = 20   # hur ofta GUI-tråden hämtar postat arbete (ett tomt varv kostar under 0,1 ms)


class TkBridge:
    """post(fn) är trådsäker. schedule(ms, fn) och drain() anropas från GUI-tråden."""
    def __init__(self, root):
        self._root = root
        self._q = queue.Queue()

    def post(self, fn):
        self._q.put(fn)

    def schedule(self, ms, fn):
        return self._root.after(ms, fn)

    def drain(self):
        try:
            while True:
                self._q.get_nowait()()
        except queue.Empty:
            pass


class FakeBridge:
    """Testbrygga: post() kör direkt, schedule() sparas och körs med run_scheduled()."""
    def __init__(self):
        self.scheduled = []

    def post(self, fn):
        fn()

    def schedule(self, ms, fn):
        self.scheduled.append((ms, fn))

    def drain(self):
        pass

    def run_scheduled(self):
        pending, self.scheduled = self.scheduled, []
        for _, fn in pending:
            fn()
