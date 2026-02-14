import sys
import threading
import os
import shutil

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import QFont, QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton, QListWidget, QFrame,
    QFileDialog, QMenu, QMessageBox
)

try:
    import speech_recognition as sr
except ImportError:
    sr = None

# -----------------------------
# Voice Worker (QThread)
# -----------------------------
class VoiceWorker(QThread):
    text_ready = pyqtSignal(str)
    finished = pyqtSignal()
    
    def __init__(self, recognizer):
        super().__init__()
        self.recognizer = recognizer
        self.running = False

    def run(self):
        self.running = True
        print("VoiceWorker: Started.")
        try:
            with sr.Microphone() as source:
                print("VoiceWorker: Adjusting ambient noise...")
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                
                while self.running:
                    print("VoiceWorker: Listening Loop...")
                    try:
                        # Listen for short phrases
                        audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)
                        print("VoiceWorker: Capturing...")
                        text = self.recognizer.recognize_google(audio)
                        print(f"VoiceWorker Result: {text}")
                        if text:
                            self.text_ready.emit(text)
                    except sr.WaitTimeoutError:
                        continue # Loop again
                    except sr.UnknownValueError:
                        print("VoiceWorker: Unintelligible")
                        continue
                    except Exception as e:
                        print(f"VoiceWorker Error: {e}")
                        break
        except Exception as e:
            print(f"VoiceWorker Init Error: {e}")
        finally:
            print("VoiceWorker: Finished.")
            self.finished.emit()

    def stop(self):
        self.running = False

# -----------------------------
# Custom Input Field (Enter to Send)
# -----------------------------
class ChatInput(QTextEdit):
    return_pressed = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.return_pressed.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

# -----------------------------
# Main UI
# -----------------------------
class ChatWindow(QMainWindow):
    # ... (Signals omitted for brevity, they remain same) ...
    send_message_signal = pyqtSignal(str, object) 
    ai_response_signal = pyqtSignal(str) 
    stream_token_signal = pyqtSignal(str) 
    stream_finished_signal = pyqtSignal() 
    stop_signal = pyqtSignal() 

    def __init__(self):
        super().__init__()
        # ... (init code remains same) ...
        self.setWindowTitle("CORA · AI Assistant")
        self.resize(1000, 600)
        self.setWindowIcon(QIcon("icons/chatbot_icon.png"))

        # State
        self.chat_history = {}
        self.current_chat = "New Chat"
        self.last_prompt = ""
        self.current_attachment = None
        self.recognizer = sr.Recognizer() if sr else None
        
        self.voice_thread = None

        self.apply_theme()
        self.init_ui()

        # Connect Signals
        self.ai_response_signal.connect(self.on_ai_response_start)
        self.stream_token_signal.connect(self.stream_response)
        self.stream_finished_signal.connect(self.finish_response)

    # ... (apply_theme remains same) ...

    def init_ui(self):
        # ... (Layout setup remains same until input_field) ...
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(240)
        self.sidebar.addItem(self.current_chat)
        self.sidebar.setCurrentRow(0)
        
        sidebar_container = QWidget()
        sidebar_layout = QVBoxLayout(sidebar_container)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.addWidget(self.sidebar)
        main_layout.addWidget(sidebar_container)

        # Chat Area
        chat_container = QWidget()
        chat_layout = QVBoxLayout(chat_container)
        chat_layout.setContentsMargins(20, 20, 20, 20)
        chat_layout.setSpacing(16)

        header = QLabel("Cora Assistant")
        header.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff;")
        chat_layout.addWidget(header)

        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setStyleSheet("border: none; background: transparent; font-size: 15px;")
        chat_layout.addWidget(self.chat_display)

        # Input Wrapper
        input_area_wrapper = QWidget()
        input_area_layout = QVBoxLayout(input_area_wrapper)
        input_area_layout.setContentsMargins(0, 0, 0, 0)
        input_area_layout.setSpacing(4)

        # Chip
        self.chip_container = QWidget()
        self.chip_container.setVisible(False) 
        self.chip_container.setStyleSheet("""
            QWidget {
                background-color: #1f2937;
                border-radius: 12px;
                border: 1px solid #374151;
            }
        """)
        chip_layout = QHBoxLayout(self.chip_container)
        chip_layout.setContentsMargins(12, 4, 12, 4)
        
        self.chip_label = QLabel("File.pdf")
        self.chip_label.setStyleSheet("color: #e5e7eb; font-size: 12px; border: none; background: transparent;")
        
        close_chip = QPushButton("✕")
        close_chip.setFixedSize(20, 20)
        close_chip.setStyleSheet("""
            QPushButton {
                background: transparent; border: none; color: #9ca3af; font-weight: bold;
            }
            QPushButton:hover { color: #ef4444; background: transparent; }
        """)
        close_chip.clicked.connect(self.remove_attachment)

        chip_layout.addWidget(QLabel("📎")) 
        chip_layout.addWidget(self.chip_label)
        chip_layout.addStretch()
        chip_layout.addWidget(close_chip)
        
        chip_wrapper = QHBoxLayout()
        chip_wrapper.setContentsMargins(10, 0, 0, 0)
        chip_wrapper.addWidget(self.chip_container)
        chip_wrapper.addStretch()
        
        input_area_layout.addLayout(chip_wrapper)

        # Input Frame (Pill)
        input_frame = QFrame()
        input_frame.setFixedHeight(54) 
        input_frame.setStyleSheet("""
            QFrame {
                background-color: #374151;
                border-radius: 27px;
                border: 1px solid #4b5563;
            }
        """)
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(10, 5, 10, 5) 
        input_layout.setSpacing(10)

        # Attach
        self.attach_btn = QPushButton("+")
        self.attach_btn.setFixedSize(36, 36)
        self.attach_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 2px solid #9ca3af;
                border-radius: 18px;
                color: #9ca3af;
                font-size: 20px;
                font-weight: bold;
                padding-bottom: 2px;
            }
            QPushButton:hover { color: #fff; border-color: #fff; background: rgba(255,255,255,0.1); }
        """)
        self.attach_btn.clicked.connect(self.attach_file)

        # Input Field (CUSTOM CLASS)
        self.input_field = ChatInput() # Using ChatInput
        self.input_field.setPlaceholderText("Ask anything...")
        self.input_field.setFixedHeight(34) 
        self.input_field.setStyleSheet("""
            QTextEdit {
                background: transparent;
                border: none;
                padding-top: 6px;
                color: white;
                font-size: 15px;
            }
        """)
        self.input_field.return_pressed.connect(self.send_message) # Connect Enter Key

        # Mic
        self.mic_btn = QPushButton("")
        self.mic_btn.setFixedSize(36, 36)
        self.mic_btn.setIcon(QIcon("icons/mic.png"))
        self.mic_btn.setIconSize(self.mic_btn.size() * 0.6)
        self.mic_btn.setStyleSheet(self.icon_btn_style())
        self.mic_btn.clicked.connect(self.toggle_voice)

        # Send
        self.send_btn = QPushButton("➤") 
        self.send_btn.setFixedSize(40, 40)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6; 
                color: white;
                border-radius: 20px;
                border: none;
                font-size: 16px;
                padding-left: 2px; 
            }
            QPushButton:hover { background-color: #2563eb; }
        """)
        self.send_btn.clicked.connect(self.send_message)

        # Stop
        self.stop_btn = QPushButton("⏹")
        self.stop_btn.setFixedSize(40, 40)
        self.stop_btn.setVisible(False)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #ef4444;
                color: white;
                border-radius: 20px;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #dc2626; }
        """)
        self.stop_btn.clicked.connect(self.stop_generation)

        input_layout.addWidget(self.attach_btn)
        input_layout.addWidget(self.input_field, 1) 
        input_layout.addWidget(self.mic_btn) 
        input_layout.addWidget(self.send_btn)
        input_layout.addWidget(self.stop_btn)
        
        input_area_layout.addWidget(input_frame)

        chat_layout.addWidget(input_area_wrapper)
        main_layout.addWidget(chat_container, 1)

    # ---------------- Styles ----------------
    def icon_btn_style(self):
        return """
        QPushButton {
            background: transparent;
            border: none;
            color: #9ca3af;
        }
        QPushButton:hover { color: #fff; background: rgba(255,255,255,0.1); border-radius: 18px; }
        """

    def primary_btn_style(self):
        # Unused in new design but kept for ref or safe removal
        return ""

    def danger_btn_style(self):
        return ""
        
    def recording_style(self):
        return """
        QPushButton {
            background-color: rgba(220, 38, 38, 0.2);
            border-radius: 18px;
            border: 1px solid #dc2626;
        }
        QPushButton:hover { background-color: rgba(220, 38, 38, 0.4); }
        """

    # ---------------- Interactions ----------------

    def send_message(self):
        prompt = self.input_field.toPlainText().strip()
        attachment = self.current_attachment
        
        if not prompt and not attachment:
            return

        # Auto-stop listening when sending
        if self.voice_thread and self.voice_thread.isRunning():
            self.stop_listening()

        if not prompt and attachment:
             prompt = f"Analyze this file: {os.path.basename(attachment)}"

        self.last_prompt = prompt
        
        self.append_chat_message("You", prompt, is_user=True)
        if attachment:
             self.chat_display.append(f"<i style='color:#9ca3af'>[Attached: {os.path.basename(attachment)}]</i>")

        self.input_field.clear()
        self.remove_attachment() # Clear UI and state
        
        self.send_btn.setVisible(False)
        self.stop_btn.setVisible(True)

        self.send_message_signal.emit(prompt, attachment)

    def stop_generation(self):
        self.stop_signal.emit()
        self.finish_response()

    # ---------------- AI Integration Slots ----------------

    def on_ai_response_start(self, text):
        self.chat_display.append(f"<br><b><span style='color:#60a5fa'>Cora</span></b><br>")
        if text:
            self.stream_response(text)

    def stream_response(self, text):
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.chat_display.setTextCursor(cursor)
        sb = self.chat_display.verticalScrollBar()
        sb.setValue(sb.maximum())

    def finish_response(self):
        self.send_btn.setVisible(True)
        self.stop_btn.setVisible(False)
        self.chat_display.append("<br>")
        self.add_regenerate_button()

    def append_chat_message(self, sender, text, is_user=False):
        color = "#e5e7eb" if not is_user else "#93c5fd" 
        html = f"<div style='margin-top: 10px;'><b><span style='color:{color}'>{sender}</span></b><br>{text}</div>"
        self.chat_display.append(html)

    # ---------------- Regenerate (Stub) ----------------
    def add_regenerate_button(self):
        # We can add a clickable link or small button in the text flow?
        # Or just rely on the user re-typing/sidebar usage.
        # For simplicity with main.py, we avoid complex regeneration logic right now.
        pass

    # ---------------- Attach File ----------------
    def attach_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Attach File")
        if file_path:
            self.set_attachment(file_path)

    def set_attachment(self, path):
        self.current_attachment = path
        filename = os.path.basename(path)
        self.chip_label.setText(filename)
        self.chip_container.setVisible(True)
        self.input_field.setFocus()

    def remove_attachment(self):
        self.current_attachment = None
        self.chip_container.setVisible(False)

    # ---------------- Voice Input ----------------
    def toggle_voice(self):
        if not self.recognizer:
            QMessageBox.information(self, "Voice Error", "Speech Recognition not installed.")
            return

        if self.voice_thread and self.voice_thread.isRunning():
            self.stop_listening()
        else:
            self.start_listening()

    def start_listening(self):
        self.mic_btn.setStyleSheet(self.recording_style())
        self.voice_thread = VoiceWorker(self.recognizer)
        self.voice_thread.text_ready.connect(self.append_voice_text)
        self.voice_thread.finished.connect(self.on_voice_finished)
        self.voice_thread.start()

    def stop_listening(self):
        if self.voice_thread:
            self.voice_thread.stop()
            # Thread will finish and trigger on_voice_finished
    
    def on_voice_finished(self):
        self.mic_btn.setStyleSheet(self.icon_btn_style())
        self.input_field.setFocus()

    def append_voice_text(self, text):
        # "Live" update - append to existing text
        current = self.input_field.toPlainText()
        if current:
            new_text = current + " " + text
        else:
            new_text = text
        self.input_field.setText(new_text)
        
        # Move cursor to end
        cursor = self.input_field.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input_field.setTextCursor(cursor)

    # ---------------- Compatibility ----------------
    def add_user(self, text, attachment=None):
        self.append_chat_message("You", text, is_user=True)
        if attachment:
            self.chat_display.append(f"<i style='color:#9ca3af'>[Attached: {os.path.basename(attachment)}]</i>")

    def add_ai(self, text):
        # Non-streaming add
        self.on_ai_response_start("")
        self.stream_response(text)
        self.finish_response()

    # ---------------- Chat History Stub ----------------
    def load_chat(self):
        # Placeholder for future history loading
        pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = ChatWindow()
    win.show()
    sys.exit(app.exec())

