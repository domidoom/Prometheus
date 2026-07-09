#!/usr/bin/env python3
"""Jarvis — Dockbox thin-client entry point.

Architecture:
- Main thread: pywebview UI (Jarvis HTML window) + tkinter companion windows.
- Background thread: single asyncio event loop.
- DockboxBridge owns the HTTP/SSE connection to the server.
- Local Whisper transcribes voice; transcribed text is sent to /api/messages.
- Agent reply chunks arrive via SSE and are spoken by Kokoro TTS.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import signal
import sys
import threading
from typing import Optional

from dotenv import load_dotenv

from core.assistant import DockboxBridge
from core.config import Config
from core.session_store import SessionStore
from ui.jarvis_window import JarvisWindow
from voice.audio import AudioPlayer, AudioRecorder, BeepGenerator, find_device
from voice.capture import capture
from voice.clap import ClapDetector
from voice.stt import STT
from voice.tts import TTS

load_dotenv()


class JarvisApp:
    def __init__(self):
        self.config = Config()
        self.bridge = DockboxBridge(config=self.config)

        voice_cfg = self.config.voice
        audio_cfg = self.config.audio
        # Resolve audio devices. pyaudio's "default" device can't capture/play
        # Bluetooth (HFP) on PipeWire — the "pipewire"/"pulse" devices can. Honor
        # an explicit config index, else auto-detect by backend name.
        in_dev = audio_cfg.get("input_device")
        if in_dev is None:
            in_dev = find_device("input")
        out_dev = audio_cfg.get("playback_device")
        if out_dev is None:
            out_dev = find_device("output")
        self._input_device = in_dev
        print(f"[jarvis] audio devices — input={in_dev} output={out_dev}")
        self.audio_recorder = AudioRecorder(
            sample_rate=voice_cfg.get("sample_rate", 16000),
            channels=voice_cfg.get("channels", 1),
            silence_timeout=voice_cfg.get("silence_timeout", 1.0),
            max_duration=voice_cfg.get("max_recording_seconds", 60.0),
            input_device_index=in_dev,
        )
        self.audio_player = AudioPlayer(output_device=out_dev)
        self.beep_generator = BeepGenerator(
            sample_rate=voice_cfg.get("sample_rate", 16000)
        )
        self.stt = STT(model=voice_cfg.get("whisper_model", "base"))
        self.tts = TTS(voice="am_michael", speed=1.0)

        self.window: Optional[JarvisWindow] = None
        self.clap_detector: Optional[ClapDetector] = None

        self.running = False
        self._conversation_active = False
        self._stop_requested = False
        self._is_speaking = False
        self._record_task: Optional[asyncio.Task] = None
        self._send_task: Optional[asyncio.Task] = None

        # TTS pipeline: SSE chunks accumulate here; a worker speaks them in order.
        self._tts_queue: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
        self._tts_worker: Optional[asyncio.Task] = None
        # Signalled when the server's SSE turn_end has been consumed and the final
        # TTS buffer has finished speaking. _single_turn awaits this before looping.
        self._turn_done: asyncio.Event = asyncio.Event()
        self._turn_done.set()

        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    # ----- audio helpers (run blocking I/O in executor) -----

    def _play_beep(self, beep_func):
        try:
            self.audio_player.play_bytes(beep_func())
        except Exception:
            pass

    async def _play_beep_async(self, beep_func):
        await asyncio.get_running_loop().run_in_executor(None, self._play_beep, beep_func)

    async def _play_audio_async(self, audio_bytes: bytes):
        if not audio_bytes:
            return
        await asyncio.get_running_loop().run_in_executor(
            None,
            functools.partial(
                self.audio_player.play_bytes, audio_bytes, on_level=self._on_audio_level
            ),
        )

    def _on_audio_level(self, level: float) -> None:
        if self.window is not None:
            try:
                self.window.set_audio_level(level)
            except Exception:
                pass

    async def _record_audio_async(self) -> bytes:
        return await asyncio.get_running_loop().run_in_executor(
            None, self.audio_recorder.record_until_silence
        )

    async def _transcribe_async(self, audio_data: bytes) -> str:
        return await asyncio.get_running_loop().run_in_executor(
            None, self.stt.transcribe, audio_data
        )

    async def _synthesize_async(self, text: str) -> bytes:
        return await asyncio.get_running_loop().run_in_executor(
            None, self.tts.synthesize, text
        )

    @staticmethod
    def _is_farewell(text: str) -> bool:
        lowered = text.lower().strip().rstrip(".!?,")
        words = set(lowered.split())
        if any(w in words for w in ("bye", "goodbye", "cya")):
            return True
        phrases = (
            "see you", "talk to you later", "that's all",
            "go to sleep", "night night", "have a good one",
            "i'm done", "we're done", "all done", "shut down",
        )
        for phrase in phrases:
            if phrase in lowered:
                return True
        if lowered in {"stop", "exit", "done", "later"}:
            return True
        return False

    # ----- vision intent -----
    # Screen-capture phrases (the monitor) vs camera phrases (the room/webcam).
    _SCREEN_PHRASES = (
        "what's on my screen", "what is on my screen", "what's on the screen",
        "what's on screen", "look at my screen", "look at the screen",
        "describe my screen", "take a screenshot",
    )
    _CAMERA_PHRASES = (
        "what do you see", "what can you see", "describe the room",
        "describe what you see", "look at this", "what am i looking at",
        "can you see",
    )

    @classmethod
    def _vision_source(cls, text: str) -> Optional[str]:
        """'screen', 'webcam', or None. An explicit 'screen' mention wins;
        otherwise general "what do you see" routes to the webcam."""
        lowered = text.lower()
        if any(p in lowered for p in cls._SCREEN_PHRASES):
            return "screen"
        if any(p in lowered for p in cls._CAMERA_PHRASES):
            return "webcam"
        return None

    # ----- conversation turn -----

    async def _single_turn(self) -> bool:
        try:
            self._update_ui_state("listening")
            await self._play_beep_async(self.beep_generator.start_beep)

            audio_data = await self._record_audio_async()
            if self._stop_requested or not audio_data:
                return False

            await self._play_beep_async(self.beep_generator.stop_beep)
            self._update_ui_state("processing")

            text = await self._transcribe_async(audio_data)
            if not text:
                return False
            print(f"User said: {text}")

            end_after_turn = self._is_farewell(text)

            self._turn_done.clear()
            vision_source = self._vision_source(text)
            if vision_source is not None:
                self._update_ui_state("processing")
                image = await asyncio.get_running_loop().run_in_executor(
                    None, capture, vision_source
                )
                if image:
                    print(f"[jarvis] vision request ({vision_source}) — sending image")
                    self._send_task = asyncio.create_task(
                        self.bridge.send_image(
                            image, caption=text,
                            ext="jpg" if vision_source == "webcam" else "png",
                        )
                    )
                else:
                    print(f"[jarvis] {vision_source} capture failed; sending text only")
                    self._send_task = asyncio.create_task(self.bridge.send_text(text))
            else:
                self._send_task = asyncio.create_task(self.bridge.send_text(text))
            try:
                await self._send_task
            except asyncio.CancelledError:
                self._turn_done.set()
                self._update_ui_state("idle")
                return not self._stop_requested
            finally:
                self._send_task = None

            # SSE will push the reply; the TTS worker will speak it.
            self._update_ui_state("speaking")
            try:
                await self._turn_done.wait()
            except asyncio.CancelledError:
                self._turn_done.set()
                return not self._stop_requested
            if end_after_turn:
                return False
            return not self._stop_requested
        except Exception as e:
            print(f"Turn error: {e}")
            await self._play_beep_async(self.beep_generator.error_beep)
            self._update_ui_state("error")
            self.window.after(2000, lambda: self._update_ui_state("idle"))
            return False

    async def _handle_interaction(self):
        if self._conversation_active:
            if self._is_speaking:
                self.audio_player.cancel()
                return
            if self._send_task is not None and not self._send_task.done():
                self._send_task.cancel()
                await self.bridge.stop()
                return
            self._stop_requested = True
            self.audio_recorder.cancel()
            return

        self._conversation_active = True
        self._stop_requested = False
        self._pause_clap()
        try:
            while not self._stop_requested:
                keep_going = await self._single_turn()
                if not keep_going:
                    break
        finally:
            self._conversation_active = False
            self._stop_requested = False
            self._update_ui_state("idle")
            self._resume_clap()

    # ----- TTS pipeline (consumes SSE chunks) -----

    def _enqueue_chunk(self, chunk: str) -> None:
        if not self.loop:
            return
        self.loop.call_soon_threadsafe(self._tts_queue.put_nowait, chunk)

    def _on_turn_end(self) -> None:
        if not self.loop:
            return
        self.loop.call_soon_threadsafe(self._tts_queue.put_nowait, None)

    async def _tts_loop(self) -> None:
        """Drains chunks from _tts_queue, batches into sentences, speaks them."""
        buffer: list[str] = []
        while self.running:
            try:
                chunk = await self._tts_queue.get()
            except asyncio.CancelledError:
                return
            if chunk is None:
                # Turn end: flush whatever's left
                if buffer:
                    text = "".join(buffer).strip()
                    buffer.clear()
                    if text:
                        await self._speak(text)
                self._update_ui_state("idle")
                self._turn_done.set()
                continue

            buffer.append(chunk)
            joined = "".join(buffer)
            # Speak when we have at least one sentence boundary
            for sep in (".", "!", "?", "\n"):
                if sep in joined:
                    head, _, tail = joined.rpartition(sep)
                    head = head + sep
                    buffer.clear()
                    if tail.strip():
                        buffer.append(tail)
                    if head.strip():
                        await self._speak(head)
                    break

    async def _speak(self, text: str) -> None:
        try:
            audio = await self._synthesize_async(text)
            if not audio:
                return
            self._is_speaking = True
            try:
                await self._play_audio_async(audio)
            finally:
                self._is_speaking = False
                self._on_audio_level(0.0)
        except Exception as e:
            print(f"TTS error: {e}")

    # ----- UI -----

    def _update_ui_state(self, state: str):
        if self.window is None:
            return
        try:
            if state == "listening":
                self.window.set_listening_state(True)
            elif state == "processing":
                self.window.set_processing_state(True)
            elif state == "speaking":
                self.window.set_speaking_state(True)
            elif state == "error":
                self.window.set_error_state(True)
            else:
                self.window.set_listening_state(False)
                self.window.set_processing_state(False)
                self.window.set_speaking_state(False)
                self.window.set_error_state(False)
        except Exception:
            pass

    def _on_button_press(self):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._handle_interaction(), self.loop)

    # ----- clap wake -----

    GREETING = "Hello. I'm ready to get to work."

    def _pause_clap(self) -> None:
        if self.clap_detector is not None:
            self.clap_detector.pause()

    def _resume_clap(self) -> None:
        if self.clap_detector is not None:
            self.clap_detector.resume()

    def _on_clap(self) -> None:
        """Clap-detector callback (fires on the detector's background thread)."""
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._clap_wake(), self.loop)

    async def _clap_wake(self) -> None:
        if self._conversation_active:
            return
        self._pause_clap()
        try:
            audio = await self._synthesize_async(self.GREETING)
            if audio:
                await self._play_audio_async(audio)
        except Exception:
            pass
        await self._handle_interaction()

    # ----- async loop -----

    def _start_async_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.set_default_executor(self._executor)
        self.running = True
        while self.running:
            try:
                self.loop.run_until_complete(asyncio.sleep(0.1))
            except Exception:
                break

    def _signal_handler(self, signum, frame):
        print("\nShutting down…")
        self.running = False
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.window:
            self.window.close()
        sys.exit(0)

    def _setup_global_hotkey(self):
        try:
            from pynput import keyboard
        except ImportError:
            return
        def on_press(key):
            try:
                if key == keyboard.Key.f9:
                    self._on_button_press()
            except Exception:
                pass
        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()

    def _setup_clap_detector(self):
        voice_cfg = self.config.voice
        if not voice_cfg.get("clap_enabled", True):
            return
        self.clap_detector = ClapDetector(
            on_double_clap=self._on_clap,
            # Don't wake mid-conversation or while Jarvis is speaking.
            can_fire=lambda: not (self._conversation_active or self._is_speaking),
            sample_rate=voice_cfg.get("sample_rate", 16000),
            threshold=int(voice_cfg.get("clap_threshold", 9000)),
            input_device=self._input_device,
        )
        self.clap_detector.start()
        print("[jarvis] clap detection on — double-clap to wake")

    # ----- bootstrap -----

    def _ensure_configured(self) -> bool:
        store = SessionStore()
        if not (store.session and store.user_id):
            print("[jarvis] no saved Dockbox session. Run `python setup.py` first.")
            return False
        if not self.bridge.active_jid:
            print("[jarvis] no default group set. Run `python setup.py` to pick one.")
            return False
        return True

    async def _bootstrap_async(self):
        self.bridge.on_chunk(self._enqueue_chunk)
        self.bridge.on_turn_end(self._on_turn_end)
        self.bridge.start_stream()
        self._tts_worker = asyncio.create_task(self._tts_loop())
        try:
            audio = await self._synthesize_async("Assistant online.")
            if audio:
                await self._play_audio_async(audio)
        except Exception:
            pass

    def run(self):
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        if not self._ensure_configured():
            return

        self._loop_thread = threading.Thread(target=self._start_async_loop, daemon=True)
        self._loop_thread.start()
        while self.loop is None:
            threading.Event().wait(0.01)

        self._setup_global_hotkey()
        self._setup_clap_detector()

        ui_cfg = self.config.ui
        self.window = JarvisWindow(
            on_button_press=self._on_button_press,
            width=ui_cfg.get("window_width", 480),
            height=ui_cfg.get("window_height", 480),
        )

        asyncio.run_coroutine_threadsafe(self._bootstrap_async(), self.loop)

        self.window.run()
        self._shutdown()

    def _shutdown(self):
        print("Shutting down…")
        self.running = False
        if self.clap_detector is not None:
            self.clap_detector.stop()
        try:
            self.audio_recorder.cancel()
        except Exception:
            pass
        try:
            self.audio_player.cancel()
        except Exception:
            pass
        if self.loop:
            future = asyncio.run_coroutine_threadsafe(self.bridge.aclose(), self.loop)
            try:
                future.result(timeout=3)
            except Exception:
                pass
            self.loop.call_soon_threadsafe(self.loop.stop)


def main():
    JarvisApp().run()


if __name__ == "__main__":
    main()
