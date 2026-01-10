import threading
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from app_settings import AppSettings, settings_path
from meeting_service import MeetingService


APP_STYLE = """
QWidget {
    font-family: "Helvetica Neue", "Helvetica", "Arial", sans-serif;
    color: #2e3440;
}

QMainWindow {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 #f7f2eb, stop: 1 #ece6dd
    );
}

QFrame#Panel {
    background: #fbf9f4;
    border: 1px solid rgba(46, 52, 64, 0.12);
    border-radius: 24px;
}

QLabel#Eyebrow {
    text-transform: uppercase;
    letter-spacing: 2px;
    font-size: 10px;
    color: #5e646c;
}

QLabel#StatusPill {
    padding: 3px 8px;
    border-radius: 999px;
    font-weight: 600;
    font-size: 10px;
    background: rgba(46, 52, 64, 0.08);
    color: #5e646c;
}

QLabel#StatusPill[status="recording"] {
    background: rgba(196, 87, 45, 0.16);
    color: #c4572d;
}

QLabel#StatusPill[status="processing"] {
    background: rgba(246, 166, 56, 0.2);
    color: #a45a00;
}

QLabel#StatusPill[status="complete"] {
    background: rgba(52, 199, 89, 0.18);
    color: #1d7b3a;
}

QLabel#StatusPill[status="error"] {
    background: rgba(196, 87, 45, 0.2);
    color: #c4572d;
}

QLabel#StatusPill[status="idle"] {
    background: rgba(46, 52, 64, 0.08);
    color: #5e646c;
}

QLabel#SourcePill {
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 10px;
    background: rgba(46, 52, 64, 0.08);
    color: #5e646c;
}

QLabel#SourcePill[ready="true"] {
    background: rgba(47, 111, 108, 0.16);
    color: #2f6f6c;
}

QLabel#SourcePill[ready="false"] {
    background: rgba(196, 87, 45, 0.18);
    color: #c4572d;
}

QLineEdit, QComboBox, QTextEdit, QPlainTextEdit, QListWidget, QScrollArea {
    border: 1px solid rgba(46, 52, 64, 0.12);
    border-radius: 24px;
    padding: 8px 10px;
    background: #fffefb;
}

QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus, QListWidget:focus {
    border-color: #2f6f6c;
    outline: none;
}

QPushButton {
    border: none;
    border-radius: 24px;
    padding: 8px 14px;
    font-weight: 600;
}

QComboBox {
    padding-right: 28px;
}

QComboBox::drop-down {
    border: none;
    border-left: 1px solid rgba(46, 52, 64, 0.12);
    width: 24px;
    subcontrol-origin: padding;
    subcontrol-position: top right;
    border-top-right-radius: 24px;
    border-bottom-right-radius: 24px;
}

QComboBox QAbstractItemView {
    border-radius: 18px;
    selection-background-color: rgba(47, 111, 108, 0.12);
}

QPushButton[variant="primary"] {
    background: #2f6f6c;
    color: white;
}

QPushButton[variant="danger"] {
    background: #c4572d;
    color: white;
}

QPushButton[variant="ghost"] {
    background: transparent;
    border: 1px solid rgba(46, 52, 64, 0.12);
}

QPushButton[size="small"] {
    padding: 6px 12px;
    font-size: 11px;
}

QPushButton:disabled {
    color: #8b9197;
    background: rgba(46, 52, 64, 0.08);
}
"""


class UiBus(QtCore.QObject):
    status = QtCore.Signal(str, str, str)
    transcription = QtCore.Signal(str)
    error = QtCore.Signal(str)
    summary_ready = QtCore.Signal(bool, str)
    whisper_ready = QtCore.Signal(bool, str)
    keypoints_result = QtCore.Signal(str)
    actions_result = QtCore.Signal(str)
    issues_result = QtCore.Signal(str)
    ask_result = QtCore.Signal(str)
    last_topic_result = QtCore.Signal(str)
    recordings_result = QtCore.Signal(list)
    devices_result = QtCore.Signal(list)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LocalScribe Desktop")

        self.bus = UiBus()
        self.settings = AppSettings.load(settings_path())
        self.meeting_service = MeetingService(self.settings)

        self.current_meeting_id = None
        self.current_meeting_name = None
        self.transcript_lines = []
        self.auto_scroll = True
        self.start_timestamp = None

        self._build_ui()
        self._bind_signals()
        self._load_settings_into_ui()
        self._reset_summary()
        self._load_devices()
        self._load_prompts()
        self._load_recordings()
        self._check_summary_ready()
        self._check_whisper_ready()

    def _build_ui(self):
        container = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(container)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(24, 24, 24, 24)

        topbar = QtWidgets.QHBoxLayout()
        brand = QtWidgets.QVBoxLayout()
        brand_title = QtWidgets.QLabel("LocalScribe")
        brand_title.setStyleSheet("font-size: 24px; font-weight: 700;")
        brand_tagline = QtWidgets.QLabel("Total Control")
        brand_tagline.setStyleSheet("color: #5e646c;")
        brand.addWidget(brand_title)
        brand.addWidget(brand_tagline)
        topbar.addLayout(brand)
        topbar.addStretch()

        self.open_settings_button = QtWidgets.QPushButton("Settings")
        self.open_settings_button.setProperty("variant", "ghost")
        topbar.addWidget(self.open_settings_button)
        main_layout.addLayout(topbar)

        self.settings_panel = self._build_settings_panel()
        self.settings_panel.setVisible(False)
        main_layout.addWidget(self.settings_panel)

        main_layout.addWidget(self._build_main_panels())
        main_layout.addWidget(self._build_library_panel())

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(container)

        self.setCentralWidget(scroll)
        self.setStyleSheet(APP_STYLE)

    def _panel_frame(self):
        frame = QtWidgets.QFrame()
        frame.setObjectName("Panel")
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        frame.setLayout(QtWidgets.QVBoxLayout())
        frame.layout().setContentsMargins(18, 18, 18, 18)
        frame.layout().setSpacing(12)
        return frame

    def _build_settings_panel(self):
        panel = self._panel_frame()
        header = QtWidgets.QHBoxLayout()
        title_col = QtWidgets.QVBoxLayout()
        eyebrow = QtWidgets.QLabel("Settings")
        eyebrow.setObjectName("Eyebrow")
        title = QtWidgets.QLabel("Storage Paths")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        title_col.addWidget(eyebrow)
        title_col.addWidget(title)
        header.addLayout(title_col)
        header.addStretch()
        self.close_settings_button = QtWidgets.QPushButton("Close")
        self.close_settings_button.setProperty("variant", "ghost")
        self.close_settings_button.setProperty("size", "small")
        header.addWidget(self.close_settings_button)
        panel.layout().addLayout(header)

        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)

        self.output_path_input = QtWidgets.QLineEdit()
        self.summary_api_url_input = QtWidgets.QLineEdit()
        self.summary_model_input = QtWidgets.QLineEdit()
        self.summary_api_token_input = QtWidgets.QLineEdit()
        self.summary_api_token_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.whisper_mode_select = QtWidgets.QComboBox()
        self.whisper_mode_select.addItems(["local", "api"])
        self.whisper_api_url_input = QtWidgets.QLineEdit()
        self.whisper_api_token_input = QtWidgets.QLineEdit()
        self.whisper_api_token_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.whisper_api_status = QtWidgets.QLabel("Status: unknown")
        self.whisper_api_status.setObjectName("SourcePill")
        self.whisper_cpp_path_input = QtWidgets.QLineEdit()
        self.whisper_stream_path_input = QtWidgets.QLineEdit()
        self.whisper_model_path_input = QtWidgets.QLineEdit()

        form.addWidget(QtWidgets.QLabel("Transcripts folder"), 0, 0)
        form.addWidget(self.output_path_input, 1, 0)
        form.addWidget(QtWidgets.QLabel("Summary API URL"), 0, 1)
        form.addWidget(self.summary_api_url_input, 1, 1)
        form.addWidget(QtWidgets.QLabel("Summary model"), 2, 0)
        form.addWidget(self.summary_model_input, 3, 0)
        form.addWidget(QtWidgets.QLabel("Summary API token"), 2, 1)
        form.addWidget(self.summary_api_token_input, 3, 1)
        form.addWidget(QtWidgets.QLabel("Whisper mode"), 4, 0)
        form.addWidget(self.whisper_mode_select, 5, 0)
        form.addWidget(QtWidgets.QLabel("Whisper API URL"), 4, 1)
        form.addWidget(self.whisper_api_url_input, 5, 1)
        form.addWidget(QtWidgets.QLabel("Whisper API token"), 6, 0)
        form.addWidget(self.whisper_api_token_input, 7, 0)
        form.addWidget(self.whisper_api_status, 7, 1)
        form.addWidget(QtWidgets.QLabel("Whisper.cpp path"), 8, 0)
        form.addWidget(self.whisper_cpp_path_input, 9, 0)
        form.addWidget(QtWidgets.QLabel("Whisper stream path"), 8, 1)
        form.addWidget(self.whisper_stream_path_input, 9, 1)
        form.addWidget(QtWidgets.QLabel("Whisper model path"), 10, 0)
        form.addWidget(self.whisper_model_path_input, 11, 0)

        panel.layout().addLayout(form)
        self.save_settings_button = QtWidgets.QPushButton("Save Settings")
        self.save_settings_button.setProperty("variant", "primary")
        panel.layout().addWidget(self.save_settings_button)

        return panel

    def _build_main_panels(self):
        container = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(container)
        layout.setSpacing(16)

        self.session_panel = self._build_session_panel()
        self.transcript_panel = self._build_transcript_panel()
        self.summary_panel = self._build_summary_panel()

        layout.addWidget(self.session_panel, 1)
        layout.addWidget(self.transcript_panel, 2)
        layout.addWidget(self.summary_panel, 1)

        return container

    def _build_session_panel(self):
        panel = self._panel_frame()
        panel.layout().setSpacing(6)
        header = QtWidgets.QVBoxLayout()
        header.setSpacing(4)
        header.setContentsMargins(0, 0, 0, 0)
        eyebrow = QtWidgets.QLabel("Session")
        eyebrow.setObjectName("Eyebrow")
        header.addWidget(eyebrow)
        self.status_pill = QtWidgets.QLabel("Idle")
        self.status_pill.setObjectName("StatusPill")
        self.status_pill.setProperty("status", "idle")
        self.status_pill.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        header.addWidget(self.status_pill, alignment=QtCore.Qt.AlignLeft)
        header_widget = QtWidgets.QWidget()
        header_widget.setLayout(header)
        header_widget.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        panel.layout().addWidget(header_widget)

        meta = QtWidgets.QGridLayout()
        meta.setVerticalSpacing(2)
        meta.setContentsMargins(0, 0, 0, 0)
        meta.addWidget(QtWidgets.QLabel("Meeting Name"), 0, 0)
        self.meeting_name_label = QtWidgets.QLabel("Not recording")
        self.meeting_name_label.setStyleSheet("font-weight: 600;")
        meta.addWidget(self.meeting_name_label, 1, 0)
        meta.addWidget(QtWidgets.QLabel("Elapsed"), 0, 1)
        self.elapsed_label = QtWidgets.QLabel("00:00:00")
        self.elapsed_label.setStyleSheet("font-weight: 600;")
        meta.addWidget(self.elapsed_label, 1, 1)
        panel.layout().addLayout(meta)

        form = QtWidgets.QFormLayout()
        form.setVerticalSpacing(4)
        form.setContentsMargins(0, 0, 0, 0)
        self.meeting_input = QtWidgets.QLineEdit()
        self.device_select = QtWidgets.QComboBox()
        self.prompt_select = QtWidgets.QComboBox()
        form.addRow("Meeting name", self.meeting_input)
        form.addRow("Audio device", self.device_select)
        form.addRow("Summary template", self.prompt_select)
        panel.layout().addLayout(form)

        buttons = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton("Start Recording")
        self.start_button.setProperty("variant", "primary")
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setProperty("variant", "danger")
        self.stop_button.setEnabled(False)
        self.refresh_devices_button = QtWidgets.QPushButton("Refresh Devices")
        self.refresh_devices_button.setProperty("variant", "ghost")
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.refresh_devices_button)
        panel.layout().addLayout(buttons)

        self.session_hint = QtWidgets.QLabel("")
        self.session_hint.setStyleSheet("color: #5e646c; font-size: 12px;")
        panel.layout().addWidget(self.session_hint)

        return panel

    def _build_transcript_panel(self):
        panel = self._panel_frame()
        header = QtWidgets.QHBoxLayout()
        title_col = QtWidgets.QVBoxLayout()
        eyebrow = QtWidgets.QLabel("Live Transcript")
        eyebrow.setObjectName("Eyebrow")
        self.transcript_source = QtWidgets.QLabel("Source: Unknown")
        self.transcript_source.setObjectName("SourcePill")
        title_col.addWidget(eyebrow)
        title_col.addWidget(self.transcript_source)
        header.addLayout(title_col)
        header.addStretch()

        self.auto_scroll_button = QtWidgets.QPushButton("Auto-scroll: On")
        self.auto_scroll_button.setProperty("variant", "ghost")
        self.auto_scroll_button.setProperty("size", "small")
        self.copy_transcript_button = QtWidgets.QPushButton("Copy")
        self.copy_transcript_button.setProperty("variant", "ghost")
        self.copy_transcript_button.setProperty("size", "small")
        header.addWidget(self.auto_scroll_button)
        header.addWidget(self.copy_transcript_button)
        panel.layout().addLayout(header)

        self.transcript_stream = QtWidgets.QPlainTextEdit()
        self.transcript_stream.setReadOnly(True)
        self.transcript_stream.setPlaceholderText("Waiting for new audio...")
        panel.layout().addWidget(self.transcript_stream)
        return panel

    def _build_summary_panel(self):
        panel = self._panel_frame()
        header = QtWidgets.QHBoxLayout()
        title_col = QtWidgets.QVBoxLayout()
        eyebrow = QtWidgets.QLabel("Summary")
        eyebrow.setObjectName("Eyebrow")
        title_col.addWidget(eyebrow)
        header.addLayout(title_col)
        header.addStretch()

        self.keypoints_tab = QtWidgets.QPushButton("Key Points")
        self.actions_tab = QtWidgets.QPushButton("Actions")
        self.issues_tab = QtWidgets.QPushButton("Issues")
        self.ask_tab = QtWidgets.QPushButton("Ask")
        for tab in (self.keypoints_tab, self.actions_tab, self.issues_tab, self.ask_tab):
            tab.setCheckable(True)
            tab.setProperty("variant", "ghost")
            tab.setProperty("size", "small")
        self.keypoints_tab.setChecked(True)

        tabs = QtWidgets.QHBoxLayout()
        tabs.addWidget(self.keypoints_tab)
        tabs.addWidget(self.actions_tab)
        tabs.addWidget(self.issues_tab)
        tabs.addWidget(self.ask_tab)
        panel.layout().addLayout(tabs)

        self.summary_stack = QtWidgets.QStackedWidget()
        self.summary_cards = self._build_summary_cards()
        self.ask_panel = self._build_ask_panel()
        self.summary_stack.addWidget(self.summary_cards)
        self.summary_stack.addWidget(self.ask_panel)
        panel.layout().addWidget(self.summary_stack)

        self.summary_ready_hint = QtWidgets.QLabel("Checking model readiness...")
        self.summary_ready_hint.setStyleSheet("font-size: 12px; color: #5e646c;")
        panel.layout().addWidget(self.summary_ready_hint)

        return panel

    def _build_summary_cards(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setSpacing(12)

        self.keypoints_list = QtWidgets.QListWidget()
        self.actions_list = QtWidgets.QListWidget()
        self.issues_list = QtWidgets.QListWidget()

        for title, list_widget in (
            ("Key Points", self.keypoints_list),
            ("Action Items", self.actions_list),
            ("Issues & Solutions", self.issues_list),
        ):
            card = self._panel_frame()
            card.setObjectName("Panel")
            label = QtWidgets.QLabel(title)
            label.setStyleSheet("font-size: 14px; font-weight: 600;")
            card.layout().addWidget(label)
            card.layout().addWidget(list_widget)
            layout.addWidget(card)

        return widget

    def _build_ask_panel(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        title = QtWidgets.QLabel("Ask the notes")
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        hint = QtWidgets.QLabel("Use the live transcript to answer quick questions.")
        hint.setStyleSheet("color: #5e646c; font-size: 12px;")
        layout.addWidget(title)
        layout.addWidget(hint)

        self.ask_input = QtWidgets.QTextEdit()
        self.ask_input.setPlaceholderText("Ask about decisions, owners, or timelines...")
        layout.addWidget(self.ask_input)

        ask_buttons = QtWidgets.QHBoxLayout()
        self.ask_submit = QtWidgets.QPushButton("What should I ask next?")
        self.ask_submit.setProperty("variant", "primary")
        self.ask_submit.setProperty("size", "small")
        self.ask_last_topic = QtWidgets.QPushButton("What was the last topic")
        self.ask_last_topic.setProperty("variant", "ghost")
        self.ask_last_topic.setProperty("size", "small")
        ask_buttons.addWidget(self.ask_submit)
        ask_buttons.addWidget(self.ask_last_topic)
        layout.addLayout(ask_buttons)

        self.ask_response = QtWidgets.QLabel("No answers yet.")
        self.ask_response.setWordWrap(True)
        self.ask_response.setStyleSheet(
            "background: #fffefc; border: 1px solid rgba(46, 52, 64, 0.12);"
            "border-radius: 10px; padding: 10px; color: #5e646c;"
        )
        layout.addWidget(self.ask_response)

        return widget

    def _build_library_panel(self):
        panel = self._panel_frame()
        header = QtWidgets.QHBoxLayout()
        title_col = QtWidgets.QVBoxLayout()
        eyebrow = QtWidgets.QLabel("Library")
        eyebrow.setObjectName("Eyebrow")
        title = QtWidgets.QLabel("Recent Recordings")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        title_col.addWidget(eyebrow)
        title_col.addWidget(title)
        header.addLayout(title_col)
        header.addStretch()
        self.refresh_recordings_button = QtWidgets.QPushButton("Refresh")
        self.refresh_recordings_button.setProperty("variant", "ghost")
        header.addWidget(self.refresh_recordings_button)
        panel.layout().addLayout(header)

        self.recordings_container = QtWidgets.QWidget()
        self.recordings_layout = QtWidgets.QVBoxLayout(self.recordings_container)
        self.recordings_layout.setSpacing(12)
        self.recordings_layout.setContentsMargins(0, 0, 0, 0)

        recordings_scroll = QtWidgets.QScrollArea()
        recordings_scroll.setWidgetResizable(True)
        recordings_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        recordings_scroll.setWidget(self.recordings_container)
        recordings_scroll.setMinimumHeight(420)
        panel.layout().addWidget(recordings_scroll)
        return panel


    def _bind_signals(self):
        self.open_settings_button.clicked.connect(self._open_settings)
        self.close_settings_button.clicked.connect(self._close_settings)
        self.save_settings_button.clicked.connect(self._save_settings)
        self.refresh_devices_button.clicked.connect(self._load_devices)
        self.refresh_recordings_button.clicked.connect(self._load_recordings)
        self.start_button.clicked.connect(self._start_recording)
        self.stop_button.clicked.connect(self._stop_recording)
        self.auto_scroll_button.clicked.connect(self._toggle_auto_scroll)
        self.copy_transcript_button.clicked.connect(self._copy_transcript)
        self.keypoints_tab.clicked.connect(lambda: self._set_active_tab("keypoints"))
        self.actions_tab.clicked.connect(lambda: self._set_active_tab("actions"))
        self.issues_tab.clicked.connect(lambda: self._set_active_tab("issues"))
        self.ask_tab.clicked.connect(lambda: self._set_active_tab("ask"))
        self.ask_submit.clicked.connect(self._ask_question)
        self.ask_last_topic.clicked.connect(self._ask_last_topic)

        self.bus.status.connect(self._handle_status)
        self.bus.transcription.connect(self._handle_transcription)
        self.bus.error.connect(self._show_error)
        self.bus.summary_ready.connect(self._handle_summary_ready)
        self.bus.whisper_ready.connect(self._handle_whisper_ready)
        self.bus.keypoints_result.connect(self._handle_keypoints)
        self.bus.actions_result.connect(self._handle_actions)
        self.bus.issues_result.connect(self._handle_issues)
        self.bus.ask_result.connect(self._handle_ask)
        self.bus.last_topic_result.connect(self._handle_last_topic)
        self.bus.recordings_result.connect(self._render_recordings)
        self.bus.devices_result.connect(self._populate_devices)

        self.elapsed_timer = QtCore.QTimer(self)
        self.elapsed_timer.timeout.connect(self._update_elapsed)

    def _load_settings_into_ui(self):
        self.output_path_input.setText(self.settings.calls_output_path)
        self.summary_api_url_input.setText(self.settings.summary_api_url)
        self.summary_model_input.setText(self.settings.summary_api_model)
        self.summary_api_token_input.setText(self.settings.summary_api_token or "")
        self.whisper_mode_select.setCurrentText(self.settings.whisper_mode)
        self.whisper_api_url_input.setText(self.settings.whisper_api_url)
        self.whisper_api_token_input.setText(self.settings.whisper_api_token or "")
        self.whisper_cpp_path_input.setText(self.settings.whisper_cpp_path)
        self.whisper_stream_path_input.setText(self.settings.whisper_stream_path)
        self.whisper_model_path_input.setText(self.settings.whisper_model_path)

    def _save_settings(self):
        output_path = self.output_path_input.text().strip()
        if not output_path:
            self._show_error("Please enter a transcripts folder.")
            return

        self.settings.calls_output_path = output_path
        self.settings.summary_api_url = self.summary_api_url_input.text().strip()
        self.settings.summary_api_model = self.summary_model_input.text().strip()
        self.settings.summary_api_token = self.summary_api_token_input.text()
        self.settings.whisper_mode = self.whisper_mode_select.currentText().strip()
        self.settings.whisper_api_url = self.whisper_api_url_input.text().strip()
        self.settings.whisper_api_token = self.whisper_api_token_input.text()
        self.settings.whisper_cpp_path = self.whisper_cpp_path_input.text().strip()
        self.settings.whisper_stream_path = self.whisper_stream_path_input.text().strip()
        self.settings.whisper_model_path = self.whisper_model_path_input.text().strip()
        if self.settings.whisper_cpp_path and not self.settings.whisper_stream_path:
            self.settings.whisper_stream_path = str(Path(self.settings.whisper_cpp_path) / "stream")
        self.settings.save(settings_path())
        self.meeting_service.update_settings(self.settings)
        self._close_settings()
        self._load_recordings()
        self._check_whisper_ready()
        self._check_summary_ready()

    def _open_settings(self):
        self.settings_panel.setVisible(True)

    def _close_settings(self):
        self.settings_panel.setVisible(False)

    def _load_devices(self):
        def worker():
            devices = self.meeting_service.get_audio_devices()
            self.bus.devices_result.emit(devices)

        threading.Thread(target=worker, daemon=True).start()

    @QtCore.Slot(list)
    def _populate_devices(self, devices):
        self.device_select.clear()
        for device in devices:
            self.device_select.addItem(device["name"], device["id"])

    def _load_prompts(self):
        prompts = self.meeting_service.get_available_prompts()
        self.prompt_select.clear()
        for prompt in prompts:
            self.prompt_select.addItem(prompt["name"], prompt["id"])

    def _load_recordings(self):
        def worker():
            files = self.meeting_service.get_meeting_files()
            self.bus.recordings_result.emit(files)

        threading.Thread(target=worker, daemon=True).start()

    def _render_recordings(self, files):
        for i in reversed(range(self.recordings_layout.count())):
            item = self.recordings_layout.itemAt(i).widget()
            if item:
                item.deleteLater()

        if not files:
            empty = QtWidgets.QLabel("No recordings found. Use the CLI to create your first recording!")
            empty.setStyleSheet("color: #5e646c;")
            self.recordings_layout.addWidget(empty)
            return

        for record in files:
            card = self._panel_frame()
            card.layout().setContentsMargins(14, 10, 14, 10)
            card.layout().setSpacing(8)
            title_row = QtWidgets.QHBoxLayout()
            title_row.setSpacing(8)
            title = QtWidgets.QLabel(record["name"])
            title.setStyleSheet("font-weight: 600;")
            date = QtWidgets.QLabel(record["date"])
            date.setStyleSheet("color: #5e646c; font-size: 12px;")
            size = QtWidgets.QLabel(record["size"])
            size.setStyleSheet("color: #5e646c; font-size: 12px;")
            title_row.addWidget(title)
            title_row.addStretch()
            title_row.addWidget(date)
            title_row.addWidget(size)
            card.layout().addLayout(title_row)

            buttons = QtWidgets.QHBoxLayout()
            buttons.setSpacing(10)
            open_transcript = QtWidgets.QPushButton("Open Transcript")
            open_transcript.setProperty("variant", "ghost")
            open_transcript.setProperty("size", "small")
            open_transcript.clicked.connect(
                lambda _checked=False, path=record["transcript_path"]: self._open_file(path)
            )
            buttons.addWidget(open_transcript)

            if record.get("summary_path"):
                view_summary = QtWidgets.QPushButton("View Summary")
                view_summary.setProperty("variant", "ghost")
                view_summary.setProperty("size", "small")
                view_summary.clicked.connect(
                    lambda _checked=False, path=record["summary_path"]: self._open_summary_viewer(path)
                )
                buttons.addWidget(view_summary)
            else:
                pending = QtWidgets.QPushButton("Summary Processing...")
                pending.setEnabled(False)
                pending.setProperty("variant", "ghost")
                pending.setProperty("size", "small")
                buttons.addWidget(pending)

            card.layout().addLayout(buttons)
            self.recordings_layout.addWidget(card)

    def _start_recording(self):
        meeting_name = self.meeting_input.text().strip()
        if not meeting_name:
            self._show_error("Meeting name is required.")
            return

        device_id = self.device_select.currentData()
        device_name = self.device_select.currentText()
        prompt_type = self.prompt_select.currentData()

        try:
            meeting_id = self.meeting_service.start_recording(
                meeting_name,
                audio_device_id=int(device_id),
                prompt_type=prompt_type,
                audio_device_name=device_name,
            )
        except Exception as exc:
            self._show_error(str(exc))
            return

        self.current_meeting_id = meeting_id
        self.current_meeting_name = meeting_name
        self.start_timestamp = time.time()
        self.elapsed_timer.start(1000)
        self.transcript_lines = []
        self.transcript_stream.setPlainText("")
        self._reset_summary()
        self._set_status("recording", "Recording started")
        self._set_transcript_source("Local")

        def status_callback(meeting_id, status, message):
            if status == "transcription":
                self.bus.transcription.emit(message)
            else:
                self.bus.status.emit(meeting_id, status, message)

        self.meeting_service.add_status_callback(meeting_id, status_callback)

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._check_summary_ready()

    def _stop_recording(self):
        if not self.current_meeting_id:
            return
        try:
            self.meeting_service.stop_recording(self.current_meeting_id)
            self._set_status("processing", "Processing")
        except Exception as exc:
            self._show_error(str(exc))

    def _handle_status(self, meeting_id, status, message):
        if meeting_id != self.current_meeting_id:
            return
        self._set_status(status, message)
        if status == "recording" and message:
            if "(API)" in message:
                self._set_transcript_source("Remote", True)
            elif "local" in message.lower():
                self._set_transcript_source("Local")
        if status in {"complete", "error"}:
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.current_meeting_id = None
            self.current_meeting_name = None
            self.elapsed_timer.stop()
            self.elapsed_label.setText("00:00:00")
            self._load_recordings()
            self._set_transcript_source("Unknown")

    def _set_status(self, status, message):
        self.status_pill.setText(message or status.title())
        self.status_pill.setProperty("status", status if status else "idle")
        self.status_pill.style().unpolish(self.status_pill)
        self.status_pill.style().polish(self.status_pill)
        if self.current_meeting_name:
            self.meeting_name_label.setText(self.current_meeting_name)

    def _handle_transcription(self, text):
        if not text:
            return
        self.transcript_lines.append(text)
        self.transcript_stream.appendPlainText(text)
        if self.auto_scroll:
            cursor = self.transcript_stream.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)
            self.transcript_stream.setTextCursor(cursor)

    def _toggle_auto_scroll(self):
        self.auto_scroll = not self.auto_scroll
        self.auto_scroll_button.setText(f"Auto-scroll: {'On' if self.auto_scroll else 'Off'}")

    def _copy_transcript(self):
        text = "\n".join(self.transcript_lines)
        if text:
            self._copy_text(text)

    def _copy_text(self, text):
        QtWidgets.QApplication.clipboard().setText(text)

    def _set_transcript_source(self, source, ready=None):
        self.transcript_source.setText(f"Source: {source}")
        if ready is None:
            self.transcript_source.setProperty("ready", "false")
        else:
            self.transcript_source.setProperty("ready", "true" if ready else "false")
        self.transcript_source.style().unpolish(self.transcript_source)
        self.transcript_source.style().polish(self.transcript_source)

    def _update_elapsed(self):
        if not self.start_timestamp:
            return
        elapsed = int(time.time() - self.start_timestamp)
        hours = str(elapsed // 3600).zfill(2)
        minutes = str((elapsed % 3600) // 60).zfill(2)
        seconds = str(elapsed % 60).zfill(2)
        self.elapsed_label.setText(f"{hours}:{minutes}:{seconds}")

    def _set_active_tab(self, tab):
        self.keypoints_tab.setChecked(tab == "keypoints")
        self.actions_tab.setChecked(tab == "actions")
        self.issues_tab.setChecked(tab == "issues")
        self.ask_tab.setChecked(tab == "ask")
        self.summary_stack.setCurrentWidget(self.ask_panel if tab == "ask" else self.summary_cards)

        if tab == "keypoints":
            self._generate_keypoints()
        elif tab == "actions":
            self._generate_actions()
        elif tab == "issues":
            self._generate_issues()

    def _generate_keypoints(self):
        prompt = (
            "You are a meeting assistant. Return only key points as short bullet items. "
            "Do not include headings, introductions, or extra commentary."
        )
        self._run_summary_task(prompt, self.bus.keypoints_result)

    def _generate_actions(self):
        prompt = (
            "You are a meeting assistant. Extract action items as short bullet items. "
            "Include owner names if mentioned. Do not include headings or extra commentary."
        )
        self._run_summary_task(prompt, self.bus.actions_result)

    def _generate_issues(self):
        prompt = (
            "You are a meeting assistant. Identify the last 2-3 issues discussed in the transcript. "
            "For each, propose a concise solution and provide a brief summary. "
            "Return bullet items only, no headings or extra commentary."
        )
        self._run_summary_task(prompt, self.bus.issues_result)

    def _run_summary_task(self, prompt, signal):
        transcript = "\n".join(self.transcript_lines).strip()
        if not transcript:
            self._show_error("No transcript available yet.")
            return

        def worker():
            try:
                summary = self.meeting_service.summarize_text(transcript, prompt)
                signal.emit(summary)
            except Exception as exc:
                self.bus.error.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_keypoints(self, summary):
        self._populate_list(self.keypoints_list, summary)

    def _handle_actions(self, summary):
        self._populate_list(self.actions_list, summary)

    def _handle_issues(self, summary):
        self._populate_list(self.issues_list, summary)

    def _populate_list(self, list_widget, summary):
        list_widget.clear()
        items = self._parse_bullets(summary)
        for item in items:
            list_widget.addItem(item)

    def _reset_summary(self):
        for list_widget, placeholder in (
            (self.keypoints_list, "No key points yet."),
            (self.actions_list, "No action items yet."),
            (self.issues_list, "No issues yet."),
        ):
            list_widget.clear()
            list_widget.addItem(placeholder)
        self.ask_input.setPlainText("")
        self.ask_response.setText("No answers yet.")

    def _parse_bullets(self, text):
        if not text:
            return ["No summary yet."]
        items = []
        for line in text.split("\n"):
            cleaned = line.strip().lstrip("-*•").strip()
            if cleaned:
                items.append(cleaned)
        return items if items else [text.strip()]

    def _ask_question(self):
        question = self.ask_input.toPlainText().strip()
        if not question:
            self._show_error("Enter a question to ask.")
            return
        transcript = "\n".join(self.transcript_lines).strip()
        if not transcript:
            self._show_error("No transcript available yet.")
            return
        self.ask_response.setText("Thinking...")

        def worker():
            try:
                answer = self.meeting_service.ask_question(transcript, question)
                self.bus.ask_result.emit(answer)
            except Exception as exc:
                self.bus.error.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_ask(self, answer):
        self.ask_response.setText(answer or "No response received.")

    def _ask_last_topic(self):
        lines = self.transcript_lines[-10:]
        if not lines:
            self._show_error("No transcript available yet.")
            return
        excerpt = "\n".join(lines)
        self.ask_response.setText("Thinking...")

        prompt = (
            "You are a meeting assistant. Summarize the last topic discussed in this excerpt. "
            "Respond with a short summary, 1-2 sentences."
        )

        def worker():
            try:
                summary = self.meeting_service.summarize_text(excerpt, prompt)
                self.bus.last_topic_result.emit(summary)
            except Exception as exc:
                self.bus.error.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_last_topic(self, summary):
        self.ask_response.setText(summary or "No response received.")

    def _check_summary_ready(self):
        def worker():
            try:
                self.meeting_service.check_summary_ready()
                self.bus.summary_ready.emit(True, "")
            except Exception as exc:
                self.bus.summary_ready.emit(False, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_summary_ready(self, ready, error):
        self.summary_ready_hint.setVisible(not ready)
        for tab in (self.keypoints_tab, self.actions_tab, self.issues_tab, self.ask_tab):
            tab.setEnabled(ready)
        if ready:
            self._set_active_tab("keypoints")
        elif error:
            self.summary_ready_hint.setText(error)

    def _check_whisper_ready(self):
        if self.settings.whisper_mode != "api":
            self._set_transcript_source("Local")
            self.whisper_api_status.setText("Status: local mode")
            self.whisper_api_status.setProperty("ready", "true")
            self.whisper_api_status.style().unpolish(self.whisper_api_status)
            self.whisper_api_status.style().polish(self.whisper_api_status)
            return

        def worker():
            try:
                self.meeting_service.check_whisper_api_ready()
                self.bus.whisper_ready.emit(True, "")
            except Exception as exc:
                self.bus.whisper_ready.emit(False, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_whisper_ready(self, ready, error):
        if ready:
            self._set_transcript_source("Remote", True)
            self.whisper_api_status.setText("Status: available")
            self.whisper_api_status.setProperty("ready", "true")
        else:
            self._set_transcript_source("API unavailable", False)
            self.whisper_api_status.setText("Status: unavailable")
            self.whisper_api_status.setProperty("ready", "false")
            if error:
                self.session_hint.setText(error)
        self.whisper_api_status.style().unpolish(self.whisper_api_status)
        self.whisper_api_status.style().polish(self.whisper_api_status)

    def _open_file(self, path):
        if not path:
            return
        url = QtCore.QUrl.fromLocalFile(str(Path(path)))
        QtGui.QDesktopServices.openUrl(url)

    def _open_summary_viewer(self, path):
        if not path:
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Summary")
        layout = QtWidgets.QVBoxLayout(dialog)
        text_view = QtWidgets.QTextBrowser()
        text_view.setOpenExternalLinks(True)
        try:
            content = Path(path).read_text()
        except Exception as exc:
            content = f"Failed to load summary: {exc}"
        text_view.setMarkdown(content)
        layout.addWidget(text_view)

        actions = QtWidgets.QHBoxLayout()
        copy_button = QtWidgets.QPushButton("Copy")
        copy_button.setProperty("variant", "ghost")
        copy_button.setProperty("size", "small")
        copy_button.clicked.connect(lambda: self._copy_text(text_view.toPlainText()))
        open_button = QtWidgets.QPushButton("Open")
        open_button.setProperty("variant", "ghost")
        open_button.setProperty("size", "small")
        open_button.clicked.connect(lambda: self._open_file(path))
        close_button = QtWidgets.QPushButton("Close")
        close_button.setProperty("variant", "ghost")
        close_button.setProperty("size", "small")
        close_button.clicked.connect(dialog.accept)
        actions.addWidget(copy_button)
        actions.addWidget(open_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        dialog.resize(540, 520)
        dialog.exec()

    def _show_error(self, message):
        self.session_hint.setText(message)


def main():
    app = QtWidgets.QApplication([])
    app.setStyle("Fusion")
    window = MainWindow()
    window.resize(1280, 860)
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
