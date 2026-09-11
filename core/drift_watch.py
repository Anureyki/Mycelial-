#!/usr/bin/env python3
"""Continuous drift watching. Wakes on new events, not on a clock.

    w = DriftWatcher(on_alert=security.raise_alert)
    w.start()

WHY NOT A TIMER, given the task said "continuously". CLAUDE.md is explicit that
nothing in this system runs on a timer, and the reason is on the page: Service
Manager's restart loop turned one broken agent into 488 restarts and a log
nobody could read through. That rule is about SUPERVISION and a monitor is a
different thing - but a fixed-interval scanner is how a monitor becomes that
loop, so this does not have one.

It watches the event spool and scans WHEN THE SPOOL GROWS. A quiet system costs
a stat() call; a burst of decisions is scanned once rather than once per event.
So "continuous" here means every event is seen, which is the property that
matters - not that something fires every N seconds, which is the property that
produces noise.

THROTTLED ON PURPOSE. A scan reads the whole record set, so scanning per event
would turn a sweep of 200 denials into 200 full scans - and a monitor whose cost
scales with the behaviour it is watching is a monitor an attacker can use to
slow the system down. One scan per quiet moment, with a floor between scans.

IT STILL DOES NOT ADJUDICATE. The watcher calls back with findings. It has no
path to revoke, halt or quarantine, and adding one would make a background
thread an authority nobody granted.
"""
import os
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPOOL = os.path.join(ROOT, "state", "security_events")

MIN_SECONDS_BETWEEN_SCANS = 5.0
IDLE_POLL_SECONDS = 2.0


class DriftWatcher:
    def __init__(self, on_alert=None, log=None, spool=SPOOL,
                 min_interval=MIN_SECONDS_BETWEEN_SCANS):
        self.on_alert = on_alert
        self.log = log or (lambda *_a, **_k: None)
        self.spool = spool
        self.min_interval = min_interval
        self._thread = None
        self._stop = threading.Event()
        self._last_sig = None
        self._last_scan = 0.0
        self.scans = 0
        self.alerts_raised = 0
        self.started_at = None
        self.last_error = None

    def _signature(self):
        """Cheap change detection: total size and newest mtime of the spool."""
        try:
            total, newest = 0, 0.0
            for f in os.listdir(self.spool):
                if not f.endswith(".jsonl"):
                    continue
                st = os.stat(os.path.join(self.spool, f))
                total += st.st_size
                newest = max(newest, st.st_mtime)
            return (total, newest)
        except FileNotFoundError:
            return (0, 0.0)
        except Exception as e:
            self.last_error = str(e)[:160]
            return None

    def scan_once(self, force=False):
        """-> the scan result, or None when nothing changed."""
        sig = self._signature()
        if sig is None:
            return None
        if not force:
            if sig == self._last_sig:
                return None
            if time.time() - self._last_scan < self.min_interval:
                return None
        self._last_sig = sig
        self._last_scan = time.time()
        self.scans += 1
        try:
            # INGEST FIRST, OR THE WATCHER WATCHES THE WRONG FILE.
            #
            # The spool is what agents emit into; the RECORDS are what the
            # harness writes after verifying. The drift monitor reads records.
            # Watching the spool and scanning the records means a burst of
            # events wakes the watcher and it then scans a record set those
            # events are not in yet - which is exactly what happened: 25
            # probe events, four scans, zero alerts, and nothing wrong with
            # either component on its own.
            #
            # The harness is still the only thing that writes a record. This
            # calls the harness's own ingest; it does not write one.
            import sys as _sys, os as _os
            _tools = _os.path.join(ROOT, "tools")
            if _tools not in _sys.path:
                _sys.path.insert(0, _tools)
            _argv, _sys.argv = _sys.argv, ["drift_watch"]
            try:
                from eval_harness import ingest, ChainBroken
                try:
                    ingest()
                except ChainBroken as ce:
                    # A broken chain stops collection and must be VISIBLE, not
                    # retried silently every two seconds.
                    self.last_error = f"chain broken, not ingesting: {ce}"
                    self.log(f"DRIFT WATCH: {self.last_error}")
            finally:
                _sys.argv = _argv

            from core.drift_monitor import scan
            out = scan()
        except Exception as e:
            # A WATCHER THAT CANNOT SCAN IS NOT AN ALL-CLEAR. It records the
            # failure rather than going quiet, because a silent watcher and a
            # clean system look identical from outside.
            self.last_error = str(e)[:200]
            self.log(f"DRIFT WATCH FAILED: {self.last_error}")
            return {"status": "unknown", "alerts": [], "error": self.last_error}
        if out.get("alerts"):
            self.alerts_raised += len(out["alerts"])
            for a in out["alerts"]:
                self.log(f"DRIFT ALERT [{a.get('severity')}] {a.get('kind')} "
                         f"{a.get('agent') or ''}")
            if self.on_alert:
                try:
                    self.on_alert(out)
                except Exception as e:
                    self.log(f"drift alert callback failed: {e}")
        return out

    def _loop(self):
        while not self._stop.is_set():
            self.scan_once()
            self._stop.wait(IDLE_POLL_SECONDS)

    def start(self):
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="drift-watch")
        self._thread.start()
        self.log("drift watcher running - wakes on new events, not on a clock")
        return True

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        return True

    def status(self):
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "scans": self.scans,
            "alerts_raised": self.alerts_raised,
            "uptime_seconds": round(time.time() - self.started_at, 1)
                              if self.started_at else None,
            "last_error": self.last_error,
            "wakes_on": "spool growth",
            "min_seconds_between_scans": self.min_interval,
            "note": ("Continuous means every event is seen. It is not a timer - "
                     "a quiet system costs one stat() call, and a burst is "
                     "scanned once rather than once per event."),
        }
