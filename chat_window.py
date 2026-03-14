
import sys
import os
import datetime
import base64
import re
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QSize, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QFont, QIcon, QTextCursor, QColor, QAction, QPainter, QBrush, QLinearGradient, QPalette
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QTextEdit, QPushButton, QListWidget, QFrame, 
    QFileDialog, QMessageBox, QScrollArea, QListWidgetItem, QMenu,
    QGraphicsDropShadowEffect, QSizePolicy
)
import formatter

try:
    import speech_recognition as sr
except ImportError:
    sr = None
    print("Speech Recognition not found. Voice features disabled.")

# --- Voice Worker (unchanged from original) ---
class VoiceWorker(QThread):
    text_ready = pyqtSignal(str)
    finished = pyqtSignal()
    
    def __init__(self, recognizer):
        super().__init__()
        self.recognizer = recognizer
        self.running = False

    def run(self):
        self.running = True
        print("VoiceWorker: Starting...")
        try:
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=1.0)
                while self.running:
                    try:
                        audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)
                        text = self.recognizer.recognize_google(audio)
                        if text:
                            self.text_ready.emit(text)
                    except sr.WaitTimeoutError:
                        continue
                    except sr.UnknownValueError:
                        continue
                    except sr.RequestError as e:
                         print(f"VoiceWorker: Request Error: {e}")
                         break
        except Exception as e:
            print(f"Voice Error: {e}")
        finally:
            self.finished.emit()

    def stop(self):
        self.running = False

# --- Modern Message Bubble ---
class MessageBubble(QFrame):
    copy_requested = pyqtSignal(str)
    edit_requested = pyqtSignal(str)

    def __init__(self, text, is_user=False, timestamp=None, parent=None):
        super().__init__(parent)
        self.is_user   = is_user
        self.raw_text  = text # preserve raw for editing
        self.text      = text
        self.timestamp = timestamp or datetime.datetime.now().strftime("%H:%M")
        
        self.setup_ui()
        self.apply_styles()
        
    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(10)
        
        # AI Avatar
        if not self.is_user:
            self.avatar_label = QLabel("🤖")
            self.avatar_label.setFixedSize(32, 32)
            self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.avatar_label.setStyleSheet("""
                background-color: #334155;
                color: white;
                border-radius: 16px;
                font-size: 16px;
                margin-right: 5px;
            """)
            layout.addWidget(self.avatar_label, 0, Qt.AlignmentFlag.AlignTop)

        if self.is_user:
            layout.addStretch()
        
        self.bubble_container = QFrame()
        self.bubble_container.setObjectName("bubbleContainer")
        bubble_layout = QVBoxLayout(self.bubble_container)
        bubble_layout.setContentsMargins(16, 12, 16, 12)
        bubble_layout.setSpacing(6)
        
        # Message text
        self.msg_label = QLabel(self.text)
        self.msg_label.setWordWrap(True)
        self.msg_label.setTextFormat(Qt.TextFormat.RichText)
        self.msg_label.setOpenExternalLinks(True)
        self.msg_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.msg_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.msg_label.setMaximumWidth(760)
        
        # ── Buttons Row (Copy/Edit) ──
        self.btn_bar = QWidget()
        self.btn_bar.setFixedHeight(24)
        btn_layout = QHBoxLayout(self.btn_bar)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)
        
        self.copy_btn = QPushButton("📋 Copy")
        self.edit_btn = QPushButton("✏️ Edit")
        
        for btn in [self.copy_btn, self.edit_btn]:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent; color: #94A3B8;
                    border: none; font-size: 11px; font-weight: 600;
                    padding: 2px 6px; border-radius: 4px;
                }
                QPushButton:hover { background: rgba(255,255,255,0.1); color: white; }
            """)
        
        self.copy_btn.clicked.connect(self.on_copy)
        self.edit_btn.clicked.connect(self.on_edit)
        
        btn_layout.addWidget(self.copy_btn)
        if self.is_user:
            btn_layout.addWidget(self.edit_btn)
        btn_layout.addStretch()
        self.btn_bar.setVisible(False)
        
        # Timestamp
        time_label = QLabel(self.timestamp)
        time_label.setObjectName("timestamp")
        
        footer_layout = QHBoxLayout()
        footer_layout.addWidget(self.btn_bar)
        footer_layout.addStretch()
        footer_layout.addWidget(time_label)
        
        bubble_layout.addWidget(self.msg_label)
        bubble_layout.addLayout(footer_layout)
        
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 2)
        self.bubble_container.setGraphicsEffect(shadow)
        
        layout.addWidget(self.bubble_container)
        if not self.is_user:
            layout.addStretch()
        
        self.bubble_container.setMaximumWidth(780)
        self.bubble_container.setMinimumWidth(100)

    def enterEvent(self, event):
        self.btn_bar.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.btn_bar.setVisible(False)
        super().leaveEvent(event)

    def on_copy(self):
        # Plain text for clipboard
        clipboard = QApplication.clipboard()
        clipboard.setText(self.raw_text)
        main_win = self.window()
        if hasattr(main_win, 'show_copy_feedback'):
            main_win.show_copy_feedback()

    def on_edit(self):
        self.edit_requested.emit(self.raw_text)

    def apply_styles(self):
        if self.is_user:
            self.setStyleSheet("""
                QFrame#bubbleContainer {
                    background-color: #2563EB;
                    border-radius: 18px;
                    border-bottom-right-radius: 4px;
                }
                QLabel { color: white; font-size: 15px; background: transparent; }
                QLabel#timestamp { color: rgba(255,255,255,0.6); font-size: 11px; }
            """)
        else:
            self.setStyleSheet("""
                QFrame#bubbleContainer {
                    background-color: #1E293B;
                    border-radius: 18px;
                    border-bottom-left-radius: 4px;
                }
                QLabel { color: #E2E8F0; font-size: 15px; background: transparent; }
                QLabel#timestamp { color: #94A3B8; font-size: 11px; }
            """)

# --- Modern Chat Display with Bubbles ---
class ChatDisplay(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: #0F172A;
            }
            QScrollBar:vertical {
                border: none;
                background: #1E293B;
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #475569;
                border-radius: 5px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #64748B;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
        """)
        
        self.container = QWidget()
        self.container.setStyleSheet("background-color: #0F172A;")
        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(40, 25, 40, 10)
        self.layout.setSpacing(10)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.layout.addStretch() # Ensure messages align to top
        
        self.setWidget(self.container)
        
        # Welcome message
        self.add_welcome_message()
        
    def add_welcome_message(self):
        welcome_text = """
        <div style='text-align: center; margin: 50px 0;'>
            <h1 style='color: white; font-size: 32px; margin-bottom: 10px;'>👋 Hello! I'm Cora</h1>
            <p style='color: #94A3B8; font-size: 16px;'>Your AI assistant. How can I help you today?</p>
        </div>
        """
        self.welcome_label = QLabel(welcome_text)
        self.welcome_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(self.welcome_label)
        
    def add_message(self, text, is_user=False):
        # Remove welcome message if it exists
        if hasattr(self, 'welcome_label') and self.welcome_label.isVisible():
             self.welcome_label.setVisible(False)
        
        bubble = MessageBubble(text, is_user)
        # Connect edit signal
        if is_user:
            bubble.edit_requested.connect(self.on_bubble_edit_requested)
            
        # Insert before the stretch at index cnt-1
        self.layout.insertWidget(self.layout.count() - 1, bubble)
        
        # Scroll to bottom
        QTimer.singleShot(50, self.scroll_to_bottom)

    def on_bubble_edit_requested(self, text):
        # Find main window to handle edit
        main_win = self.window()
        if hasattr(main_win, 'on_edit_requested'):
            main_win.on_edit_requested(text)
    
    def get_last_bubble(self):
        # Return the last MessageBubble (skip the stretch at end)
        cnt = self.layout.count()
        if cnt > 1: # Layout has stretch at end
            item = self.layout.itemAt(cnt - 2)
            if item.widget() and isinstance(item.widget(), MessageBubble):
                return item.widget()
        return None

    def scroll_to_bottom(self):
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        
    def clear(self):
         # Clear all widgets except welcome or just reset
         while self.layout.count():
             item = self.layout.takeAt(0)
             if item.widget():
                 item.widget().deleteLater()
         self.add_welcome_message()

# --- Modern Input Area ---
class ModernInputArea(QFrame):
    message_sent = pyqtSignal(str, object) # text, attachment
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_attachment = None
        self.setup_ui()
        self.apply_styles()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # Attachment chip
        self.chip_container = QFrame()
        self.chip_container.setVisible(False)
        chip_layout = QHBoxLayout(self.chip_container)
        chip_layout.setContentsMargins(20, 0, 20, 0)
        
        chip_content = QFrame()
        chip_content.setObjectName("chipContent")
        chip_content.setFixedHeight(32)
        chip_content_layout = QHBoxLayout(chip_content)
        chip_content_layout.setContentsMargins(10, 0, 5, 0)
        
        self.chip_label = QLabel("")
        self.chip_label.setStyleSheet("color: #60A5FA; font-size: 13px;")
        
        close_chip = QPushButton("✕")
        close_chip.setFixedSize(20, 20)
        close_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        close_chip.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #94A3B8;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover {
                color: #EF4444;
            }
        """)
        close_chip.clicked.connect(self.remove_attachment)
        
        chip_content_layout.addWidget(self.chip_label)
        chip_content_layout.addWidget(close_chip)
        chip_layout.addWidget(chip_content)
        chip_layout.addStretch()
        
        # Input container
        input_container = QFrame()
        input_container.setObjectName("inputContainer")
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(15, 8, 15, 8)
        input_layout.setSpacing(10)
        
        # Attachment button
        self.attach_btn = QPushButton("📎")
        self.attach_btn.setFixedSize(36, 36)
        self.attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_btn.clicked.connect(self.attach_file)
        
        # Text area
        self.input_field = QTextEdit()
        self.input_field.setPlaceholderText("Message Cora AI...")
        self.input_field.setMaximumHeight(70) # Reduced from 120
        self.input_field.setMinimumHeight(45) # Adjusted from 50
        self.input_field.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.input_field.installEventFilter(self) # Install filter to catch Enter
        
        # Voice button
        self.voice_btn = QPushButton("🎤")
        self.voice_btn.setFixedSize(36, 36)
        self.voice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.voice_btn.clicked.connect(self.toggle_voice)
        
        # Send button
        self.send_btn = QPushButton("➤")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setFixedSize(40, 40) # Circular 40x40
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self.send_message)
        
        input_layout.addWidget(self.attach_btn)
        input_layout.addWidget(self.input_field)
        input_layout.addWidget(self.voice_btn)
        input_layout.addWidget(self.send_btn)
        
        layout.addWidget(self.chip_container)
        layout.addWidget(input_container)
        
        # Apply shadow to input container
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 50))
        shadow.setOffset(0, 2)
        input_container.setGraphicsEffect(shadow)
        
    def apply_styles(self):
        self.setStyleSheet("""
            ModernInputArea {
                background: transparent;
            }
            QFrame#inputContainer {
                background-color: #1E293B;
                border-radius: 24px;
                border: 1px solid #334155;
                margin: 0px 40px 15px 40px;
            }
            QFrame#chipContent {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 16px;
            }
            QTextEdit {
                background: transparent;
                border: none;
                color: #E2E8F0;
                font-size: 15px;
                selection-background-color: #2563EB;
            }
            QTextEdit:focus {
                outline: none;
            }
            QPushButton {
                background: transparent;
                border: none;
                color: #94A3B8;
                font-size: 18px;
                border-radius: 18px;
            }
            QPushButton:hover {
                background-color: #334155;
                color: white;
            }
            QPushButton#sendBtn {
                background-color: #2563EB;
                color: white;
                font-size: 18px;
                font-weight: bold;
                border-radius: 20px;
            }
            QPushButton#sendBtn:hover {
                background-color: #1D4ED8;
            }
            QPushButton#sendBtn:disabled {
                background-color: #475569;
                color: #94A3B8;
            }
        """)
        
    def attach_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Attach File")
        if path:
            self.current_attachment = path
            self.chip_label.setText(f"📎 {os.path.basename(path)}")
            self.chip_container.setVisible(True)
            
    def remove_attachment(self):
        self.current_attachment = None
        self.chip_container.setVisible(False)
        
    def toggle_voice(self):
        pass # Signal handled by main wrapper
        
    def send_message(self):
        # Check if button is in STOP mode (text is square)
        if self.send_btn.text() == "⏹":
             self.message_sent.emit("", None) # Emit empty to trigger stop logic in handle_send
             return

        text = self.input_field.toPlainText().strip()
        attachment = self.current_attachment
        if text or attachment:
            self.message_sent.emit(text, attachment)
            self.input_field.clear()
            self.remove_attachment()
        
    def eventFilter(self, obj, event):
        if obj == self.input_field and event.type() == event.Type.KeyPress:
             if event.key() == Qt.Key.Key_Return and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                 self.send_message()
                 return True
        return super().eventFilter(obj, event)

# Sidebar Removed


# --- Main Window (This is the class that replaces the old ChatWindow) ---
# NOTE: Renamed to ChatWindow to match main.py expectation
class ChatWindow(QMainWindow):
    # Signals matching the old ChatWindow for compatibility with main.py
    send_message_signal = pyqtSignal(str, object)
    stop_signal = pyqtSignal()
    ai_response_signal = pyqtSignal(str)
    stream_token_signal = pyqtSignal(str)
    stream_finished_signal = pyqtSignal()
    new_chat_signal = pyqtSignal()
    switch_chat_signal = pyqtSignal(str)
    delete_session_signal = pyqtSignal(str)
    closed_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cora AI")
        self.setMinimumSize(1000, 750)
        
        self.recognizer = sr.Recognizer() if sr else None
        self.voice_thread = None
        self.is_generating = False
        
        self.init_ui()
        self.apply_styles()
        
        # Streaming state
        self._streaming_label     = None
        self._streaming_text      = ""
        self._streaming_container = None

        # Message history tracking for edit/regenerate
        self._message_widgets = []  # list of {role, text, widget}
        self._last_user_message = ""

        # Connect internal signals
        self.stream_token_signal.connect(self.append_stream_chunk)
        self.stream_finished_signal.connect(self.on_stream_done)
        
        self._welcome_widget = None
    def _make_welcome_widget(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 40, 20, 40)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        emoji = QLabel("👋")
        emoji.setStyleSheet("font-size: 36px; background: transparent;")
        emoji.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Hello! I'm Cora")
        title.setStyleSheet("""
            color: #f1f5f9;
            font-size: 20px;
            font-weight: bold;
            background: transparent;
        """)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("Your AI assistant. How can I help you today?")
        sub.setStyleSheet("color: #64748b; font-size: 13px; background: transparent;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(emoji)
        layout.addWidget(title)
        layout.addWidget(sub)
        return w

    def _hide_welcome(self):
        """Hide welcome screen when first message is added."""
        if self._welcome_widget and self._welcome_widget.isVisible():
            self._welcome_widget.hide()
            self._welcome_widget = None
        # Also hide ChatDisplay's built-in welcome
        if hasattr(self, 'chat_display') and hasattr(self.chat_display, 'welcome_label'):
            if self.chat_display.welcome_label and self.chat_display.welcome_label.isVisible():
                self.chat_display.welcome_label.setVisible(False)

    def closeEvent(self, event):
        self.closed_signal.emit()
        event.accept()
        
    def show(self):
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        super().show()
        self.raise_()
        self.activateWindow()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Sidebar Removed

        
        # Main content
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        
        # Header Area for Mode Indicator
        header = QFrame()
        header.setFixedHeight(50)
        header.setStyleSheet("background-color: #1E293B; border-bottom: 1px solid #334155;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)
        
        self.mode_label = QLabel("Cora AI Assistant")
        self.mode_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        
        self.copy_feedback = QLabel("Copied!")
        self.copy_feedback.setStyleSheet("color: #10B981; font-weight: bold; background: #064E3B; border-radius: 4px; padding: 2px 8px;")
        self.copy_feedback.setVisible(False)
        
        header_layout.addWidget(self.mode_label)
        header_layout.addStretch()
        header_layout.addWidget(self.copy_feedback)
        
        # Chat display
        self.chat_display = ChatDisplay()
        self.chat_layout  = self.chat_display.layout
        
        # Input area
        self.input_area = ModernInputArea()
        self.input_area.message_sent.connect(self.handle_send)
        self.input_area.voice_btn.clicked.connect(self.toggle_voice)
        
        content_layout.addWidget(header)
        content_layout.addWidget(self.chat_display, 1) # Added stretch
        content_layout.addWidget(self.input_area, 0)   # Fixed height
        
        # Add to main layout
        # Add to main layout
        # main_layout.addWidget(self.sidebar) 
        main_layout.addWidget(content_widget, 1)
        
    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0F172A;
            }
            QWidget {
                font-family: 'Segoe UI', 'Arial', sans-serif;
            }
        """)
        
    def handle_send(self, text, attachment=None):
        # Trigger Stop if generating (InputArea sends empty text if stop clicked)
        if self.is_generating:
             print("ChatWindow: Stop Signal Emitted")
             self.stop_signal.emit()
             # Reset UI State manually if backend doesn't acknowledge quickly?
             # No, finish_response handles that.
             return

        # Regular Message
        # Only proceed if text/attachment exists (prevent empty bubbles)
        if text or attachment:
             if text:
                 # self.chat_display.add_message(text, is_user=True)
                 self.add_user_message(text)
                 
             if attachment:
                 # self.chat_display.add_message(f"📎 Attached: {os.path.basename(attachment)}", is_user=True)
                 self.add_user_message(f"📎 Attached: {os.path.basename(attachment)}")
                 
             # Emit signal to main.py
             self.set_generating_state(True)
             self.send_message_signal.emit(text, attachment)

    def add_user_message(self, text: str):
        self._hide_welcome()
        self._last_user_message = text
        widget = self._make_message_widget(text, is_user=True)
        self.chat_layout.addWidget(widget)
        if hasattr(self, 'scroll_area'):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(50, lambda:
                self.scroll_area.verticalScrollBar().setValue(
                    self.scroll_area.verticalScrollBar().maximum()
                )
            )

    def _make_message_widget(self, text: str, is_user: bool) -> QWidget:
        outer = QWidget()
        outer.setStyleSheet("background: transparent;")
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 2, 0, 2)
        outer_layout.setSpacing(2)

        # Bubble
        # We use a QLabel but with style similar to MessageBubble
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        if is_user:
            bubble.setStyleSheet("""
                QLabel {
                    background: #2563EB;
                    color: white;
                    border-radius: 12px;
                    padding: 10px 14px;
                    font-size: 13px;
                }
            """)
            bubble.setAlignment(Qt.AlignmentFlag.AlignRight)
        else:
            bubble.setStyleSheet("""
                QLabel {
                    background: rgba(255,255,255,0.08);
                    color: #e2e8f0;
                    border-radius: 12px;
                    padding: 10px 14px;
                    font-size: 13px;
                }
            """)
        outer_layout.addWidget(bubble)

        # Action buttons row
        btn_row        = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        btn_layout     = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(4, 0, 4, 0)
        btn_layout.setSpacing(4)

        btn_style = """
            QPushButton {
                background: transparent;
                border: 1px solid #334155;
                color: #64748b;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
            }
            QPushButton:hover {
                border-color: #3b82f6;
                color: #3b82f6;
            }
        """

        # Copy button — always present
        copy_btn = QPushButton("⎘ Copy")
        copy_btn.setFixedHeight(22)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(btn_style)
        copy_btn.clicked.connect(lambda: self._copy_text(text, copy_btn))

        if is_user:
            btn_layout.addStretch()
            btn_layout.addWidget(copy_btn)

            # Edit button
            edit_btn = QPushButton("✎ Edit")
            edit_btn.setFixedHeight(22)
            edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_btn.setStyleSheet(btn_style)
            edit_btn.clicked.connect(lambda: self._edit_message(text, outer))
            btn_layout.addWidget(edit_btn)
        else:
            btn_layout.addStretch()
            btn_layout.addWidget(copy_btn)

            # Regenerate button — only on last AI message
            regen_btn = QPushButton("↺ Regenerate")
            regen_btn.setFixedHeight(22)
            regen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            regen_btn.setStyleSheet(btn_style)
            regen_btn.clicked.connect(lambda: self._regenerate(outer))
            btn_layout.addWidget(regen_btn)
            # Store ref so we can hide regen on older messages
            outer._regen_btn = regen_btn

        outer_layout.addWidget(btn_row)

        # Track this widget
        role = 'user' if is_user else 'assistant'
        self._message_widgets.append({
            'role':   role,
            'text':   text,
            'widget': outer,
        })

        return outer

    def _copy_text(self, text: str, btn: QPushButton):
        try:
            import pyperclip
            pyperclip.copy(text)
        except Exception:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)

        original = btn.text()
        btn.setText("✓ Copied!")
        btn.setStyleSheet(btn.styleSheet().replace('#64748b', '#22c55e'))
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: btn.setText(original))

    def _edit_message(self, text: str, widget: QWidget):
        """Put message in input, remove it and all after it."""
        idx = None
        for i, m in enumerate(self._message_widgets):
            if m['widget'] is widget:
                idx = i
                break
        if idx is None: return

        to_remove = self._message_widgets[idx:]
        self._message_widgets = self._message_widgets[:idx]

        for m in to_remove:
            w = m['widget']
            self.chat_layout.removeWidget(w)
            w.deleteLater()

        # Put text back in input field
        if hasattr(self, 'input_area') and hasattr(self.input_area, 'input_field'):
            self.input_area.input_field.setText(text)
            self.input_area.input_field.setFocus()

    def _regenerate(self, widget: QWidget):
        """Remove last AI response and resend last user message."""
        idx = None
        for i, m in enumerate(self._message_widgets):
            if m['widget'] is widget:
                idx = i
                break
        if idx is None: return

        last_user_text = None
        for m in reversed(self._message_widgets[:idx]):
            if m['role'] == 'user':
                last_user_text = m['text']
                break
        if not last_user_text: return

        to_remove = self._message_widgets[idx:]
        self._message_widgets = self._message_widgets[:idx]

        for m in to_remove:
            w = m['widget']
            self.chat_layout.removeWidget(w)
            w.deleteLater()

        print(f'[REGEN] Re-sending: {last_user_text[:60]}')
        self.set_generating_state(True)
        self.send_message_signal.emit(last_user_text, None)
        
    def set_generating_state(self, is_generating):
        self.is_generating = is_generating
        
        # Update Input UI based on vision keywords presence
        text = self.input_area.input_field.toPlainText().lower()
        vision_keywords = ["look", "see", "screen", "visual", "watch", "what is this", "screenshot", "observe", "check", "debug", "fix"]
        is_vision = any(k in text for k in vision_keywords)
        
        if is_generating:
            btn_text = "⏹"
            status_style = "border: 1px solid #EF4444;" if is_vision else "border: 1px solid #2563EB;"
            self.input_area.send_btn.setText(btn_text)
            self.input_area.send_btn.setStyleSheet(f"""
                QPushButton#sendBtn {{
                    background-color: #EF4444;
                    color: white;
                    font-size: 18px;
                    font-weight: bold;
                    border-radius: 20px;
                }}
                QPushButton#sendBtn:hover {{
                    background-color: #DC2626;
                }}
            """)
        else:
            self.input_area.send_btn.setText("➤")
            self.input_area.send_btn.setStyleSheet("""
                QPushButton#sendBtn {
                    background-color: #2563EB;
                    color: white;
                    font-size: 16px;
                    font-weight: bold;
                    border-radius: 20px;
                }
                QPushButton#sendBtn:hover {
                    background-color: #1D4ED8;
                }
            """)
        QApplication.processEvents()
            
    def show_copy_feedback(self):
        self.copy_feedback.setVisible(True)
        QTimer.singleShot(2000, lambda: self.copy_feedback.setVisible(False))
        
    def update_mode_indicator(self, mode: str, reason: str = ""):
        if reason:
            display = reason if len(reason) <= 72 else reason[:69] + "…"
            self.mode_label.setText(display)
            return
        ICONS = {
            "developer": "💻  Developer",
            "writing": "✍️  Writing",
            "reading": "📖  Reading",
            "pdf": "📄  PDF",
            "spreadsheet": "📊  Spreadsheet",
            "browser": "🌐  Browser",
            "youtube": "▶️  YouTube",
            "general": "🤖  General",
        }
        self.mode_label.setText(ICONS.get(mode, f"🤖  {mode.capitalize()}"))

    def append_stream_chunk(self, text: str):
        """Append streaming text to current AI response bubble."""
        self._hide_welcome()
        if self._streaming_label is None:
            # Create new response bubble for this stream
            self._streaming_text  = ""
            self._streaming_label = QLabel()
            self._streaming_label.setWordWrap(True)
            self._streaming_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self._streaming_label.setStyleSheet("""
                QLabel {
                    background: rgba(255,255,255,0.08);
                    color: #e2e8f0;
                    border-radius: 12px;
                    padding: 10px 14px;
                    font-size: 13px;
                }
            """)
            # Add to chat layout
            # Note: We append to self.chat_display.layout
            container = QWidget()
            layout    = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 4)
            layout.addWidget(self._streaming_label)
            
            # Using the chat_display's internal layout
            d_layout = self.chat_display.layout
            d_layout.insertWidget(d_layout.count() - 1, container)
            self._streaming_container = container

        self._streaming_text += text
        self._streaming_label.setText(self._streaming_text)

        # Auto scroll to bottom
        self.chat_display.scroll_to_bottom()

    def on_stream_done(self):
        if self._streaming_text:
            final_text = self._streaming_text

            if self._streaming_container:
                d_layout = self.chat_display.layout
                d_layout.removeWidget(self._streaming_container)
                self._streaming_container.deleteLater()

            msg_widget = self._make_message_widget(final_text, is_user=False)

            # If response has code — add dedicated Copy Code button
            if '```' in final_text:
                import re
                code_blocks = re.findall(r'```\w*\n?(.*?)```', final_text, re.DOTALL)
                if code_blocks:
                    all_code = '\n\n'.join(code_blocks).strip()
                    code_copy_btn = QPushButton("📋 Copy Code")
                    code_copy_btn.setFixedHeight(26)
                    code_copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    code_copy_btn.setStyleSheet("""
                        QPushButton {
                            background: rgba(59,130,246,0.15);
                            border: 1px solid #3b82f6;
                            color: #3b82f6;
                            border-radius: 6px;
                            padding: 3px 12px;
                            font-size: 11px;
                            font-weight: bold;
                        }
                        QPushButton:hover {
                            background: rgba(59,130,246,0.3);
                            color: white;
                        }
                    """)
                    code_copy_btn.clicked.connect(
                        lambda: self._copy_text(all_code, code_copy_btn)
                    )
                    code_row        = QWidget()
                    code_row_layout = QHBoxLayout(code_row)
                    code_row_layout.setContentsMargins(4, 0, 4, 4)
                    code_row_layout.addStretch()
                    code_row_layout.addWidget(code_copy_btn)
                    msg_widget.layout().addWidget(code_row)

            # Hide regen on previous AI messages
            for m in self._message_widgets[:-1]:
                if m['role'] == 'assistant' and hasattr(m['widget'], '_regen_btn'):
                    m['widget']._regen_btn.hide()

            d_layout = self.chat_display.layout
            d_layout.insertWidget(d_layout.count() - 1, msg_widget)

        self._streaming_label     = None
        self._streaming_text      = ""
        self._streaming_container = None
        self.set_generating_state(False)

        if hasattr(self, 'chat_display'):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(100, lambda:
                self.chat_display.verticalScrollBar().setValue(
                    self.chat_display.verticalScrollBar().maximum()
                )
            )

    def _render_markdown(self, text: str) -> str:
        import re
        html = text

        # Code blocks with copy button
        def replace_code_block(m):
            lang = m.group(1) or 'code'
            code = m.group(2).strip()
            escaped = (code
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))
            return (
                f'<div style="margin:8px 0;">'
                f'<div style="background:#0d1117;border:1px solid #30363d;'
                f'border-radius:8px;padding:12px;'
                f'font-family:Consolas,Monaco,monospace;font-size:12px;'
                f'color:#e6edf3;line-height:1.5;white-space:pre-wrap;">'
                f'{escaped}'
                f'</div></div>'
            )
        html = re.sub(
            r'```(\w*)\n?(.*?)```',
            replace_code_block,
            html,
            flags=re.DOTALL
        )

        # Inline code
        html = re.sub(
            r'`([^`\n]+)`',
            r'<code style="background:#0d1117;color:#79c0ff;'
            r'padding:1px 5px;border-radius:3px;'
            r'font-family:Consolas,monospace;font-size:12px;">\1</code>',
            html
        )

        # Bold
        html = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html)

        # Italic
        html = re.sub(r'\*(.+?)\*', r'<i>\1</i>', html)

        # Headers
        html = re.sub(r'^### (.+)$',
            r'<div style="color:#60a5fa;font-weight:bold;'
            r'margin:6px 0 2px 0;">\1</div>',
            html, flags=re.MULTILINE)
        html = re.sub(r'^## (.+)$',
            r'<div style="color:#60a5fa;font-size:14px;font-weight:bold;'
            r'margin:8px 0 2px 0;">\1</div>',
            html, flags=re.MULTILINE)

        # Bullets
        html = re.sub(
            r'^\s*[-•*]\s+(.+)$',
            r'<div style="padding-left:14px;margin:2px 0;">• \1</div>',
            html, flags=re.MULTILINE
        )

        # Numbered list
        html = re.sub(
            r'^\s*(\d+)\.\s+(.+)$',
            r'<div style="padding-left:14px;margin:2px 0;">\1. \2</div>',
            html, flags=re.MULTILINE
        )

        # Newlines
        html = html.replace('\n', '<br>')

        return (
            f'<div style="color:#e2e8f0;font-size:13px;'
            f'line-height:1.6;">{html}</div>'
        )

    def _copy_to_clipboard(self, text, btn):
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        btn.setText("✓ Copied")
        QTimer.singleShot(2000, lambda: btn.setText("⎘ Copy"))

    # --- Integration Methods for Main.py ---
    
    def start_new_chat(self):
        # We just emit the signal. Main.py will call methods to clear UI via handle_new_chat logic
        # OR main.py expects this method to signal AND clear. 
        # Based on previous fixes, main.py clears UI. But let's be safe.
        self.chat_display.clear()
        self.new_chat_signal.emit()
        
    def switch_chat(self, session_id):
        self.switch_chat_signal.emit(session_id)
        
    def delete_chat(self, session_id):
        self.delete_session_signal.emit(session_id)
        
    def on_edit_requested(self, text):
        from PyQt6.QtGui import QTextCursor
        self.input_area.input_field.setPlainText(text)
        self.input_area.input_field.setFocus()
        self.input_area.input_field.moveCursor(QTextCursor.MoveOperation.End)
        
    def toggle_voice(self):
        if not self.recognizer:
            QMessageBox.warning(self, "Voice Error", "Speech Recognition module not installed.")
            return
            
        if self.voice_thread and self.voice_thread.isRunning():
            self.voice_thread.stop()
            self.input_area.voice_btn.setStyleSheet("color: #94A3B8; border: none; background: transparent; font-size: 18px;")
        else:
            self.input_area.voice_btn.setStyleSheet("color: #EF4444; border: none; background: transparent; font-size: 18px; font-weight: bold;")
            self.voice_thread = VoiceWorker(self.recognizer)
            self.voice_thread.text_ready.connect(lambda t: self.input_area.input_field.insertPlainText(t + " "))
            self.voice_thread.finished.connect(self.on_voice_finished)
            self.voice_thread.start()
            
    def on_voice_finished(self):
        self.input_area.voice_btn.setStyleSheet("color: #94A3B8; border: none; background: transparent; font-size: 18px;")
        
    def load_sessions(self, sessions):
        pass # Sidebar removed

            
    def append_message(self, role, text, is_user=False):
        # This is called by main.py when loading history
        self.chat_display.add_message(text, is_user=is_user)

    def set_context(self, ctx):
        """Store context for next generation."""
        self._active_ctx = ctx
        self.update_mode_indicator(ctx.app)

    def get_history(self) -> list:
        """Return conversation history from tracked message widgets."""
        history = []
        for m in self._message_widgets:
            role = 'user' if m['role'] == 'user' else 'assistant'
            # Strip markdown for history
            text = m['text']
            if len(text) > 500:
                text = text[:500] + '...'
            history.append({'role': role, 'content': text})
        return history[-12:]  # last 6 turns
        
    # Helper to clean/prep markdown text if needed
    def clean_text(self, text):
        return text

    def add_user(self, text):
        self.chat_display.add_message(text, is_user=True)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    
    window = ChatWindow()
    window.show()
    
    sys.exit(app.exec())
