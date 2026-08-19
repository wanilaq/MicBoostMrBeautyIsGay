"""مدیریت استریم بلادرنگ ورودی/خروجی صدا با sounddevice و اتصال به MicBoostEngine."""
from __future__ import annotations

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, Signal

from .dsp import MicBoostEngine


class AudioStreamManager(QObject):
    level_updated = Signal(float, float)  # input_rms, output_rms
    error_occurred = Signal(str)

    def __init__(self, sample_rate: int = 48000, block_size: int = 512):
        super().__init__()
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.dsp = MicBoostEngine(sample_rate=sample_rate)
        self.input_device = None
        self.output_devices = []  # لیست خروجی‌ها (می‌تواند چند مقصد باشد: هم مجازی هم تست اسپیکر)
        self._streams = []
        self.running = False

    def set_input_device(self, index: int):
        self.input_device = index

    def set_output_devices(self, indices: list[int]):
        self.output_devices = indices

    def _callback(self, indata, frames, time_info, status):
        if status:
            pass  # underrun/overrun نادیده گرفته می‌شود، فقط لاگ نرم
        mono_in = indata[:, 0] if indata.ndim > 1 else indata
        processed = self.dsp.process_block(mono_in)

        in_rms = float(np.sqrt(np.mean(mono_in ** 2) + 1e-12))
        out_rms = float(np.sqrt(np.mean(processed ** 2) + 1e-12))
        self.level_updated.emit(in_rms, out_rms)

        self._last_block = processed

    def start(self):
        if self.running:
            return
        if self.input_device is None:
            self.error_occurred.emit("میکروفون ورودی انتخاب نشده است.")
            return
        if not self.output_devices:
            self.error_occurred.emit("هیچ خروجی‌ای انتخاب نشده است.")
            return

        try:
            self._last_block = np.zeros(self.block_size, dtype=np.float32)

            in_stream = sd.InputStream(
                device=self.input_device,
                channels=1,
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                dtype="float32",
                callback=self._callback,
            )

            out_streams = []
            for out_dev in self.output_devices:
                def make_out_callback():
                    def out_cb(outdata, frames, time_info, status):
                        block = self._last_block
                        if len(block) != frames:
                            block = np.resize(block, frames)
                        outdata[:, 0] = block
                    return out_cb

                out_stream = sd.OutputStream(
                    device=out_dev,
                    channels=1,
                    samplerate=self.sample_rate,
                    blocksize=self.block_size,
                    dtype="float32",
                    callback=make_out_callback(),
                )
                out_streams.append(out_stream)

            in_stream.start()
            for s in out_streams:
                s.start()

            self._streams = [in_stream] + out_streams
            self.running = True
        except Exception as e:
            self.error_occurred.emit(f"خطا در راه‌اندازی صدا: {e}")
            self.stop()

    def stop(self):
        for s in self._streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self._streams = []
        self.running = False
