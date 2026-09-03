"""Lightweight timing for the measurement-path pipeline (Milestone 3.14).

Why this exists
---------------
"Displaying a measurement line is slow" has four possible causes, and they
need completely different fixes:

    A. the exact solver (pygeodesic)
    B. a cache miss that forced A to run at all
    C. Blender curve construction
    D. depsgraph handler churn

Guessing between them is how a display-only change ends up being blamed on the
solver. Every stage of the path pipeline is therefore timed by name, the
samples are kept in a small ring buffer, and the researcher can read them back
without a profiler.

Rules this module follows, because they are the whole point:

* **It must never be able to spam the console.** Printing is off unless the
  researcher turns on Timing Log, and even then a repeating label prints at
  most once every ``LOG_INTERVAL_SECONDS``. A redraw-rate stage therefore
  costs one line every two seconds, not one line per frame.
* **It must never change behaviour.** ``time.perf_counter()`` twice and a
  deque append; no bpy access, no imports beyond the standard library, and
  every public entry point is safe to call when nothing is registered.
* **It is measurement, not accounting.** A stage that is not reached simply
  has no samples, which is itself the answer to "did this run at all".
"""

import collections
import time

#: How many samples to keep. Enough for a full interactive session's worth of
#: distinct stages without ever growing.
HISTORY = 64

#: Never print the same label more often than this, however often it runs.
LOG_INTERVAL_SECONDS = 2.0

#: Below this a stage is not worth a line even when logging is on. Kept small
#: enough that "cache validation took 0.4 ms" is still visible.
LOG_THRESHOLD_MS = 0.0

#: Stage names. Strings are used at the call sites, but naming them here keeps
#: the vocabulary in one place and makes a typo a missing stage rather than a
#: silently new one.
CACHE_VALIDATE = "cache-validate"
CACHE_LOAD = "cache-load"
CACHE_STORE = "cache-store"
SOLVER_BUILD = "solver-build"
PATH_SOLVE = "path-solve"
RESULT_COPY = "result-copy"
HELPER_CREATE = "helper-create"
HELPER_UPDATE = "helper-update"
HELPER_TRANSFORM = "helper-transform"
NORMAL_SAMPLE = "normal-sample"

_samples = collections.deque(maxlen=HISTORY)

#: Cumulative {label: [count, total_ms]}, deliberately NOT a ring buffer.
#: The stages that matter most are the rare expensive ones - a single 40 s
#: path solve - and a redraw-rate stage would evict them from any bounded
#: history within seconds, leaving the report saying the solver never ran.
_totals = {}
_last_log = {}
_enabled = False


def set_enabled(enabled):
    """Turn console logging on or off. Recording is always on; it is free."""
    global _enabled
    _enabled = bool(enabled)
    return _enabled


def enabled():
    return _enabled


def reset():
    """Forget every recorded sample and every total."""
    _samples.clear()
    _totals.clear()
    _last_log.clear()


def record(label, seconds, detail=""):
    """Store one measured stage, and log it if logging is on and due."""
    entry = {
        "label": str(label),
        "ms": float(seconds) * 1000.0,
        "detail": str(detail),
        "at": time.time(),
    }
    _samples.append(entry)
    running = _totals.setdefault(entry["label"], [0, 0.0])
    running[0] += 1
    running[1] += entry["ms"]
    if _enabled and entry["ms"] >= LOG_THRESHOLD_MS and _due(entry["label"]):
        print("[BSMT TIMING] %-18s %8.2f ms  %s"
              % (entry["label"], entry["ms"], entry["detail"]))
    return entry


def _due(label):
    """Throttle: True at most once per LOG_INTERVAL_SECONDS per label."""
    now = time.monotonic()
    previous = _last_log.get(label)
    if previous is not None and now - previous < LOG_INTERVAL_SECONDS:
        return False
    _last_log[label] = now
    return True


class stage(object):
    """Context manager timing one named stage.

    Deliberately records on the way out even when the body raised: a stage
    that failed after eight seconds is exactly the one worth seeing.
    """

    __slots__ = ("label", "detail", "_started", "seconds")

    def __init__(self, label, detail=""):
        self.label = label
        self.detail = detail
        self._started = 0.0
        self.seconds = 0.0

    def __enter__(self):
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.seconds = time.perf_counter() - self._started
        record(self.label, self.seconds, self.detail)
        return False

    def note(self, detail):
        """Add context discovered inside the block, e.g. a point count."""
        self.detail = str(detail)
        return self


def samples():
    """Every recorded sample, oldest first. Plain dicts; safe to keep."""
    return list(_samples)


def summary(limit=8):
    """The most recent stages as display lines, newest first.

    Returned as text rather than printed, so a panel can show it without the
    console being involved at all.
    """
    lines = []
    for entry in reversed(list(_samples)):
        line = "%-18s %8.2f ms" % (entry["label"], entry["ms"])
        if entry["detail"]:
            line += "  %s" % entry["detail"]
        lines.append(line)
        if len(lines) >= int(limit):
            break
    return lines


def totals():
    """{label: (count, total_ms)} for the whole session.

    This is what answers "is the slowness A, B, C or D": one expensive
    path-solve is a different picture from four hundred cheap helper updates
    that add up to the same number. Cumulative rather than windowed, so the
    one solve that cost forty seconds is still in the report after a thousand
    cheap redraws.
    """
    return {label: (count, total) for label, (count, total)
            in _totals.items()}
