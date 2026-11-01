import os, sys, queue, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spe_net import command_writer, start_command_writer


def test_command_sent_immediately():
    q = queue.Queue(); got = queue.Queue()
    stop, t = start_command_writer(lambda c: got.put((time.perf_counter(), c)), q)
    time.sleep(0.05)
    t0 = time.perf_counter(); q.put("a")
    ts, c = got.get(timeout=1)
    assert c == "a"
    assert ts - t0 < 0.05
    stop.set(); t.join(1)


def test_heartbeat_and_commands_share_one_sender():
    q = queue.Queue(); sent = []
    stop, t = start_command_writer(sent.append, q, heartbeat=lambda: "hb", heartbeat_every=0.05)
    time.sleep(0.18)
    q.put("x")
    time.sleep(0.1)
    stop.set(); t.join(1)
    assert sent.count("hb") >= 3
    assert "x" in sent


def test_send_error_stops_writer_and_reports():
    q = queue.Queue(); errs = []
    def bad(c): raise OSError("dead")
    stop = threading.Event()
    th = threading.Thread(target=command_writer, args=(bad, q, stop, None, 1.0, errs.append), daemon=True)
    th.start(); q.put("a"); th.join(1)
    assert not th.is_alive()
    assert len(errs) == 1 and isinstance(errs[0], OSError)


def test_stop_ends_writer_without_commands():
    q = queue.Queue()
    stop, t = start_command_writer(lambda c: None, q)
    stop.set(); t.join(1)
    assert not t.is_alive()
