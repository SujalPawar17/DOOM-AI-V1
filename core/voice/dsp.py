"""Tactical Voice DSP Processing Chain — V9.3.8 validated algorithms.

Deterministic audio processing for DOOM Tactical voice profile.
All functions are pure, stateless, and safe for production use.
"""

from __future__ import annotations

import numpy as np

SCIPY_AVAILABLE = False
try:
    import scipy.signal
    import scipy.fft
    SCIPY_AVAILABLE = True
except ImportError:
    pass


SAMPLE_RATE = 24000


def _db_to_linear(db: float) -> float:
    """Convert dB to linear gain."""
    return 10.0 ** (db / 20.0)


def _time_stretch_sola(audio: np.ndarray, factor: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Time-stretch using SOLA (Synchronized Overlap-Add).
    factor > 1.0 = shorter/faster, factor < 1.0 = longer/slower.
    """
    if abs(factor - 1.0) < 0.01:
        return audio.copy()

    N = int(0.025 * sample_rate)  # 25 ms window
    if N % 2 != 0:
        N += 1
    Ha = N // 2
    Hs = int(round(Ha / factor))
    max_offset = N // 4

    window = np.hanning(N).astype(np.float32)

    out_len = int(len(audio) / factor)
    out = np.zeros(out_len + N + max_offset, dtype=np.float32)
    weight = np.zeros_like(out)

    frame = audio[:N] * window
    out[:N] += frame
    weight[:N] += window

    synth_pos = Hs
    ana_pos = Ha

    while ana_pos + N <= len(audio) and synth_pos + N + max_offset <= len(out):
        curr_frame = audio[ana_pos:ana_pos + N] * window
        ref = out[synth_pos:synth_pos + N]
        if np.max(np.abs(ref)) > 1e-6 and np.max(np.abs(curr_frame)) > 1e-6:
            corr = np.correlate(ref, curr_frame[:N // 2], mode="valid")
            best_offset = int(np.argmax(corr)) if len(corr) > 0 else 0
        else:
            best_offset = 0

        pos = synth_pos + best_offset
        if pos + N <= len(out):
            out[pos:pos + N] += curr_frame
            weight[pos:pos + N] += window

        ana_pos += Ha
        synth_pos += Hs

    mask = weight > 1e-4
    out[mask] /= weight[mask]
    return out[:out_len].astype(np.float32)


def apply_pitch_shift_polyphase(audio: np.ndarray, sample_rate: int, semitones: float) -> np.ndarray:
    """Apply pitch shift preserving speech rate/duration:
    1. Resample by ratio = 2^(semitones/12) using polyphase filter (shifts pitch + speed)
    2. Time-stretch by 1/ratio with SOLA to restore original duration and speech rate

    Positive semitones = higher pitch. Negative = lower pitch.
    Conservative range: -3.5 to +3.5 semitones.
    """
    if abs(semitones) < 0.05:
        return audio.copy()

    ratio = 2.0 ** (semitones / 12.0)

    if SCIPY_AVAILABLE:
        from fractions import Fraction
        frac = Fraction(ratio).limit_denominator(80)
        up, down = frac.numerator, frac.denominator
        up = max(1, min(up, 150))
        down = max(1, min(down, 150))
        resampled = scipy.signal.resample_poly(audio, up, down).astype(np.float32)

        # Restore original duration using SOLA time stretch
        target_factor = len(resampled) / len(audio)
        shifted = _time_stretch_sola(resampled, target_factor, sample_rate)

        if len(shifted) < len(audio):
            shifted = np.pad(shifted, (0, len(audio) - len(shifted)))
        else:
            shifted = shifted[:len(audio)]
        return shifted.astype(np.float32)
    else:
        new_length = int(len(audio) / ratio)
        x_old = np.linspace(0, 1, len(audio))
        x_new = np.linspace(0, 1, new_length)
        resampled = np.interp(x_new, x_old, audio).astype(np.float32)
        target_factor = len(resampled) / len(audio)
        shifted = _time_stretch_sola(resampled, target_factor, sample_rate)
        return shifted.astype(np.float32)


def apply_biquad_shelf(
    audio: np.ndarray,
    sample_rate: int,
    freq_hz: float,
    gain_db: float,
    shelf_type: str = "low",
) -> np.ndarray:
    """Apply a first-order shelving EQ filter.

    Uses scipy.signal.sosfilt for stable, accurate IIR filtering.
    shelf_type: "low" boosts/cuts frequencies below freq_hz,
                "high" boosts/cuts frequencies above freq_hz.
    gain_db: positive = boost, negative = cut.
    """
    if not SCIPY_AVAILABLE or abs(gain_db) < 0.01:
        return audio.copy()

    A = _db_to_linear(gain_db / 2.0)
    w0 = 2 * np.pi * freq_hz / sample_rate

    cos_w = np.cos(w0)
    sin_w = np.sin(w0)
    alpha = sin_w / 2.0 * np.sqrt((A + 1.0 / A) * (1.0 / 0.707 - 1.0) + 2.0)

    if shelf_type == "low":
        b0 = A * ((A + 1) - (A - 1) * cos_w + 2 * np.sqrt(A) * alpha)
        b1 = 2 * A * ((A - 1) - (A + 1) * cos_w)
        b2 = A * ((A + 1) - (A - 1) * cos_w - 2 * np.sqrt(A) * alpha)
        a0 = (A + 1) + (A - 1) * cos_w + 2 * np.sqrt(A) * alpha
        a1 = -2 * ((A - 1) + (A + 1) * cos_w)
        a2 = (A + 1) + (A - 1) * cos_w - 2 * np.sqrt(A) * alpha
    else:
        b0 = A * ((A + 1) + (A - 1) * cos_w + 2 * np.sqrt(A) * alpha)
        b1 = -2 * A * ((A - 1) + (A + 1) * cos_w)
        b2 = A * ((A + 1) + (A - 1) * cos_w - 2 * np.sqrt(A) * alpha)
        a0 = (A + 1) - (A - 1) * cos_w + 2 * np.sqrt(A) * alpha
        a1 = 2 * ((A - 1) - (A + 1) * cos_w)
        a2 = (A + 1) - (A - 1) * cos_w - 2 * np.sqrt(A) * alpha

    sos = np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]])
    return scipy.signal.sosfilt(sos, audio).astype(np.float32)


def apply_eq_low_mid(audio: np.ndarray, sample_rate: int, boost_db: float) -> np.ndarray:
    """Apply low-mid EQ boost/cut (200-500 Hz region).

    Uses cascaded shelving: low shelf at 350 Hz.
    """
    if abs(boost_db) < 0.01:
        return audio.copy()
    return apply_biquad_shelf(audio, sample_rate, 350.0, boost_db, "low")


def apply_hf_air(audio: np.ndarray, sample_rate: int, boost_db: float) -> np.ndarray:
    """Apply gentle high-frequency air boost/cut (6-8 kHz region).

    Uses high-shelf filter. Positive = presence/clarity, negative = warmth.
    """
    if abs(boost_db) < 0.01:
        return audio.copy()
    return apply_biquad_shelf(audio, sample_rate, 7000.0, boost_db, "high")


def apply_spectral_tilt(audio: np.ndarray, sample_rate: int, tilt_db: float) -> np.ndarray:
    """Apply spectral tilt — slopes the spectrum across the full frequency range.

    Positive tilt_db: brighter (more HF). Negative: darker (more LF presence).
    This is a simplified spectral tilt using a first-order IIR filter.
    It shapes the overall spectral balance differently from simple pitch shift,
    making this the closest available approximation to spectral character change.

    FORMANT_PROCESSING_AVAILABLE = 'spectral_tilt_via_scipy'
    """
    if not SCIPY_AVAILABLE or abs(tilt_db) < 0.01:
        return audio.copy()

    return apply_biquad_shelf(audio, sample_rate, 1000.0, tilt_db * 0.5, "high")


def apply_compression(
    audio: np.ndarray,
    ratio: float,
    threshold_db: float,
    attack_ms: float = 5.0,
    release_ms: float = 50.0,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Apply soft-knee dynamic range compression.

    Conservative implementation using smooth RMS envelope follower.
    ratio: compression ratio (e.g., 2.0 = 2:1)
    threshold_db: level above which compression kicks in
    """
    if ratio <= 1.0:
        return audio.copy()

    threshold_linear = _db_to_linear(threshold_db)

    attack_samples = max(1, int(attack_ms / 1000.0 * sample_rate))
    release_samples = max(1, int(release_ms / 1000.0 * sample_rate))
    alpha_attack = np.exp(-1.0 / attack_samples)
    alpha_release = np.exp(-1.0 / release_samples)

    envelope = np.abs(audio)
    env_smooth = np.zeros_like(envelope)
    env_smooth[0] = envelope[0]
    for i in range(1, len(envelope)):
        if envelope[i] > env_smooth[i - 1]:
            env_smooth[i] = alpha_attack * env_smooth[i - 1] + (1 - alpha_attack) * envelope[i]
        else:
            env_smooth[i] = alpha_release * env_smooth[i - 1] + (1 - alpha_release) * envelope[i]

    gain = np.ones_like(audio)
    above_mask = env_smooth > threshold_linear
    if np.any(above_mask):
        excess_db = 20.0 * np.log10(env_smooth[above_mask] / (threshold_linear + 1e-12))
        knee_db = 6.0
        knee_mask = excess_db < knee_db
        gain_db = np.zeros_like(excess_db)
        gain_db[knee_mask] = excess_db[knee_mask] * (1.0 / ratio - 1.0) * (excess_db[knee_mask] / (2.0 * knee_db))
        gain_db[~knee_mask] = (excess_db[~knee_mask] - knee_db / 2.0) * (1.0 / ratio - 1.0)
        gain[above_mask] = _db_to_linear(gain_db)

    return (audio * gain).astype(np.float32)


def apply_saturation(audio: np.ndarray, amount: float) -> np.ndarray:
    """Apply subtle tape-style saturation (tanh soft clipping).

    amount: 0 = no saturation, 0.15 = moderate character.
    Keeps voice dominant; adds very subtle harmonic warmth.
    """
    if amount <= 0.001:
        return audio.copy()
    drive = 1.0 + amount * 4.0
    return (np.tanh(audio * drive) / drive).astype(np.float32)


def apply_synthetic_layer(
    audio: np.ndarray,
    sample_rate: int,
    blend: float,
) -> np.ndarray:
    """Apply subtle synthetic character layer.

    The synthetic layer is created by:
    1. Half-wave rectifying the signal (creates harmonics)
    2. Bandpass filtering to a specific resonant frequency range (1.5-4 kHz)
    3. Blending at a very low level with the original

    This adds very subtle electronic texture without obvious robotics.
    blend: 0.0 = no effect, 0.08 = noticeable but subtle.
    """
    if blend <= 0.001 or not SCIPY_AVAILABLE:
        return audio.copy()

    synth = np.where(audio > 0, audio, 0.0).astype(np.float32)

    nyq = sample_rate / 2.0
    low_norm = 1500.0 / nyq
    high_norm = 4000.0 / nyq
    low_norm = np.clip(low_norm, 0.01, 0.99)
    high_norm = np.clip(high_norm, 0.01, 0.99)

    if low_norm < high_norm:
        try:
            sos = scipy.signal.butter(3, [low_norm, high_norm], btype="bandpass", output="sos")
            synth = scipy.signal.sosfilt(sos, synth).astype(np.float32)
        except Exception:
            return audio.copy()

    orig_peak = np.max(np.abs(audio)) + 1e-12
    synth_peak = np.max(np.abs(synth)) + 1e-12
    synth = synth * (orig_peak / synth_peak) * blend

    mixed = audio + synth
    return mixed.astype(np.float32)


def normalize_audio(audio: np.ndarray, target_peak: float = 0.92) -> np.ndarray:
    """Normalize audio to target peak, preventing clipping."""
    peak = np.max(np.abs(audio))
    if peak < 1e-9:
        return audio.copy()
    return (audio * (target_peak / peak)).astype(np.float32)


def sanitize_audio(audio: np.ndarray) -> np.ndarray:
    """Sanitize audio: replace NaN/Inf, clip to [-1, 1]."""
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(audio, -1.0, 1.0).astype(np.float32)


def float32_to_int16(audio: np.ndarray) -> np.ndarray:
    """Convert float32 [-1, 1] audio to int16."""
    audio = sanitize_audio(audio)
    return (audio * 32767.0).astype(np.int16)


def process_tactical_chain(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    pitch_semitones: float = -3.0,
    low_mid_boost_db: float = 2.2,
    hf_air_db: float = 0.0,
    spectral_tilt_db: float = -1.0,
    compression_ratio: float = 2.5,
    compression_threshold_db: float = -20.0,
    saturation_amount: float = 0.05,
    synthetic_pct: float = 0.0,
) -> np.ndarray:
    """Apply the complete Tactical voice character processing chain.

    Chain order (V9.3.8 validated):
    1. Pitch shift (polyphase resampling + SOLA)
    2. Low-mid EQ boost (200-500 Hz)
    3. HF air (6-8 kHz)
    4. Spectral tilt
    5. Compression
    6. Saturation
    7. Synthetic character layer
    8. Loudness normalization
    9. Sanitize (NaN/Inf/clipping safety)

    Returns processed float32 array, ready for int16 conversion at output boundary.
    """
    y = audio.astype(np.float32).copy()

    if abs(pitch_semitones) >= 0.05:
        y = apply_pitch_shift_polyphase(y, sample_rate, pitch_semitones)

    if abs(low_mid_boost_db) >= 0.01:
        y = apply_eq_low_mid(y, sample_rate, low_mid_boost_db)

    if abs(hf_air_db) >= 0.01:
        y = apply_hf_air(y, sample_rate, hf_air_db)

    if abs(spectral_tilt_db) >= 0.01:
        y = apply_spectral_tilt(y, sample_rate, spectral_tilt_db)

    if compression_ratio > 1.0:
        y = apply_compression(y, compression_ratio, compression_threshold_db, sample_rate=sample_rate)

    if saturation_amount > 0.001:
        y = apply_saturation(y, saturation_amount)

    if synthetic_pct > 0.001:
        y = apply_synthetic_layer(y, sample_rate, synthetic_pct)

    y = normalize_audio(y, target_peak=0.90)
    y = sanitize_audio(y)

    return y