"""Main UI window with big red button."""

import threading
from pathlib import Path
from typing import Callable, Optional

import tkinter as tk


class BigRedButtonWindow:
    """Main application window - text-free for blind accessibility.

    The entire window is a single clickable region.
    Visual feedback is minimal since the user is blind.
    A sighted helper can see the red circle and status dot.
    """

    def __init__(
        self,
        on_button_press: Callable,
        on_button_release: Optional[Callable] = None,
        width: int = 400,
        height: int = 400,
    ):
        self.on_button_press = on_button_press
        self.on_button_release = on_button_release
        self.is_listening = False
        self.pulsing = False
        self._scheduled_tasks: list = []

        self.root = tk.Tk()
        self.root.title("Jarvis")
        self.root.geometry(f"{width}x{height}")
        self.root.configure(bg="black")

        self.root.attributes("-fullscreen", False)
        self.root.bind("<F11>", self._toggle_fullscreen)
        self.root.bind("<Escape>", self._exit_fullscreen)

        self._center_window(width, height)

        self.bg_color = "black"
        self.button_color = "#DC143C"
        self.button_hover = "#B22222"
        self.button_active = "#8B0000"
        self.pulse_color = "#FF6B6B"

        self._create_widgets()
        self._setup_bindings()

    def _center_window(self, width: int, height: int):
        """Center window on screen."""
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _create_widgets(self):
        """Create UI widgets - no text, just color."""
        self.main_frame = tk.Frame(self.root, bg=self.bg_color)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(
            self.main_frame,
            bg=self.bg_color,
            highlightthickness=0,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Single big red circle - entire window is effectively the button
        self.button_radius = 140
        self.center_x = 200
        self.center_y = 200

        self.button = self.canvas.create_oval(
            self.center_x - self.button_radius,
            self.center_y - self.button_radius,
            self.center_x + self.button_radius,
            self.center_y + self.button_radius,
            fill=self.button_color,
            outline="",
            tags="button",
        )

        # Small status indicator at top (for sighted helper)
        self.status_indicator = self.canvas.create_oval(
            self.center_x - 10,
            20,
            self.center_x + 10,
            40,
            fill="gray",
            outline="",
        )

    def _setup_bindings(self):
        """Setup event bindings - entire window is clickable."""
        # Click anywhere in canvas (canvas fills the window)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        # Hover on the circle only
        self.canvas.tag_bind("button", "<Enter>", self._on_enter)
        self.canvas.tag_bind("button", "<Leave>", self._on_leave)

        # Keyboard shortcuts (on root so they work anywhere)
        self.root.bind("<space>", self._on_press)
        self.root.bind("<KeyRelease-space>", self._on_release)
        self.root.bind("<Return>", self._on_press)
        self.root.bind("<KeyRelease-Return>", self._on_release)

    def _on_press(self, event=None):
        """Handle button press - always fires, app decides state."""
        self.canvas.itemconfig(self.button, fill=self.button_active)

        if self.on_button_press:
            self.on_button_press()

    def _on_release(self, event=None):
        """Handle button release."""
        if not self.is_listening:
            self.canvas.itemconfig(self.button, fill=self.button_color)

        if self.on_button_release:
            self.on_button_release()

    def _on_enter(self, event):
        """Handle mouse enter."""
        if not self.is_listening:
            self.canvas.itemconfig(self.button, fill=self.button_hover)

    def _on_leave(self, event):
        """Handle mouse leave."""
        if not self.is_listening:
            self.canvas.itemconfig(self.button, fill=self.button_color)

    def _update_status(self, state: str):
        """Update status indicator color."""
        colors = {
            "idle": "gray",
            "listening": "green",
            "processing": "yellow",
            "speaking": "blue",
            "error": "red",
        }
        self.canvas.itemconfig(self.status_indicator, fill=colors.get(state, "gray"))

    def set_listening_state(self, listening: bool):
        """Set listening state externally (threadsafe)."""
        self.root.after(0, self._do_set_listening, listening)

    def _do_set_listening(self, listening: bool):
        self.is_listening = listening
        self._update_status("listening" if listening else "idle")
        if not listening:
            self.canvas.itemconfig(self.button, fill=self.button_color)
        else:
            self.canvas.itemconfig(self.button, fill=self.button_active)

    def set_processing_state(self, processing: bool):
        """Set processing state (threadsafe)."""
        self.root.after(0, self._do_set_processing, processing)

    def _do_set_processing(self, processing: bool):
        self._update_status("processing" if processing else "idle")

    def set_speaking_state(self, speaking: bool):
        """Set speaking state (threadsafe)."""
        self.root.after(0, self._do_set_speaking, speaking)

    def _do_set_speaking(self, speaking: bool):
        self._update_status("speaking" if speaking else "idle")

    def set_error_state(self, error: bool):
        """Set error state (threadsafe)."""
        self.root.after(0, self._do_set_error, error)

    def _do_set_error(self, error: bool):
        self._update_status("error" if error else "idle")

    def start_pulse(self):
        """Start pulsing animation."""
        self.pulsing = True
        self._pulse()

    def stop_pulse(self):
        """Stop pulsing animation."""
        self.pulsing = False

    def _pulse(self):
        """Pulse animation."""
        if not self.pulsing:
            return
        current = self.canvas.itemcget(self.button, "fill")
        new_color = self.pulse_color if current == self.button_color else self.button_color
        self.canvas.itemconfig(self.button, fill=new_color)
        self.root.after(500, self._pulse)

    def _toggle_fullscreen(self, event=None):
        """Toggle fullscreen mode."""
        is_fullscreen = self.root.attributes("-fullscreen")
        self.root.attributes("-fullscreen", not is_fullscreen)

    def _exit_fullscreen(self, event=None):
        """Exit fullscreen mode."""
        self.root.attributes("-fullscreen", False)

    def run(self):
        """Run the main loop."""
        self.root.mainloop()

    def close(self):
        """Close the window."""
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def after(self, ms: int, callback: Callable):
        """Schedule a callback on the main thread (threadsafe)."""
        self.root.after(ms, callback)
