import time
import math
import struct
import threading
from typing import Callable, Optional

# Try to import pyaudio for real-time acoustic peak detection
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False


class DOOMSoundDetector:
    """Acoustic sound & double-clap detector for waking DOOM like Iron Man.

    Auto-calibrates threshold from ambient noise on first run.
    Default threshold=75 (prevents background noise from false-triggering).
    Clap window: two sharp peaks within 0.20–1.00s triggers wake.
    """

    def __init__(
        self,
        threshold: int = 75,              # Tuned floor: high enough above ambient, sensitive to claps
        clap_window_min: float = 0.20,    # 200ms minimum to reject echoes/reverb of the same clap
        clap_window_max: float = 1.00,    # Natural two-clap rhythm window
        auto_calibrate: bool = True,
    ):
        self.threshold = threshold
        self.clap_window_min = clap_window_min
        self.clap_window_max = clap_window_max
        self.auto_calibrate = auto_calibrate
        self.last_clap_time = 0.0
        self.is_listening = False
        self.thread: Optional[threading.Thread] = None
        self.callback: Optional[Callable[[], None]] = None
        self._calibrated = False
        self.is_paused = False             # Set to True while DOOM is speaking/listening to avoid feedback loop


    # ─────────────────────────────────────────────
    # Utilities
    # ─────────────────────────────────────────────

    def _calculate_rms(self, audio_data: bytes) -> float:
        """Calculate Root Mean Square (loudness) of audio buffer."""
        count = len(audio_data) // 2
        if count == 0:
            return 0.0
        try:
            shorts = struct.unpack(f"{count}h", audio_data)
            rms = math.sqrt(sum(s * s for s in shorts) / count)
            return rms
        except Exception:
            return 0.0

    def _open_stream(self, p: "pyaudio.PyAudio", chunk: int = 1024, rate: int = 44100):
        """Open a mono 16-bit input stream. Returns stream or raises."""
        return p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=rate,
            input=True,
            frames_per_buffer=chunk,
        )

    def calibrate_threshold(self, ambient_duration: float = 1.5, clap_duration: float = 4.0) -> int:
        """
        NEW: Two-phase calibration:
          Phase 1 — measure ambient noise (1.5s, be quiet)
          Phase 2 — ask user to clap and capture the peak (4s)
        Threshold = midpoint between ambient and clap peak.
        Falls back to 3x ambient if no clap detected.
        """
        if not PYAUDIO_AVAILABLE:
            return self.threshold

        chunk = 1024
        p = None
        stream = None

        try:
            p = pyaudio.PyAudio()
            stream = self._open_stream(p, chunk)

            # Phase 1: Ambient noise
            print("[CLAP SENSOR] Phase 1: Measuring ambient noise (be quiet)...")
            ambient_samples = []
            end = time.time() + ambient_duration
            while time.time() < end:
                data = stream.read(chunk, exception_on_overflow=False)
                ambient_samples.append(self._calculate_rms(data))
                time.sleep(0.01)
            ambient = sum(ambient_samples) / len(ambient_samples) if ambient_samples else 10.0
            print(f"[CLAP SENSOR] Ambient RMS = {ambient:.1f}")

            # Phase 2: Clap peak
            print(f"[CLAP SENSOR] Phase 2: CLAP NOW (you have {clap_duration:.0f} seconds)...")
            clap_peak = 0.0
            end = time.time() + clap_duration
            while time.time() < end:
                data = stream.read(chunk, exception_on_overflow=False)
                rms = self._calculate_rms(data)
                if rms > clap_peak:
                    clap_peak = rms
                time.sleep(0.01)

            if clap_peak > ambient * 1.5:
                # Set threshold to midpoint between ambient and clap peak, minimum 65
                new_threshold = max(65, int((ambient + clap_peak) / 2))
                print(f"[CLAP SENSOR] Clap peak RMS = {clap_peak:.1f} -> threshold set to {new_threshold}")
            else:
                # Fallback to 3.5x ambient, minimum 65 to avoid room noise false positives
                new_threshold = max(65, min(500, int(ambient * 3.5)))
                print(f"[CLAP SENSOR] Ambient only ({ambient:.1f}), threshold set to {new_threshold}")

            self.threshold = new_threshold
            self._calibrated = True
            return self.threshold

        except Exception as e:
            print(f"[CLAP SENSOR] Calibration warning: {e}. Using default threshold={self.threshold}")
            return self.threshold

        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if p:
                try:
                    p.terminate()
                except Exception:
                    pass

    # ─────────────────────────────────────────────
    # Synchronous (one-shot) listener
    # ─────────────────────────────────────────────

    def listen_for_claps_sync(self, duration: float = 5.0) -> bool:
        """Synchronously check for double claps within a time duration."""
        if not PYAUDIO_AVAILABLE:
            print("[CLAP SENSOR] PyAudio not installed — clap detection disabled.")
            return False

        chunk = 1024
        p = None
        stream = None

        try:
            p = pyaudio.PyAudio()
            stream = self._open_stream(p, chunk)

            start_time = time.time()
            first_clap_time = 0.0

            while time.time() - start_time < duration:
                data = stream.read(chunk, exception_on_overflow=False)
                rms = self._calculate_rms(data)

                if rms > self.threshold:
                    current_time = time.time()
                    if first_clap_time == 0.0:
                        first_clap_time = current_time
                        time.sleep(0.06)  # debounce
                    else:
                        interval = current_time - first_clap_time
                        if self.clap_window_min <= interval <= self.clap_window_max:
                            print(f"\n[ACOUSTIC] ** Double clap detected! ** (gap={interval:.2f}s, rms={rms:.0f})")
                            return True
                        elif interval > self.clap_window_max:
                            # Too slow — reset, treat this peak as a new first clap
                            first_clap_time = current_time

                time.sleep(0.01)

        except Exception as e:
            print(f"[CLAP SENSOR] Stream error: {e}")
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if p:
                try:
                    p.terminate()
                except Exception:
                    pass

        return False

    # ─────────────────────────────────────────────
    # Background continuous detector (daemon thread)
    # ─────────────────────────────────────────────

    def start_background_detector(self, on_clap_detected: Callable[[], None]):
        """Run continuous clap detection in a background daemon thread.

        Auto-calibrates first, then loops forever until stop_detector() is called.
        If the audio stream dies (e.g. USB mic unplugged), it retries every 5s.
        """
        if not PYAUDIO_AVAILABLE:
            print("[CLAP SENSOR] PyAudio not installed — acoustic wake disabled.")
            return

        if self.is_listening:
            print("[CLAP SENSOR] Already running.")
            return

        self.callback = on_clap_detected
        self.is_listening = True

        # Auto-calibrate before starting the thread
        if self.auto_calibrate and not self._calibrated:
            self.calibrate_threshold()

        def _worker():
            print(f"[CLAP SENSOR] Background detector STARTED (threshold={self.threshold})")
            chunk = 1024
            rate = 44100

            while self.is_listening:
                p = None
                stream = None
                first_clap_time = 0.0

                try:
                    p = pyaudio.PyAudio()
                    stream = self._open_stream(p, chunk, rate)
                    print(f"[CLAP SENSOR] Listening for double claps... (threshold={self.threshold})")

                    while self.is_listening:
                        if self.is_paused:
                            first_clap_time = 0.0
                            time.sleep(0.05)
                            continue

                        data = stream.read(chunk, exception_on_overflow=False)
                        rms = self._calculate_rms(data)

                        if rms > self.threshold:
                            current_time = time.time()

                            if first_clap_time == 0.0:
                                # First peak detected
                                first_clap_time = current_time
                                time.sleep(0.08)  # 80ms debounce so the same clap doesn't count twice

                            else:
                                interval = current_time - first_clap_time

                                if self.clap_window_min <= interval <= self.clap_window_max:
                                    print(f"\n[ACOUSTIC] ** Double clap! ** gap={interval:.2f}s, rms={rms:.0f}")
                                    first_clap_time = 0.0
                                    # Trigger callback in a separate thread
                                    if self.callback:
                                        t = threading.Thread(target=self.callback, daemon=True)
                                        t.start()
                                    time.sleep(2.5)  # 2.5s cooldown to avoid speaker audio or echoes re-triggering

                                elif interval > self.clap_window_max:
                                    # Second clap came too late — treat as new first clap
                                    first_clap_time = current_time

                        time.sleep(0.008)  # tighter polling for better responsiveness


                except Exception as e:
                    # Don't silently swallow — log it so we know what broke
                    print(f"[CLAP SENSOR] Stream error: {e} — retrying in 5s...")
                finally:
                    if stream:
                        try:
                            stream.stop_stream()
                            stream.close()
                        except Exception:
                            pass
                    if p:
                        try:
                            p.terminate()
                        except Exception:
                            pass

                if self.is_listening:
                    # Auto-retry if stream died (e.g. device busy, USB disconnect)
                    time.sleep(5)

            print("[CLAP SENSOR] Background detector STOPPED.")

        self.thread = threading.Thread(target=_worker, daemon=True, name="DOOM-ClapSensor")
        self.thread.start()

    def stop_detector(self):
        """Stop the background detector loop."""
        self.is_listening = False
        print("[CLAP SENSOR] Stop signal sent.")

    def set_threshold(self, value: int):
        """Manually override the detection threshold at runtime."""
        self.threshold = value
        print(f"[CLAP SENSOR] Threshold manually set to {value}")

    def get_status(self) -> dict:
        """Returns diagnostic info."""
        return {
            "pyaudio_available": PYAUDIO_AVAILABLE,
            "is_listening": self.is_listening,
            "threshold": self.threshold,
            "calibrated": self._calibrated,
            "clap_window": f"{self.clap_window_min}s – {self.clap_window_max}s",
            "thread_alive": self.thread.is_alive() if self.thread else False,
        }


# Global instance — threshold=75 (tuned to reject ambient sounds and only catch real double claps)
sound_detector = DOOMSoundDetector(threshold=75, auto_calibrate=True)

