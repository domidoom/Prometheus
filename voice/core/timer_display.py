"""Background poller that keeps the Jarvis UI timer widget in sync.

The widget renders a single timer at a time (the most urgent), so this
module inspects both the break ``TimerTool`` and pending ``ReminderTool``
entries each tick and pushes whichever will fire soonest to the window.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger("jarvis.timer_display")


class TimerDisplay:
    """Poll the assistant's timer/reminder state and drive the UI widget.

    The class is duck-typed on ``assistant``: it only reads ``assistant.timer``
    (expected to expose ``is_running()``, ``get_remaining()``, and an
    ``_end_time`` datetime for sub-minute precision) and
    ``assistant.reminder.reminders`` (a ``{id: {"at", "text", "fired"}}``
    dict). It never touches the LLM or any other tools.
    """

    def __init__(self, window, assistant, tick_sec: float = 1.0):
        """Create a display poller.

        Args:
            window: JarvisWindow (needs ``set_timer`` / ``clear_timer``).
            assistant: Object exposing ``.timer`` and ``.reminder``.
            tick_sec: Poll interval in seconds.
        """
        self._window = window
        self._assistant = assistant
        self._tick_sec = max(0.1, float(tick_sec))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Track the last id we pushed so we can clear it when nothing is active.
        self._last_pushed_id: Optional[str] = None

    def start(self) -> None:
        """Start the background thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="jarvis-timer-display",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread to exit and join briefly."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _run(self) -> None:
        """Poll loop body. Survives exceptions per tick."""
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("timer_display tick raised")
            # Using Event.wait gives us a responsive shutdown.
            if self._stop_event.wait(self._tick_sec):
                break

    def _tick(self) -> None:
        """Inspect state once and push the most-urgent candidate (or clear)."""
        best_id: Optional[str] = None
        best_label: Optional[str] = None
        best_seconds: Optional[int] = None

        # --- Break timer candidate ---
        timer = getattr(self._assistant, "timer", None)
        if timer is not None:
            try:
                if timer.is_running():
                    seconds = self._break_seconds_remaining(timer)
                    if seconds is not None and seconds >= 0:
                        best_id = "break"
                        best_label = "BREAK"
                        best_seconds = seconds
            except Exception:
                logger.exception("failed to read break timer state")

        # --- Reminder candidates ---
        reminder = getattr(self._assistant, "reminder", None)
        if reminder is not None:
            reminders = getattr(reminder, "reminders", None) or {}
            now = datetime.now()
            # Snapshot to tolerate concurrent mutation from the reminder thread.
            try:
                items = list(reminders.items())
            except Exception:
                items = []
            for rid, entry in items:
                try:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("fired"):
                        continue
                    at_raw = entry.get("at")
                    if not at_raw:
                        continue
                    at = datetime.fromisoformat(at_raw)
                    seconds = int((at - now).total_seconds())
                    if seconds < 0:
                        # Already-due but not yet fired — still show as 0.
                        seconds = 0
                    if best_seconds is None or seconds < best_seconds:
                        text = str(entry.get("text", "reminder"))
                        label = text[:24]
                        best_id = str(rid)
                        best_label = label
                        best_seconds = seconds
                except Exception:
                    logger.exception(
                        "skipping malformed reminder %r", rid
                    )
                    continue

        # --- Push or clear ---
        if best_id is not None and best_seconds is not None and best_label is not None:
            try:
                self._window.set_timer(best_id, best_label, int(best_seconds))
                self._last_pushed_id = best_id
            except Exception:
                logger.exception("set_timer failed")
        else:
            # Nothing active — clear whatever we last pushed.
            if self._last_pushed_id is not None:
                try:
                    self._window.clear_timer(self._last_pushed_id)
                except Exception:
                    logger.exception("clear_timer failed")
                self._last_pushed_id = None

    @staticmethod
    def _break_seconds_remaining(timer) -> Optional[int]:
        """Best-effort seconds-remaining for the break timer.

        Prefers ``timer._end_time`` (a ``datetime``) for sub-minute precision;
        falls back to ``timer.get_remaining() * 60`` (minute resolution).
        """
        end_time = getattr(timer, "_end_time", None)
        if isinstance(end_time, datetime):
            seconds = int((end_time - datetime.now()).total_seconds())
            return max(0, seconds)
        try:
            minutes = timer.get_remaining()
        except Exception:
            return None
        if minutes is None:
            return None
        return max(0, int(minutes) * 60)
