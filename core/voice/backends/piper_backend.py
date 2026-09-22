"""Piper TTS backend — process-isolated fallback using Piper CLI.

This backend communicates with Piper through a subprocess boundary to avoid
GPL linkage concerns. The Piper CLI streams raw PCM to stdout which is
fed incrementally to SoundDeviceOutput.
"""

from __future__ import annotations

import os
import subprocess
import threading
import shutil
from typing import Optional, Iterator
from pathlib import Path

from core.voice.backends.base import TTSBackend, BackendStatus
from core.voice.outputs.base import AudioOutput, OutputStatus
from core.cost_guard import ResourceRequest, ResourceType, cost_guard


class PiperBackend:
    """
    Piper TTS backend using CLI subprocess isolation.
    
    Uses piper CLI (piper-tts package) to generate speech.
    Streams raw int16 PCM from stdout to SoundDeviceOutput.
    """
    
    name = "piper"
    
    def __init__(
        self,
        executable: Optional[str] = None,
        model_path: Optional[str] = None,
        config_path: Optional[str] = None,
        voice: Optional[str] = None,
        length_scale: Optional[float] = None,
        noise_scale: Optional[float] = None,
        noise_w_scale: Optional[float] = None,
        speaker_id: int = 0,
        sentence_silence: float = 0.25,
        volume: float = 1.0,
        audio_output: Optional[AudioOutput] = None,
    ):
        # Configuration from env or parameters
        # Check user Scripts directory on Windows for piper.exe
        default_executable = executable or os.getenv("DOOM_PIPER_EXECUTABLE")
        if not default_executable:
            default_executable = shutil.which("piper")
        if not default_executable and os.name == "nt":
            # Check user Scripts directory on Windows
            user_scripts = Path.home() / "AppData" / "Roaming" / "Python" / "Python311" / "Scripts" / "piper.exe"
            if user_scripts.exists():
                default_executable = str(user_scripts)
        self.executable = default_executable
        
        self.model_path = model_path or os.getenv("DOOM_PIPER_MODEL_PATH")
        self.config_path = config_path or os.getenv("DOOM_PIPER_CONFIG_PATH")
        self.voice = voice or os.getenv("DOOM_PIPER_VOICE")
        self.length_scale = float(length_scale) if length_scale is not None else float(os.getenv("DOOM_PIPER_LENGTH_SCALE", "1.0"))
        self.noise_scale = float(noise_scale) if noise_scale is not None else float(os.getenv("DOOM_PIPER_NOISE_SCALE", "0.667"))
        self.noise_w_scale = float(noise_w_scale) if noise_w_scale is not None else float(os.getenv("DOOM_PIPER_NOISE_W_SCALE", "0.8"))
        self.speaker_id = speaker_id
        self.sentence_silence = sentence_silence
        self.volume = volume
        self.audio_output = audio_output
        
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._init_error: Optional[str] = None
        self._sample_rate: Optional[int] = None
        self._config: Optional[dict] = None
        
    def _load_config(self) -> bool:
        """Load model config to get sample rate. Returns True on success."""
        if self._config is not None:
            return self._sample_rate is not None
            
        if not self.config_path:
            # Try to infer config path from model path
            if self.model_path:
                model_p = Path(self.model_path)
                inferred_config = model_p.with_suffix(model_p.suffix + ".json")
                if inferred_config.exists():
                    self.config_path = str(inferred_config)
        
        if not self.config_path or not Path(self.config_path).exists():
            self._init_error = f"Config file not found: {self.config_path}"
            return False
            
        try:
            import json
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)
            self._sample_rate = self._config.get("audio", {}).get("sample_rate")
            if not self._sample_rate:
                self._init_error = "Sample rate not found in config"
                return False
            return True
        except Exception as e:
            self._init_error = f"Failed to load config: {e}"
            return False
    
    def is_available(self) -> bool:
        """Return True if Piper CLI and model files exist."""
        if not self.executable or not shutil.which(self.executable) and not Path(self.executable).exists():
            self._init_error = "Piper executable not found"
            return False
            
        if not self.model_path or not Path(self.model_path).exists():
            self._init_error = f"Model file not found: {self.model_path}"
            return False
            
        if not self._load_config():
            return False
            
        return True
    
    def get_init_error(self) -> Optional[str]:
        """Return initialization error message if any."""
        if not self.is_available():
            if self._init_error:
                return self._init_error
            if not self.executable or not (shutil.which(self.executable) or Path(self.executable).exists()):
                return "Piper executable not found"
            if not self.model_path or not Path(self.model_path).exists():
                return f"Model file not found: {self.model_path}"
        return None
    
    def get_sample_rate(self) -> int:
        """Return the sample rate from model config."""
        if self._sample_rate is None:
            self._load_config()
        return self._sample_rate or 22050
    
    def is_allowed(self, lang: Optional[str] = None) -> bool:
        """Check Cost Guard authorization for piper."""
        decision = cost_guard.authorize(ResourceRequest(
            resource_type=ResourceType.TTS,
            provider="piper",
            capability="tts",
        ))
        return decision.is_allow
    
    def get_voice_for_language(self, lang: str) -> Optional[str]:
        """Return configured voice for language."""
        if self.voice:
            return self.voice
        # Could map language to voice here if multiple voices configured
        return None
    
    def _build_command(self) -> list[str]:
        """Build the Piper CLI command array. No shell=True."""
        cmd = [
            self.executable,
            "--model", self.model_path,
            "--output-raw",
        ]
        
        if self.config_path:
            cmd.extend(["--config", self.config_path])
            
        # Only add speaker if voice is explicitly set or speaker_id is provided (not just default)
        if self.voice:
            cmd.extend(["--speaker", str(self.voice)])
        elif self.speaker_id is not None and self.speaker_id != 0:
            cmd.extend(["--speaker", str(self.speaker_id)])
            
        if self.length_scale != 1.0:
            cmd.extend(["--length-scale", str(self.length_scale)])
            
        if self.noise_scale != 0.667:
            cmd.extend(["--noise-scale", str(self.noise_scale)])
            
        if self.noise_w_scale != 0.8:
            cmd.extend(["--noise-w-scale", str(self.noise_w_scale)])
            
        if self.sentence_silence != 0.25:
            cmd.extend(["--sentence-silence", str(self.sentence_silence)])
            
        if self.volume != 1.0:
            cmd.extend(["--volume", str(self.volume)])
            
        return cmd
    
    def _stream_stdout(self, process: subprocess.Popen) -> Iterator[bytes]:
        """Stream stdout from Piper process in chunks."""
        try:
            # Read in chunks matching typical block sizes
            chunk_size = 4096  # 4KB chunks
            while True:
                if self._stop_event.is_set():
                    break
                chunk = process.stdout.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            # Ensure process is cleaned up
            try:
                process.stdout.close()
            except Exception:
                pass
    
    def synthesize(self, text: str, lang: Optional[str] = None) -> BackendStatus:
        """Synthesize and play text using Piper CLI subprocess."""
        if not text:
            return BackendStatus.UNAVAILABLE
        
        if not self.is_available():
            print(f"[VOICE] Piper unavailable: {self.get_init_error()}")
            return BackendStatus.FAILED
        
        if not self.is_allowed(lang):
            return BackendStatus.BLOCKED
        
        self._stop_event.clear()
        
        cmd = self._build_command()
        sample_rate = self.get_sample_rate()
        
        try:
            # Start Piper subprocess
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # No shell=True - command is an array
            )
            
            # Write text to stdin
            try:
                self._process.stdin.write(text.encode("utf-8"))
                self._process.stdin.close()
            except BrokenPipeError:
                # Process may have exited early
                pass
            
            # Stream stdout to audio output
            if self.audio_output and hasattr(self.audio_output, 'play_stream'):
                chunks = self._stream_stdout(self._process)
                status = self.audio_output.play_stream(
                    chunks,
                    sample_rate=sample_rate,
                    channels=1,  # Piper outputs mono
                )
                # Wait for process to complete
                self._process.wait(timeout=30)
                
                if status == OutputStatus.ERROR:
                    return BackendStatus.FAILED
                return BackendStatus.AVAILABLE
            else:
                # No audio output - just wait for process
                self._process.wait(timeout=30)
                return BackendStatus.FAILED
                
        except subprocess.TimeoutExpired:
            print("[VOICE] Piper synthesis timed out")
            self._terminate_process()
            return BackendStatus.FAILED
        except FileNotFoundError:
            print(f"[VOICE] Piper executable not found: {self.executable}")
            self._init_error = f"Piper executable not found: {self.executable}"
            return BackendStatus.FAILED
        except Exception as e:
            print(f"[VOICE] Piper synthesis failed: {e}")
            self._terminate_process()
            return BackendStatus.FAILED
        finally:
            self._stop_event.set()
    
    def _terminate_process(self) -> None:
        """Safely terminate the Piper subprocess."""
        if self._process:
            try:
                # On Windows, terminate() sends CTRL_BREAK_EVENT
                self._process.terminate()
                # Wait briefly for graceful exit
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                    self._process.wait(timeout=1)
                except Exception:
                    pass
            except Exception:
                pass
            finally:
                self._process = None
    
    def stop(self) -> None:
        """Stop current synthesis/playback."""
        self._stop_event.set()
        self._terminate_process()
        if self.audio_output:
            self.audio_output.stop()


def create_piper_backend(
    executable: Optional[str] = None,
    model_path: Optional[str] = None,
    config_path: Optional[str] = None,
    voice: Optional[str] = None,
    length_scale: Optional[float] = None,
    noise_scale: Optional[float] = None,
    noise_w_scale: Optional[float] = None,
    audio_output: Optional[AudioOutput] = None,
) -> PiperBackend:
    """Factory function to create PiperBackend with optional config from env."""
    return PiperBackend(
        executable=executable,
        model_path=model_path,
        config_path=config_path,
        voice=voice,
        length_scale=length_scale,
        noise_scale=noise_scale,
        noise_w_scale=noise_w_scale,
        audio_output=audio_output,
    )