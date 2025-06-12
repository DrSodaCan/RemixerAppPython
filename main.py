import sys
import os
import asyncio
from os.path import basename

import soundfile as sf
import numpy as np
import sounddevice as sd
from PyQt6.QtGui import QPixmap, QFont, QColor
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton,
    QFileDialog, QLabel, QSlider, QHBoxLayout, QComboBox,
    QFormLayout, QSizePolicy, QCheckBox,
    QDialog, QLineEdit, QMessageBox, QProgressDialog,
    QColorDialog
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from pedalboard import Pedalboard
from effects import get_available_effects, get_param_configs
from splitter import convert_audio, spleeter_split, demucs_split


def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


class TrackEffectWidget(QWidget):
    def __init__(self, parent_track):
        super().__init__()
        self.parent_track = parent_track
        self.locked = False
        self.effect_name = None
        self.param_sliders = {}

        self.main_layout = QVBoxLayout()
        self.header_layout = QHBoxLayout()

        self.name_combo = QComboBox()
        self.name_combo.addItems(get_available_effects())
        combo_ss = (
            "QComboBox {"
            "   background-color: black;"
            "   color: white;"
            "   border: 2px solid white;"
            "   border-radius: 6px;"
            "   padding: 4px 8px;"
            "}"
            "QComboBox QAbstractItemView {"
            "   background-color: black;"
            "   color: white;"
            "   selection-background-color: #444444;"
            "}"
        )
        self.name_combo.setStyleSheet(combo_ss)

        self.name_combo.currentTextChanged.connect(self.on_effect_change)
        self.header_layout.addWidget(self.name_combo)

        self.lock_button = QPushButton("Lock")
        self.lock_button.clicked.connect(self.toggle_lock)
        self.header_layout.addWidget(self.lock_button)

        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.on_remove)
        self.header_layout.addWidget(self.remove_button)

        self.main_layout.addLayout(self.header_layout)

        self.params_form = QFormLayout()
        self.main_layout.addLayout(self.params_form)

        self.setLayout(self.main_layout)

        btn_ss = (
            "QPushButton {"
            "   background-color: black;"
            "   color: white;"
            "   border: 2px solid white;"
            "   border-radius: 6px;"
            "   padding: 4px 12px;"
            "}"
            "QPushButton:hover {"
            "   background-color: #222222;"
            "}"
            "QPushButton:pressed {"
            "   background-color: #111111;"
            "}"
        )


        self.lock_button.setStyleSheet(btn_ss)
        self.remove_button.setStyleSheet(btn_ss)

        self.on_effect_change(self.name_combo.currentText())

    def on_effect_change(self, name):
        while self.params_form.count():
            item = self.params_form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.param_sliders.clear()

        for cfg in get_param_configs(name):
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            default_norm = (cfg['default'] - cfg['min']) / (cfg['max'] - cfg['min'])
            slider.setValue(int(default_norm * 100))
            slider.sliderReleased.connect(self.parent_track.apply_effect)
            self.params_form.addRow(cfg['name'].replace('_', ' ').title(), slider)
            self.param_sliders[cfg['name']] = (slider, cfg)

        self.effect_name = name
        self.parent_track.apply_effect()

    def toggle_lock(self):
        self.locked = not self.locked

        self.name_combo.setVisible(not self.locked)

        self.lock_button.setText("Unlock" if self.locked else "Lock")

        for slider, _ in self.param_sliders.values():
            slider.setVisible(not self.locked)
            lbl = self.params_form.labelForField(slider)
            if lbl:
                lbl.setVisible(not self.locked)

        self.parent_track.apply_effect()


    def on_remove(self):
        self.setParent(None)
        self.parent_track.effect_widgets.remove(self)
        self.parent_track.apply_effect()


class Track(QWidget):
    instances = []

    def __init__(self, track_number, parent_app=None):
        super().__init__()
        self.track_number = track_number
        self.parent_app = parent_app
        self.original_audio_data = None
        self.audio_data = None
        self.sample_rate = None
        self.stream = None
        self.is_playing = False
        self.position = 0
        self.duration = 0.0
        self.muted = False
        self.soloed = False
        self.track_color = None

        Track.instances.append(self)
        self.effect_widgets = []
        self.init_ui()

    def init_ui(self):
        self.setFont(QFont("Roboto", 12))
        self.setStyleSheet("background-color: #303030; color: white;")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setLayout(layout)

        self.label = QLabel("No file loaded")
        header_font = QFont("Roboto", 14, QFont.Weight.DemiBold)
        self.label.setFont(header_font)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)

        #Import and Color buttons
        self.import_button = QPushButton("Import")
        self.import_button.setFont(QFont("Roboto", 12))
        self.import_button.clicked.connect(self.import_audio)

        self.color_button = QPushButton("Color")
        self.color_button.setFont(QFont("Roboto", 12))
        self.color_button.clicked.connect(self.choose_color)

        top_row = QHBoxLayout()
        top_row.addStretch()
        top_row.addWidget(self.import_button)
        top_row.addWidget(self.color_button)
        top_row.addStretch()
        layout.addLayout(top_row)

        self.mute_checkbox = QCheckBox("Mute")
        self.mute_checkbox.setFont(QFont("Roboto", 12))
        self.mute_checkbox.stateChanged.connect(lambda s: setattr(self, "muted", bool(s)))

        self.solo_checkbox = QCheckBox("Solo")
        self.solo_checkbox.setFont(QFont("Roboto", 12))
        self.solo_checkbox.stateChanged.connect(lambda s: setattr(self, "soloed", bool(s)))

        mute_row = QHBoxLayout()
        mute_row.addStretch()
        mute_row.addWidget(self.mute_checkbox)
        mute_row.addWidget(self.solo_checkbox)
        mute_row.addStretch()
        layout.addLayout(mute_row)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(8)

        vol_label = QLabel("Vol")
        vol_label.setFont(QFont("Roboto", 12))
        vol_row.addWidget(vol_label)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(50)
        vol_row.addWidget(self.volume_slider)

        layout.addLayout(vol_row)
        layout.addStretch()

        self.effects_container = QVBoxLayout()
        layout.addLayout(self.effects_container)

        self.add_effect_button = QPushButton("Add Effect")
        self.add_effect_button.setFont(QFont("Roboto", 12))
        self.add_effect_button.clicked.connect(self.add_effect)
        layout.addWidget(self.add_effect_button)

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_time)

        default_bg = "#303030"
        default_text = "white"
        self._apply_track_style(default_bg, default_text)

    def _button_style(self):
        """
        Compute a stylesheet string for this track's buttons, based on self.track_color.
        We take the track_color, lighten it (to stand out),
        then pick a contrasting text color + border color.
        """
        if self.track_color:
            base = QColor(self.track_color)
        else:
            base = QColor("#303030")

        btn_color = base.lighter(110).name()

        #Base text color by bg color
        r, g, b = base.red(), base.green(), base.blue()
        brightness = (r * 299 + g * 587 + b * 114) / 1000
        text_color = "black" if brightness > 128 else "white"

        return (
            f"QPushButton {{ "
            f"background-color: {btn_color}; "
            f"color: {text_color}; "
            f"border: 2px solid {text_color}; "
            f"border-radius: 6px; "
            f"padding: 6px 12px; "
            f"}}"
        )

    def _apply_track_style(self, bg_color: str, text_color: str):

        self.setStyleSheet(
            f"background-color: {bg_color}; color: {text_color}; border-radius: 15px;"
        )

        btn_ss = self._button_style()
        for w in (self.import_button, self.color_button, self.add_effect_button):
            w.setStyleSheet(btn_ss)

        check_ss = (
            f"QCheckBox {{ "
            f"color: {text_color}; "
            f"spacing: 8px; "
            f"}}"
        )
        self.mute_checkbox.setStyleSheet(check_ss)
        self.solo_checkbox.setStyleSheet(check_ss)

    def choose_color(self):
        color = QColorDialog.getColor(parent=self, title="Select Track Color")
        if color.isValid():
            self.track_color = color.name()
            r, g, b = color.red(), color.green(), color.blue()
            brightness = (r * 299 + g * 587 + b * 114) / 1000
            text_color = "black" if brightness > 128 else "white"
            # When the user chooses a new color, reapply everything:
            self._apply_track_style(self.track_color, text_color)

    def import_audio(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Open Audio File", "", "Audio Files (*.wav *.mp3 *.flac)")
        if fname:
            self.load_audio(fname)

    def load_audio(self, filename: str):
        data, sr = sf.read(filename, always_2d=True)
        self.original_audio_data = data
        self.sample_rate = sr

        self.apply_effect()

        self.duration = len(self.audio_data) / self.sample_rate

        base = os.path.basename(filename)
        name_without_ext = os.path.splitext(base)[0]
        cleaned = name_without_ext.capitalize()
        self.label.setText(cleaned)

    def audio_callback(self, outdata, frames, time, status):
        if status:
            print(status)
        vol = self.volume_slider.value() / 100
        any_solo = any(t.soloed for t in Track.instances)
        if self.audio_data is None:
            out = np.zeros((frames, 2))
        else:
            start = self.position
            end = start + frames
            if end <= len(self.audio_data):
                chunk = self.audio_data[start:end]
            else:
                chunk = self.audio_data[start:]
                pad = np.zeros((frames - chunk.shape[0], self.audio_data.shape[1]))
                chunk = np.vstack((chunk, pad))
            self.position = min(end, len(self.audio_data))
            out = chunk * vol
            if self.muted or (any_solo and not self.soloed):
                out = np.zeros_like(out)
        outdata[:] = out

    def play(self):
        if not self.is_playing and self.audio_data is not None:
            self.stream = sd.OutputStream(samplerate=self.sample_rate,
                                          channels=self.audio_data.shape[1],
                                          callback=self.audio_callback)
            self.stream.start()
            self.timer.start(100)
            self.is_playing = True

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.timer.stop()
        self.is_playing = False

    def update_time(self):
        current = min(self.position / self.sample_rate, self.duration)
        total = self.duration

    def add_effect(self):
        widget = TrackEffectWidget(self)
        self.effect_widgets.append(widget)
        self.effects_container.addWidget(widget)

    def apply_effect(self):
        if self.original_audio_data is None:
            return
        #build pedalboard chain
        chain = []
        for w in self.effect_widgets:
            if w.effect_name and w.effect_name != 'None':
                params = {}
                for name, (slider, cfg) in w.param_sliders.items():
                    norm = slider.value() / slider.maximum()
                    params[name] = cfg['min'] + (cfg['max'] - cfg['min']) * norm
                #instantiate effect class
                eff_cfg = get_param_configs(w.effect_name)
                from effects import EFFECTS
                cls = EFFECTS[w.effect_name]['class']
                chain.append(cls(**params))
        self.board = Pedalboard(chain)
        self.audio_data = self.board(self.original_audio_data.copy(), self.sample_rate)

class SplitterThread(QThread):
    finished = pyqtSignal(tuple)
    error = pyqtSignal(str)

    def __init__(self, path, method):
        super().__init__()
        self.path = path
        self.method = method

    def run(self):
        try:
            conv = convert_audio(self.path)
            stems = asyncio.run(spleeter_split(conv)) if self.method == 'spleeter' else asyncio.run(demucs_split(conv))
            self.finished.emit(stems)
        except Exception as e:
            self.error.emit(str(e))


class AudioApp(QWidget):
    def __init__(self):
        super().__init__()
        self.tracks = []
        self.is_playing = False
        self.init_ui()
    def init_ui(self):
        self.setStyleSheet("background-color: #202020; color: white;")
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 10)
        main_layout.setSpacing(5)

        btn_font = QFont("Roboto", 16)
        play_font = QFont("Roboto", 24, QFont.Weight.DemiBold)

        #Buttion Style Sheet
        self.btn_style = (
            "padding: 15px 30px; "
            "border-radius: 12px; "
            "background-color: {}; color: white;"
        )

        #Logo
        logo_layout = QHBoxLayout()
        logo_layout.setContentsMargins(10, 10, 10, 0)
        logo_label = QLabel()
        pixmap = QPixmap("./graphics/logo.png").scaled(614, 82,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        logo_label.setPixmap(pixmap)
        logo_layout.addWidget(logo_label)
        main_layout.addLayout(logo_layout)
        main_layout.addStretch()

        main_layout.addSpacing(75)

        #Now Playing label
        self.now_playing_label = QLabel("")
        self.now_playing_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.now_playing_label.setFont(btn_font)
        main_layout.addWidget(self.now_playing_label)

        #Primary controls HBox
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(10)

        #Splitter button
        self.split_button = QPushButton('Splitter')
        self.split_button.setFont(btn_font)
        self.split_button.setStyleSheet(self.btn_style.format('#800080'))
        self.split_button.setMinimumSize(120, 50)
        self.split_button.clicked.connect(self.open_splitter_dialog)

        #Play button
        self.play_button = QPushButton('Play')
        self.play_button.setFont(play_font)
        self.play_button.setStyleSheet(self.btn_style.format('#008000'))
        self.play_button.setFixedSize(200, 100)
        self.play_button.clicked.connect(self.toggle_play_stop)

        #Export button
        self.export_button = QPushButton('Export')
        self.export_button.setFont(btn_font)
        self.export_button.setStyleSheet(self.btn_style.format('#008080'))
        self.export_button.setMinimumSize(120, 50)
        self.export_button.clicked.connect(self.export_tracks)

        #assemble control row
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self.split_button)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self.play_button)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self.export_button)
        ctrl_layout.addStretch()
        main_layout.addLayout(ctrl_layout)

        #Reset button
        reset_layout = QHBoxLayout()
        reset_layout.addStretch()
        self.reset_button = QPushButton('Reset')
        self.reset_button.setFont(btn_font)
        self.reset_button.setStyleSheet(
            "padding: 10px 20px; border-radius: 10px; "
            "background-color: orange; color: black;"
        )
        self.reset_button.clicked.connect(self.reset_all)
        reset_layout.addWidget(self.reset_button)
        reset_layout.addStretch()
        main_layout.addLayout(reset_layout)

        #Timer
        #xx:xx
        self.global_time_label = QLabel("00:00 / 00:00")
        self.global_time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.global_time_label.setFont(btn_font)
        main_layout.addWidget(self.global_time_label)

        #Slider
        self.global_slider = QSlider(Qt.Orientation.Horizontal)
        self.global_slider.setRange(0, 1000)
        self.global_slider.sliderMoved.connect(self.seek_all)
        slider_container = QHBoxLayout()
        slider_container.setContentsMargins(20, 0, 20, 0)
        slider_container.addWidget(self.global_slider)
        main_layout.addLayout(slider_container)

        #Timer
        self.global_timer = QTimer()
        self.global_timer.timeout.connect(self.update_global_progress)


        main_layout.addSpacing(50)
        #Tracks area
        tracks_layout = QHBoxLayout()
        tracks_layout.setSpacing(15)

        default_colors = ['#FF4C4C', '#4C6FFF', '#3BCB3B', '#FFEB3B']
        for i in range(4):
            tr = Track(i + 1, parent_app=self)
            default_color = default_colors[i % len(default_colors)]
            tr.track_color = default_color
            r = tr.palette().color(tr.backgroundRole()).red()
            g = tr.palette().color(tr.backgroundRole()).green()
            b = tr.palette().color(tr.backgroundRole()).blue()
            brightness = (r * 299 + g * 587 + b * 114) / 1000
            text_color = 'black' if brightness > 128 else 'white'
            tr.setStyleSheet(f"background-color: {default_color}; color: {text_color}; border-radius: 15px")
            self.tracks.append(tr)
            tracks_layout.addWidget(tr)

        main_layout.addLayout(tracks_layout, 1)

        self.setLayout(main_layout)
        self.setWindowTitle('Remixer Demo')
        self.resize(1920, 1080)

    def toggle_play_stop(self):
        if not self.is_playing:
            #check already finished
            for t in self.tracks:
                if t.audio_data is not None and t.position >= len(t.audio_data):
                    t.position = 0
                    t.update_time()

            for t in self.tracks:
                t.play()
            self.global_timer.start(100)

            self.play_button.setText('Stop')
            self.play_button.setStyleSheet(self.btn_style.format('#FF0000'))
            self.is_playing = True
        else:
            for t in self.tracks:
                t.stop()
            self.global_timer.stop()

            self.play_button.setText('Play')
            self.play_button.setStyleSheet(self.btn_style.format('#008000'))
            self.is_playing = False

    def reset_all(self):
        reply = QMessageBox.question(
            self,
            'Confirm Reset',
            'Are you sure you want to clear all tracks, remove loaded files and effects?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        #Stop and reset elements
        if self.is_playing:
            self.toggle_play_stop()

        self.now_playing_label.setText('')
        self.global_slider.setValue(0)
        self.global_time_label.setText("00:00 / 00:00")

        #clear each track
        for t in self.tracks:
            t.stop()

            #clear audio data
            t.original_audio_data = None
            t.audio_data = None
            t.sample_rate = None
            t.position = 0
            t.duration = 0.0

            #reset UI
            t.label.setText("No file loaded")

            t.volume_slider.setValue(50)
            t.mute_checkbox.setChecked(False)
            t.solo_checkbox.setChecked(False)

            #remove all effects
            for w in t.effect_widgets:
                w.setParent(None)
            t.effect_widgets.clear()

    def update_global_progress(self):
        #figure out the furthest playback position and total length
        max_pos = 0
        max_len = 1
        sample_rate = None
        for t in self.tracks:
            if t.audio_data is None:
                continue
            length = len(t.audio_data)
            if length > max_len:
                max_len = length
                sample_rate = t.sample_rate
            if t.position > max_pos:
                max_pos = t.position

        if self.is_playing and max_pos >= max_len:
            # stop playback
            for t in self.tracks:
                t.stop()
                t.position = 0
                t.update_time()
            self.global_timer.stop()
            # reset UI
            self.global_slider.blockSignals(True)
            self.global_slider.setValue(0)
            self.global_slider.blockSignals(False)
            self.global_time_label.setText("00:00 / " + format_time(max_len / sample_rate))
            self.play_button.setText('Play')
            self.play_button.setStyleSheet(self.btn_style.format('#008000'))
            self.is_playing = False
            return


        #update slider
        val = int((max_pos / max_len) * 1000)
        self.global_slider.blockSignals(True)
        self.global_slider.setValue(val)
        self.global_slider.blockSignals(False)

        #update label
        if sample_rate:
            current_sec = max_pos / sample_rate
            total_sec = max_len / sample_rate
            self.global_time_label.setText(f"{format_time(current_sec)} / {format_time(total_sec)}")

    def open_splitter_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Splitter')
        layout = QVBoxLayout()
        form = QFormLayout()

        file_edit = QLineEdit()
        browse = QPushButton('Browse')
        browse.clicked.connect(lambda: file_edit.setText(QFileDialog.getOpenFileName(self, 'Select Audio', '', "Audio Files (*.wav *.mp3 *.flac)")[0]))
        row = QHBoxLayout(); row.addWidget(file_edit); row.addWidget(browse)
        form.addRow('File', row)

        method = QComboBox(); method.addItems(["Demucs", "Spleeter"])
        form.addRow('Method', method)

        layout.addLayout(form)
        go = QPushButton('Split')
        go.clicked.connect(lambda: self.handle_split(dialog, file_edit.text(), method.currentText().lower()))
        layout.addWidget(go)
        dialog.setLayout(layout)
        dialog.exec()

    def handle_split(self, dialog, path, method):
        if not path:
            QMessageBox.warning(self, 'No File', 'Select a file first')
            return
        dialog.accept()

        #show the song name above the Play button
        from os.path import basename
        self.now_playing_label.setText(f"Now playing: {basename(path)}")

        #progress bar
        self.progress = QProgressDialog('Splitting in progress…', None, 0, 0, self)
        self.progress.setWindowTitle('Please wait')
        self.progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.progress.setCancelButton(None)
        self.progress.show()

        #background task
        self.splitter_thread = SplitterThread(path, method)
        self.splitter_thread.finished.connect(self.on_split_finished)
        self.splitter_thread.error.connect(self.on_split_error)
        self.splitter_thread.start()

    def on_split_finished(self, stems):
        self.progress.close()
        for i, t in enumerate(self.tracks[:4]):
            t.load_audio(stems[i])
        QMessageBox.information(self, 'Done', 'Splitting complete!')
        self.splitter_thread = None

    def on_split_error(self, err_msg):
        self.progress.close()
        QMessageBox.critical(self, 'Error', err_msg)
        self.splitter_thread = None

    def seek_all(self, value):
        was_playing = self.is_playing

        if was_playing:
            for t in self.tracks:
                t.stop()
            self.global_timer.stop()
            self.play_button.setText('Play')
            self.is_playing = False

        max_len = 1
        for t in self.tracks:
            if t.audio_data is not None:
                max_len = max(max_len, len(t.audio_data))
        target = int((value / 1000) * max_len)

        for t in self.tracks:
            if t.audio_data is not None:
                t.position = min(target, len(t.audio_data))
                t.update_time()

        if was_playing:
            for t in self.tracks:
                t.play()
            self.global_timer.start(100)
            self.play_button.setText('Stop')

            self.is_playing = True

    def export_tracks(self):
        mixed, sr = None, None
        for t in self.tracks:
            if t.audio_data is None: continue
            data = t.audio_data.copy() * (t.volume_slider.value()/100)
            if mixed is None:
                mixed, sr = data, t.sample_rate
            else:
                maxlen = max(mixed.shape[0], data.shape[0])
                pad1 = np.zeros((maxlen-mixed.shape[0], mixed.shape[1]))
                pad2 = np.zeros((maxlen-data.shape[0], data.shape[1]))
                mixed = np.vstack((mixed,pad1)) + np.vstack((data,pad2))
        if mixed is None:
            QMessageBox.warning(self, 'No Tracks', 'Load at least one track')
            return
        mx = np.max(np.abs(mixed))
        if mx>1: mixed/=mx
        save,_ = QFileDialog.getSaveFileName(self, 'Save Mix', '', "WAV (*.wav)")
        if save:
            sf.write(save, mixed, sr)
            QMessageBox.information(self, 'Done', f'Saved to {save}')

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyleSheet(
        "QWidget { color: white; background-color: #202020; }\n"
        "QSlider::groove:horizontal { background: #404040; height: 8px; border-radius: 4px; }\n"
        "QSlider::sub-page:horizontal { background: #888888; border-radius: 4px; }\n"
        "QSlider::add-page:horizontal { background: #505050; border-radius: 4px; }\n"
        "QSlider::handle:horizontal { background: #A0A0A0; width: 12px; margin: -2px 0; border-radius: 6px; }\n"
        "QPushButton { background: #404040; color: white; }\n"
        "QComboBox, QLineEdit { background: #303030; color: white; }"
    )
    w = AudioApp()
    w.show()
    sys.exit(app.exec())