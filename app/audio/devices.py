"""لیست و مدیریت دیوایس‌های صوتی (میکروفون‌های ورودی و خروجی‌های مجازی/واقعی)."""
from __future__ import annotations
import sounddevice as sd


def list_input_devices():
    devices = sd.query_devices()
    result = []
    for idx, d in enumerate(devices):
        if d.get("max_input_channels", 0) > 0:
            result.append({"index": idx, "name": d["name"], "channels": d["max_input_channels"]})
    return result


def list_output_devices():
    devices = sd.query_devices()
    result = []
    for idx, d in enumerate(devices):
        if d.get("max_output_channels", 0) > 0:
            result.append({"index": idx, "name": d["name"], "channels": d["max_output_channels"]})
    return result


def find_virtual_cable_index():
    """تلاش برای پیدا کردن خودکار دیوایس VB-Cable (CABLE Input) در ویندوز."""
    for d in list_output_devices():
        name = d["name"].lower()
        if "cable input" in name or "vb-audio" in name or "vb-cable" in name:
            return d["index"]
    return None
