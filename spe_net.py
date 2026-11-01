"""Nätverkshjälpare utan GUI-import: skrivartråd som skickar köade kommandon direkt."""
import queue
import threading
import time


def command_writer(send, cmd_queue, stop, heartbeat=None, heartbeat_every=1.0, on_error=None):
    """Blockerar på kön och skickar varje kommando så fort det läggs in (inte först när
    en läsning timar ut). heartbeat() ger meddelandet som skickas var heartbeat_every:e sekund.
    Slutar när stop sätts eller send() kastar (on_error anropas då med undantaget)."""
    next_hb = time.monotonic()
    while not stop.is_set():
        wait = 0.25
        if heartbeat is not None:
            wait = max(0.0, min(wait, next_hb - time.monotonic()))
        try:
            cmd = cmd_queue.get(timeout=wait)
        except queue.Empty:
            cmd = None
        try:
            if cmd is not None:
                send(cmd)
            if heartbeat is not None and time.monotonic() >= next_hb:
                send(heartbeat())
                next_hb = time.monotonic() + heartbeat_every
        except Exception as e:
            if on_error is not None:
                on_error(e)
            return


def start_command_writer(send, cmd_queue, heartbeat=None, heartbeat_every=1.0, on_error=None):
    """Startar command_writer i en daemon-tråd. Returnerar (stop_event, tråd)."""
    stop = threading.Event()
    t = threading.Thread(target=command_writer, daemon=True,
                         args=(send, cmd_queue, stop, heartbeat, heartbeat_every, on_error))
    t.start()
    return stop, t
