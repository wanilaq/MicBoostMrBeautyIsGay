from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSlider, QFrame, QCheckBox, QProgressBar, QFileDialog,
    QMessageBox, QGridLayout, QListWidget, QListWidgetItem
)

from app.audio.engine import AudioStreamManager
from app.audio.devices import list_input_devices, list_output_devices, find_virtual_cable_index

PRESET_DIR = os.path.join(os.path.expanduser("~"), ".micboost", "presets")


def card() -> QFrame:
    f = QFrame()
    f.setObjectName("Card")
    return f


class BandSlider(QWidget):
    def __init__(self, label: str, on_change):
        super().__init__()
        self.on_change = on_change
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignHCenter)

        self.value_lbl = QLabel("0dB")
        self.value_lbl.setProperty("class", "ValueLabel")
        self.value_lbl.setObjectName("ValueLabel")
        self.value_lbl.setAlignment(Qt.AlignHCenter)

        self.slider = QSlider(Qt.Vertical)
        self.slider.setMinimum(-24)
        self.slider.setMaximum(24)
        self.slider.setValue(0)
        self.slider.setFixedHeight(120)
        self.slider.valueChanged.connect(self._changed)

        name_lbl = QLabel(label)
        name_lbl.setAlignment(Qt.AlignHCenter)
        name_lbl.setObjectName("SectionLabel")

        layout.addWidget(self.value_lbl)
        layout.addWidget(self.slider, alignment=Qt.AlignHCenter)
        layout.addWidget(name_lbl)

    def _changed(self, v):
        self.value_lbl.setText(f"{v:+d}dB")
        self.on_change(v)

    def set_value(self, v):
        self.slider.setValue(int(v))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("MainWindow")
        self.setWindowTitle("MicBoost — Ventus")
        self.resize(920, 640)

        self.manager = AudioStreamManager()
        self.manager.level_updated.connect(self._on_levels)
        self.manager.error_occurred.connect(self._on_error)

        self._build_ui()
        self._load_devices()
        self._apply_style()

        self.meter_timer = QTimer(self)
        self.meter_timer.setInterval(60)
        self.meter_timer.timeout.connect(self._decay_meters)
        self.meter_timer.start()
        self._in_level = 0.0
        self._out_level = 0.0

        os.makedirs(PRESET_DIR, exist_ok=True)

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # هدر
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("MicBoost")
        title.setObjectName("Title")
        subtitle = QLabel("بوست حرفه‌ای صدای میکروفون با حذف نویز هوشمند")
        subtitle.setObjectName("Subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()

        self.power_btn = QPushButton("OFF")
        self.power_btn.setObjectName("PowerButton")
        self.power_btn.setCheckable(True)
        self.power_btn.setFixedSize(56, 56)
        self.power_btn.clicked.connect(self._toggle_power)
        header.addWidget(self.power_btn)
        root.addLayout(header)

        # کارت دیوایس‌ها
        dev_card = card()
        dev_layout = QGridLayout(dev_card)
        dev_layout.setContentsMargins(16, 16, 16, 16)
        dev_layout.setSpacing(10)

        in_lbl = QLabel("میکروفون ورودی")
        in_lbl.setObjectName("SectionLabel")
        self.input_combo = QComboBox()

        out_lbl = QLabel("خروجی‌ها (میکروفون مجازی برای تیم‌اسپیک/دیسکورد/گیم)")
        out_lbl.setObjectName("SectionLabel")
        self.output_list = QListWidget()
        self.output_list.setSelectionMode(QListWidget.MultiSelection)
        self.output_list.setFixedHeight(90)

        refresh_btn = QPushButton("بروزرسانی دیوایس‌ها")
        refresh_btn.clicked.connect(self._load_devices)

        dev_layout.addWidget(in_lbl, 0, 0)
        dev_layout.addWidget(self.input_combo, 1, 0)
        dev_layout.addWidget(out_lbl, 0, 1)
        dev_layout.addWidget(self.output_list, 1, 1)
        dev_layout.addWidget(refresh_btn, 2, 0, 1, 2)
        root.addWidget(dev_card)

        # کارت متر صدا
        meter_card = card()
        meter_layout = QVBoxLayout(meter_card)
        meter_layout.setContentsMargins(16, 16, 16, 16)
        in_meter_lbl = QLabel("سطح ورودی")
        in_meter_lbl.setObjectName("SectionLabel")
        self.in_meter = QProgressBar()
        self.in_meter.setRange(0, 100)
        self.in_meter.setTextVisible(False)
        out_meter_lbl = QLabel("سطح خروجی (بعد از بوست)")
        out_meter_lbl.setObjectName("SectionLabel")
        self.out_meter = QProgressBar()
        self.out_meter.setRange(0, 100)
        self.out_meter.setTextVisible(False)
        meter_layout.addWidget(in_meter_lbl)
        meter_layout.addWidget(self.in_meter)
        meter_layout.addWidget(out_meter_lbl)
        meter_layout.addWidget(self.out_meter)
        root.addWidget(meter_card)

        # کارت بوست + نویزگیت + AI denoise
        ctrl_card = card()
        ctrl_layout = QGridLayout(ctrl_card)
        ctrl_layout.setContentsMargins(16, 16, 16, 16)
        ctrl_layout.setSpacing(10)

        boost_lbl = QLabel("میزان بوست میکروفون")
        boost_lbl.setObjectName("SectionLabel")
        self.boost_value_lbl = QLabel("0 dB")
        self.boost_value_lbl.setObjectName("ValueLabel")
        self.boost_slider = QSlider(Qt.Horizontal)
        self.boost_slider.setMinimum(0)
        self.boost_slider.setMaximum(30)
        self.boost_slider.valueChanged.connect(self._on_boost_changed)

        gate_lbl = QLabel("آستانه نویزگیت (سکوت‌سنج)")
        gate_lbl.setObjectName("SectionLabel")
        self.gate_value_lbl = QLabel("-45 dB")
        self.gate_value_lbl.setObjectName("ValueLabel")
        self.gate_slider = QSlider(Qt.Horizontal)
        self.gate_slider.setMinimum(-80)
        self.gate_slider.setMaximum(-10)
        self.gate_slider.setValue(-45)
        self.gate_slider.valueChanged.connect(self._on_gate_changed)

        self.ai_checkbox = QCheckBox("حذف نویز هوشمند (AI Noise Reduction)")
        self.ai_checkbox.setChecked(True)
        self.ai_checkbox.stateChanged.connect(self._on_ai_toggle)

        ai_strength_lbl = QLabel("قدرت حذف نویز")
        ai_strength_lbl.setObjectName("SectionLabel")
        self.ai_strength_slider = QSlider(Qt.Horizontal)
        self.ai_strength_slider.setMinimum(0)
        self.ai_strength_slider.setMaximum(100)
        self.ai_strength_slider.setValue(70)
        self.ai_strength_slider.valueChanged.connect(self._on_ai_strength_changed)

        capture_btn = QPushButton("ضبط نمونه نویز محیط (۲ ثانیه سکوت نگه دارید)")
        capture_btn.clicked.connect(self._capture_noise)

        ctrl_layout.addWidget(boost_lbl, 0, 0)
        ctrl_layout.addWidget(self.boost_value_lbl, 0, 1)
        ctrl_layout.addWidget(self.boost_slider, 1, 0, 1, 2)

        ctrl_layout.addWidget(gate_lbl, 2, 0)
        ctrl_layout.addWidget(self.gate_value_lbl, 2, 1)
        ctrl_layout.addWidget(self.gate_slider, 3, 0, 1, 2)

        ctrl_layout.addWidget(self.ai_checkbox, 4, 0, 1, 2)
        ctrl_layout.addWidget(ai_strength_lbl, 5, 0)
        ctrl_layout.addWidget(self.ai_strength_slider, 6, 0, 1, 2)
        ctrl_layout.addWidget(capture_btn, 7, 0, 1, 2)

        root.addWidget(ctrl_card)

        # کارت اکولایزر
        eq_card = card()
        eq_outer = QVBoxLayout(eq_card)
        eq_outer.setContentsMargins(16, 16, 16, 16)
        eq_title = QLabel("اکولایزر قدرتمند (Voice EQ)")
        eq_title.setObjectName("SectionLabel")
        eq_outer.addWidget(eq_title)

        eq_row = QHBoxLayout()
        self.band_widgets = []
        bands = [60, 150, 400, "1k", "2.5k", "6k", "12k"]
        for i, b in enumerate(bands):
            bw = BandSlider(str(b), lambda v, idx=i: self._on_band_changed(idx, v))
            self.band_widgets.append(bw)
            eq_row.addWidget(bw)
        eq_outer.addLayout(eq_row)
        root.addWidget(eq_card)

        # پریست‌ها
        preset_row = QHBoxLayout()
        save_btn = QPushButton("ذخیره پریست")
        save_btn.clicked.connect(self._save_preset)
        load_btn = QPushButton("بارگذاری پریست")
        load_btn.clicked.connect(self._load_preset)
        preset_row.addWidget(save_btn)
        preset_row.addWidget(load_btn)
        preset_row.addStretch()
        root.addLayout(preset_row)

    def _apply_style(self):
        style_path = os.path.join(os.path.dirname(__file__), "style.qss")
        if os.path.exists(style_path):
            with open(style_path, "r", encoding="utf-8") as f:
                self.setStyleSheet(f.read())

    # ----------------------------------------------------------- devices ---
    def _load_devices(self):
        self.input_combo.clear()
        for d in list_input_devices():
            self.input_combo.addItem(f"{d['name']}", userData=d["index"])

        self.output_list.clear()
        vcable_idx = find_virtual_cable_index()
        for d in list_output_devices():
            item = QListWidgetItem(d["name"])
            item.setData(Qt.UserRole, d["index"])
            self.output_list.addItem(item)
            if d["index"] == vcable_idx:
                item.setSelected(True)

    # ------------------------------------------------------------ events ---
    def _toggle_power(self):
        if self.power_btn.isChecked():
            in_idx = self.input_combo.currentData()
            out_indices = [i.data(Qt.UserRole) for i in self.output_list.selectedItems()]
            self.manager.set_input_device(in_idx)
            self.manager.set_output_devices(out_indices)
            self.manager.start()
            if self.manager.running:
                self.power_btn.setText("ON")
            else:
                self.power_btn.setChecked(False)
        else:
            self.manager.stop()
            self.power_btn.setText("OFF")

    def _on_boost_changed(self, v):
        self.boost_value_lbl.setText(f"{v} dB")
        self.manager.dsp.set_boost_db(v)

    def _on_gate_changed(self, v):
        self.gate_value_lbl.setText(f"{v} dB")
        self.manager.dsp.set_gate_threshold(v)

    def _on_ai_toggle(self, state):
        self.manager.dsp.set_ai_denoise(bool(state))

    def _on_ai_strength_changed(self, v):
        self.manager.dsp.set_ai_denoise(self.ai_checkbox.isChecked(), strength=v / 100.0)

    def _on_band_changed(self, idx, v):
        self.manager.dsp.set_band_gain(idx, v)

    def _capture_noise(self):
        self.manager.dsp.start_noise_capture()
        QTimer.singleShot(2000, self.manager.dsp.stop_noise_capture)
        QMessageBox.information(self, "ضبط نویز", "لطفاً ۲ ثانیه ساکت بمانید تا نویز محیط ضبط شود.")

    def _on_levels(self, in_rms, out_rms):
        self._in_level = min(100, in_rms * 400)
        self._out_level = min(100, out_rms * 400)

    def _decay_meters(self):
        self.in_meter.setValue(int(self._in_level))
        self.out_meter.setValue(int(self._out_level))

    def _on_error(self, msg):
        QMessageBox.warning(self, "خطا", msg)

    # ------------------------------------------------------------ presets --
    def _current_settings(self) -> dict:
        return {
            "boost_db": self.boost_slider.value(),
            "gate_db": self.gate_slider.value(),
            "ai_enabled": self.ai_checkbox.isChecked(),
            "ai_strength": self.ai_strength_slider.value(),
            "bands": [bw.slider.value() for bw in self.band_widgets],
        }

    def _apply_settings(self, data: dict):
        self.boost_slider.setValue(data.get("boost_db", 0))
        self.gate_slider.setValue(data.get("gate_db", -45))
        self.ai_checkbox.setChecked(data.get("ai_enabled", True))
        self.ai_strength_slider.setValue(data.get("ai_strength", 70))
        for bw, v in zip(self.band_widgets, data.get("bands", [])):
            bw.set_value(v)

    def _save_preset(self):
        path, _ = QFileDialog.getSaveFileName(self, "ذخیره پریست", PRESET_DIR, "MicBoost Preset (*.mbpreset)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._current_settings(), f, ensure_ascii=False, indent=2)

    def _load_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "بارگذاری پریست", PRESET_DIR, "MicBoost Preset (*.mbpreset)")
        if path:
            with open(path, "r", encoding="utf-8") as f:
                self._apply_settings(json.load(f))

    def closeEvent(self, event):
        self.manager.stop()
        super().closeEvent(event)
