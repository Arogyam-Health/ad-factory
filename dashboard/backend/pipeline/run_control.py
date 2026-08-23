from __future__ import annotations

import threading

import httpx

# Pipeline cancellation signal, keyed by run_id.
_cancel_events: dict[str, threading.Event] = {}
_cancel_current_run: threading.Event = threading.Event()
# Registry of subprocesses spawned by run_cmd() so they can be killed on cancel.
# Each entry: (run_id_or_None, subprocess.Popen)
_tracked_subprocesses: list[tuple[str | None, "subprocess.Popen[str]"]] = []
_tracked_subprocesses_lock = threading.Lock()
# Registry of active httpx clients so cancel can interrupt in-flight HTTP calls.
_active_httpx_clients: dict[int, httpx.Client] = {}
_active_httpx_clients_lock = threading.Lock()
def _register_subprocess(run_id: str | None, proc: "subprocess.Popen[str]") -> None:
    with _tracked_subprocesses_lock:
        _tracked_subprocesses.append((run_id, proc))


def _unregister_subprocess(proc: "subprocess.Popen[str]") -> None:
    with _tracked_subprocesses_lock:
        _tracked_subprocesses[:] = [(rid, p) for (rid, p) in _tracked_subprocesses if p is not proc]


def _kill_tracked_subprocesses(run_id: str | None) -> list[str]:
    """Kill all tracked subprocesses (optionally filtered by run_id). Returns list of killed commands."""
    killed: list[str] = []
    with _tracked_subprocesses_lock:
        snapshot = list(_tracked_subprocesses)
    for rid, proc in snapshot:
        if run_id is not None and rid is not None and rid != run_id:
            continue
        if proc.poll() is not None:
            continue
        try:
            cmd_str = " ".join(proc.args) if isinstance(proc.args, list) else str(proc.args)
        except Exception:
            cmd_str = "<unknown>"
        try:
            proc.kill()
            killed.append(cmd_str)
        except Exception:
            pass
    return killed


def _register_httpx_client(client: httpx.Client) -> None:
    with _active_httpx_clients_lock:
        _active_httpx_clients[threading.get_ident()] = client

def _unregister_httpx_client() -> None:
    with _active_httpx_clients_lock:
        _active_httpx_clients.pop(threading.get_ident(), None)

def _close_active_httpx_clients() -> None:
    with _active_httpx_clients_lock:
        for tid, client in list(_active_httpx_clients.items()):
            try:
                client.close()
            except Exception:
                pass
        _active_httpx_clients.clear()

def signal_cancel_run(run_id: str) -> None:
    ev = _cancel_events.get(run_id)
    if ev:
        ev.set()
    _kill_tracked_subprocesses(run_id)


def signal_cancel_current_run() -> None:
    _cancel_current_run.set()
    _close_active_httpx_clients()
    _kill_tracked_subprocesses(None)


def cancel_event_for_run(run_id: str) -> threading.Event:
    if run_id not in _cancel_events:
        _cancel_events[run_id] = threading.Event()
    return _cancel_events[run_id]
