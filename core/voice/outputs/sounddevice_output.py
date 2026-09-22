"""SoundDevice audio output implementation — streaming PCM playback."""

from __future__ import annotations

import threading
import queue
import time
import warnings
from typing import Iterator, Optional
from core.voice.outputs.base import AudioOutput, OutputStatus
from core.voice.outputs.pygame_output import PygameOutput


class SoundDeviceOutput:
    """
    SoundDevice-based audio output with streaming PCM support.
    
    Uses sounddevice/PortAudio for low-latency streaming playback.
    Falls back to PygameOutput for file-based playback.
    """

    def __init__(
        self,
        sample_rate: int = 24000,
        channels: int = 1,
        blocksize: int = 512,
        dtype: str = "float32",
        device: Optional[int] = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        self.dtype = dtype
        self.device = device
        
        self._stream: Optional["sounddevice.OutputStream"] = None
        self._lock = threading.Lock()
        self._status = OutputStatus.STOPPED
        self._stop_requested = threading.Event()
        self._chunk_queue: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=100)
        self._worker_thread: Optional[threading.Thread] = None
        self._stream_active = threading.Event()
        self._stream_finished = threading.Event()
        self._worker_done = threading.Event()
        self._worker_exception: Optional[BaseException] = None
        self._stream_owned = False  # Track if we own the stream
        
        # Fallback for file playback
        self._pygame_fallback = PygameOutput(
            frequency=sample_rate,
            size=-16,
            channels=2 if channels > 1 else 1,
            buffer=2048,
        )

    def _bytes_to_audio_data(self, chunk: bytes) -> "numpy.ndarray":
        """Convert bytes to numpy array based on dtype."""
        import numpy as np
        if self.dtype == "float32":
            # Assume int16 input, convert to float32
            audio_data = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
        elif self.dtype == "int16":
            audio_data = np.frombuffer(chunk, dtype=np.int16)
        else:
            raise ValueError(f"Unsupported dtype: {self.dtype}")
        
        # Reshape for channels
        if self.channels > 1:
            audio_data = audio_data.reshape(-1, self.channels)
        return audio_data
    
    def _validate_audio_data(self, audio_data: "numpy.ndarray") -> bool:
        """Validate audio data before playback."""
        import numpy as np
        if audio_data.size == 0:
            return False
        if np.any(np.isnan(audio_data)) or np.any(np.isinf(audio_data)):
            return False
        # Check for reasonable amplitude (prevent extreme values)
        if np.max(np.abs(audio_data)) > 10.0:
            return False
        return True

    def _worker(self) -> None:
        """Worker thread that manages the sounddevice stream."""
        import sounddevice as sd
        import numpy as np
        
        stop_requested = self._stop_requested
        chunk_queue = self._chunk_queue
        
        self._stream_finished.clear()
        self._worker_done.clear()
        self._worker_exception = None
        
        stream = None
        stream_ended = threading.Event()
        done_queue: "queue.Queue[str]" = queue.Queue()
        
        try:
            def callback(outdata, frames, time_info, status):
                if status:
                    # Only log non-underflow statuses to reduce noise
                    if status.output_underflow:
                        return  # Suppress underflow spam
                    print(f"[VOICE] Stream callback status: {status}")
                
                needed_samples = frames * self.channels
                if self.dtype == "float32":
                    needed_bytes = needed_samples * 4  # float32 = 4 bytes
                else:  # int16
                    needed_bytes = needed_samples * 2
                
                buffer = bytearray()
                while len(buffer) < needed_bytes:
                    try:
                        chunk = chunk_queue.get(timeout=0.1)
                    except queue.Empty:
                        if stop_requested.is_set():
                            done_queue.put("stop")
                            raise sd.CallbackStop()
                        # Send silence
                        outdata.fill(0)
                        return
                    
                    if chunk is None:  # End of stream marker
                        # Always signal done when we receive None
                        done_queue.put("done")
                        if len(buffer) == 0:
                            raise sd.CallbackStop()
                        # Pad with zeros if needed
                        buffer.extend(b'\x00' * (needed_bytes - len(buffer)))
                        break
                    
                    buffer.extend(chunk)
                
                # Convert and write
                try:
                    audio_data = self._bytes_to_audio_data(bytes(buffer[:needed_bytes]))
                except Exception:
                    outdata.fill(0)
                    return
                
                # Validate audio data
                if not self._validate_audio_data(audio_data):
                    outdata.fill(0)
                    return
                
                if audio_data.shape[0] < frames:
                    # Pad with zeros
                    pad_shape = (frames - audio_data.shape[0],) + audio_data.shape[1:]
                    audio_data = np.vstack([audio_data, np.zeros(pad_shape, dtype=audio_data.dtype)])
                elif audio_data.shape[0] > frames:
                    audio_data = audio_data[:frames]
                
                if self.channels == 1 and audio_data.ndim == 1:
                    audio_data = audio_data.reshape(-1, 1)
                
                outdata[:] = audio_data
            
            def _finished_callback():
                stream_ended.set()
            
            with self._lock:
                if self._stream is not None:
                    # Stream already exists - should not happen
                    self._status = OutputStatus.ERROR
                    return
                
                stream = sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype=self.dtype,
                    blocksize=self.blocksize,
                    device=self.device,
                    callback=callback,
                    finished_callback=_finished_callback,
                )
                self._stream = stream
                self._stream_owned = True
            
            stream.start()
            self._stream_active.set()
            
            # Wait for stream to finish or stop requested
            while stream.active and not stop_requested.is_set():
                try:
                    msg = done_queue.get(timeout=0.1)
                    if msg in ("done", "stop"):
                        break
                except queue.Empty:
                    continue
            
            # If we got done/stop signal, explicitly stop the stream
            if stream.active:
                try:
                    stream.stop()
                except Exception:
                    pass
            
            # Wait for finished callback
            stream_ended.wait(timeout=2.0)
            
            if stream.active:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
        except sd.PortAudioError as e:
            if "Invalid stream pointer" not in str(e):
                print(f"[VOICE] SoundDevice stream error: {e}")
            self._status = OutputStatus.ERROR
        except Exception as e:
            self._worker_exception = e
            print(f"[VOICE] SoundDevice stream error: {e}")
            self._status = OutputStatus.ERROR
        finally:
            self._stream_active.clear()
            self._stream_finished.set()
            self._worker_done.set()
            # Clear stream reference
            with self._lock:
                self._stream = None
                self._stream_owned = False

    def play_stream(
        self,
        audio_chunks: Iterator[bytes],
        sample_rate: int = 24000,
        channels: int = 1,
    ) -> OutputStatus:
        """Play streaming audio chunks via SoundDevice."""
        if sample_rate != self.sample_rate or channels != self.channels:
            print(f"[VOICE] Warning: stream format ({sample_rate}Hz, {channels}ch) differs from output config ({self.sample_rate}Hz, {self.channels}ch)")
        
        with self._lock:
            if self._status == OutputStatus.PLAYING:
                self.stop()
            
            self._stop_requested.clear()
            self._stream_finished.clear()
            self._worker_done.clear()
            self._worker_exception = None
            self._status = OutputStatus.PLAYING
            
            # Clear any leftover queue items
            while not self._chunk_queue.empty():
                try:
                    self._chunk_queue.get_nowait()
                except queue.Empty:
                    break
            
            # Start worker thread (non-daemon so we can join it)
            self._worker_thread = threading.Thread(target=self._worker, daemon=False, name="DOOM-AudioStream")
            self._worker_thread.start()
            
            # Wait for stream to become active
            if not self._stream_active.wait(timeout=2.0):
                self._status = OutputStatus.ERROR
                print("[VOICE] Stream failed to start")
                self._worker_done.wait(timeout=1.0)
                if self._worker_thread:
                    self._worker_thread.join(timeout=1.0)
                return OutputStatus.ERROR
            
            # Feed chunks to queue
            try:
                for chunk in audio_chunks:
                    if self._stop_requested.is_set():
                        break
                    if chunk:
                        self._chunk_queue.put(chunk, timeout=1.0)
            except queue.Full:
                print(f"[VOICE] Audio chunk queue full, dropping chunks")
            except Exception as e:
                print(f"[VOICE] Error feeding audio chunks: {e}")
                self._stop_requested.set()
                self._status = OutputStatus.ERROR
            
            # Signal end of stream
            try:
                self._chunk_queue.put(None, timeout=1.0)
            except queue.Full:
                pass
            
            # Wait for stream to finish
            self._stream_finished.wait(timeout=10.0)
            
            # Wait for worker thread to finish
            if self._worker_thread and self._worker_thread.is_alive():
                self._worker_done.wait(timeout=3.0)
                self._worker_thread.join(timeout=3.0)
            
            if self._worker_exception:
                self._status = OutputStatus.ERROR
            
            if self._status != OutputStatus.ERROR:
                self._status = OutputStatus.STOPPED
            
            return self._status

    def play(self, audio_path: str) -> OutputStatus:
        """Play an audio file using Pygame fallback."""
        return self._pygame_fallback.play(audio_path)

    def stop(self) -> None:
        """Stop current playback."""
        # Signal stop
        self._stop_requested.set()
        
        # Signal end of stream if queue is active
        try:
            self._chunk_queue.put_nowait(None)
        except queue.Full:
            pass
        
        # Stop pygame fallback
        try:
            self._pygame_fallback.stop()
        except Exception:
            pass
        
        # Wait for worker if playing
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_done.wait(timeout=2.0)
            self._worker_thread.join(timeout=2.0)
        
        # Ensure stream is closed
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
                self._stream_owned = False
        
        self._status = OutputStatus.STOPPED

    def get_status(self) -> OutputStatus:
        return self._status