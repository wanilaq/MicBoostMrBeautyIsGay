"""
MicBoost DSP Engine
--------------------
پردازش بلادرنگ سیگنال میکروفون شامل:
 - Boost (گین شدید همراه با Soft Limiter برای جلوگیری از خش/کلیپ)
 - Noise Gate (حذف نویز زیر یک آستانه مشخص در سکوت)
 - AI-ish Noise Reduction (Spectral Gating غیر ایستا - noisereduce) روی بافر رولینگ
 - Parametric multi-band Equalizer (شبیه اکولایزر گرافیکی ولی با گین بالاتر)
"""
from __future__ import annotations

import numpy as np
from scipy.signal import iirpeak, iirfilter, lfilter, sosfilt, tf2sos

try:
    import noisereduce as nr
    HAS_NR = True
except Exception:
    HAS_NR = False


class BiquadEQBand:
    """یک باند اکولایزر پارامتریک (peaking filter) با گین قابل تنظیم تا ۲۴ دسی‌بل."""

    def __init__(self, freq: float, gain_db: float, q: float, sample_rate: int):
        self.freq = freq
        self.gain_db = gain_db
        self.q = q
        self.sample_rate = sample_rate
        self._zi = np.zeros(2)
        self._design()

    def _design(self):
        sr = self.sample_rate
        A = 10 ** (self.gain_db / 40.0)
        w0 = 2 * np.pi * self.freq / sr
        alpha = np.sin(w0) / (2 * self.q)
        cos_w0 = np.cos(w0)

        b0 = 1 + alpha * A
        b1 = -2 * cos_w0
        b2 = 1 - alpha * A
        a0 = 1 + alpha / A
        a1 = -2 * cos_w0
        a2 = 1 - alpha / A

        self.b = np.array([b0, b1, b2]) / a0
        self.a = np.array([1.0, a1 / a0, a2 / a0])

    def set_gain(self, gain_db: float):
        self.gain_db = gain_db
        self._design()

    def process(self, x: np.ndarray) -> np.ndarray:
        y, self._zi = lfilter(self.b, self.a, x, zi=self._zi * x[0] if False else self._zi)
        return y

    def reset_state(self):
        self._zi = np.zeros(2)


class NoiseGate:
    """گیت نویز ساده مبتنی بر آستانه انرژی (RMS) با attack/release نرم."""

    def __init__(self, sample_rate: int, threshold_db: float = -45.0,
                 attack_ms: float = 5.0, release_ms: float = 120.0):
        self.sample_rate = sample_rate
        self.threshold_db = threshold_db
        self.attack_coeff = np.exp(-1.0 / (sample_rate * attack_ms / 1000.0))
        self.release_coeff = np.exp(-1.0 / (sample_rate * release_ms / 1000.0))
        self._env = 0.0

    def set_threshold(self, db: float):
        self.threshold_db = db

    def process(self, x: np.ndarray) -> np.ndarray:
        out = np.empty_like(x)
        thresh_lin = 10 ** (self.threshold_db / 20.0)
        env = self._env
        for i, s in enumerate(x):
            rectified = abs(s)
            coeff = self.attack_coeff if rectified > env else self.release_coeff
            env = coeff * env + (1 - coeff) * rectified
            gain = 1.0 if env > thresh_lin else max(0.0, env / (thresh_lin + 1e-9))
            out[i] = s * gain
        self._env = env
        return out


def soft_limiter(x: np.ndarray, ceiling: float = 0.98) -> np.ndarray:
    """لیمیتر نرم (tanh-based) که اجازه بوست شدید می‌دهد بدون کلیپ سخت/خش گوش‌خراش."""
    return np.tanh(x / ceiling) * ceiling


class MicBoostEngine:
    """موتور کامل پردازش: گین -> نویزگیت -> حذف نویز هوشمند -> اکولایزر -> لیمیتر خروجی."""

    DEFAULT_BANDS = [60, 150, 400, 1000, 2500, 6000, 12000]

    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self.boost_db = 0.0          # 0 تا 30 دسی‌بل
        self.gate = NoiseGate(sample_rate)
        self.eq_bands = [BiquadEQBand(f, 0.0, 1.4, sample_rate) for f in self.DEFAULT_BANDS]
        self.ai_denoise_enabled = True
        self.ai_denoise_strength = 0.7  # 0..1
        self._noise_profile = None
        self._noise_capture_frames = []
        self._capturing_noise = False

    # ---- تنظیمات از UI ----
    def set_boost_db(self, db: float):
        self.boost_db = max(0.0, min(30.0, db))

    def set_gate_threshold(self, db: float):
        self.gate.set_threshold(db)

    def set_band_gain(self, index: int, db: float):
        if 0 <= index < len(self.eq_bands):
            self.eq_bands[index].set_gain(max(-24.0, min(24.0, db)))

    def set_ai_denoise(self, enabled: bool, strength: float = None):
        self.ai_denoise_enabled = enabled
        if strength is not None:
            self.ai_denoise_strength = max(0.0, min(1.0, strength))

    def start_noise_capture(self):
        self._capturing_noise = True
        self._noise_capture_frames = []

    def stop_noise_capture(self):
        self._capturing_noise = False
        if self._noise_capture_frames:
            self._noise_profile = np.concatenate(self._noise_capture_frames)

    # ---- پردازش هر بلاک صوتی ----
    def process_block(self, block: np.ndarray) -> np.ndarray:
        x = block.astype(np.float32).copy()

        if self._capturing_noise:
            self._noise_capture_frames.append(x.copy())

        # 1) بوست گین (دسی‌بل به ضریب خطی)
        gain_lin = 10 ** (self.boost_db / 20.0)
        x = x * gain_lin

        # 2) حذف نویز هوشمند (Spectral Gating) - قبل از گیت تا کیفیت بهتر باشد
        if self.ai_denoise_enabled and HAS_NR and len(x) > 256:
            try:
                x = nr.reduce_noise(
                    y=x,
                    sr=self.sample_rate,
                    y_noise=self._noise_profile,
                    stationary=False,
                    prop_decrease=self.ai_denoise_strength,
                )
                x = x.astype(np.float32)
            except Exception:
                pass

        # 3) نویز گیت (سکوت‌های باقی‌مانده را کاملاً می‌بندد)
        x = self.gate.process(x)

        # 4) اکولایزر چندبانده
        for band in self.eq_bands:
            x = band.process(x)

        # 5) لیمیتر خروجی برای جلوگیری از کلیپ حتی با بوست بالا
        x = soft_limiter(x, ceiling=0.98)

        return x.astype(np.float32)
