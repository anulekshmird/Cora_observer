import sys
import threading
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
import observer
import ui_overlay
import chat_window

# Try importing keyboard, fallback if missing
try:
    import keyboard
except ImportError:
    print("Keyboard library not found. Hotkeys disabled.")
    keyboard = None

class ShortcutListener(QObject):
    activated = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        
    def start(self):
        if keyboard:
            try:
                # Register Ctrl+Shift+Q to toggle chat
                keyboard.add_hotkey('ctrl+shift+q', self.on_hotkey)
                print("Global Shortcut 'Ctrl+Shift+Q' registered.")
            except Exception as e:
                print(f"Failed to register hotkey: {e}")
                
    def on_hotkey(self):
        self.activated.emit()

class CoraApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        
        # Load Icon (Support both png formats just in case)
        self.icon = QIcon("icon.png")
        
        # UI Bubble (Proactive)
        self.bubble = ui_overlay.ProactiveBubble()
        self.bubble.ask_cora_clicked.connect(self.handle_overlay_action)
        
        # UI Chat Window (Reactive)
        self.chat_win = chat_window.ChatWindow()
        self.chat_win.send_message_signal.connect(self.handle_chat_message)
        self.chat_win.stop_signal.connect(self.handle_stop) # Connect Stop
        self.chat_win.setWindowIcon(self.icon)
        
        # Shortcut Handler
        self.shortcut = ShortcutListener()
        self.shortcut.activated.connect(self.toggle_chat_thread_safe)
        self.shortcut.start()
        
        # Observer Thread
        self.observer = observer.Observer()
        self.observer.signals.suggestion_ready.connect(self.on_suggestion)
        self.observer.signals.prepare_capture.connect(self.hide_ui_for_capture)
        self.observer.signals.finished_capture.connect(self.restore_ui_after_capture)
        
        self.observer_thread = threading.Thread(target=self.observer.loop, daemon=True)
        
        # State for capture
        self.was_chat_visible = False
        self.was_bubble_visible = False
        
        # System Tray
        self.tray_icon = QSystemTrayIcon(self.icon, self.app)
        self.tray_icon.setToolTip("Cora AI")
        
        # Tray Interactions
        self.tray_icon.activated.connect(self.on_tray_activate)
        
        # Tray Menu
        self.tray_menu = QMenu()
        
        self.chat_action = QAction("Open Chat", self.app)
        self.chat_action.triggered.connect(self.open_chat)
        self.tray_menu.addAction(self.chat_action)
        
        self.show_hint_action = QAction("Show Last Hint", self.app)
        self.show_hint_action.triggered.connect(self.show_last_hint)
        self.tray_menu.addAction(self.show_hint_action)
        
        self.quit_action = QAction("Exit", self.app)
        self.quit_action.triggered.connect(self.quit_app)
        self.tray_menu.addAction(self.quit_action)
        
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.show()

        self.last_title = "Welcome"
        self.last_details = "Cora is running silently."
        
        # Auto-trigger test for user

        # Use a fake payload for the initial test
        test_payload = {
            "reason": "Cora UI Test Active",
            "confidence": 1.0, 
            "suggestions": [
                {"id": "test", "label": "See Suggestions", "hint": "Click to test the UI"}
            ]
        }
        QTimer.singleShot(3000, lambda: self.on_suggestion(test_payload))

    def start(self):
        self.observer_thread.start()
        sys.exit(self.app.exec())

    def on_tray_activate(self, reason):
        # Open chat on any click (Trigger or DoubleClick)
        # Simplified to avoid Enum type errors on some PyQt versions
        self.open_chat()

    def toggle_chat_thread_safe(self):
        # Ensure toggling runs on main thread (Signals handle this usually, but explicit name helps)
        self.open_chat()

    def open_chat(self):
        if self.chat_win.isVisible():
            self.chat_win.hide()
            self.observer.resume() # Resume observer when chat closes
        else:
            self.observer.pause() # Pause observer to prevent auto-hiding while user types
            self.chat_win.show()
            self.chat_win.activateWindow()
            self.chat_win.raise_()

    def handle_chat_message(self, text, attachment=None):
        print(f"User sent: {text} | File: {attachment}")
        t = threading.Thread(target=self._process_chat, args=(text, attachment))
        t.start()
        
    def handle_stop(self):
        print("Stop requested.")
        self.observer.stop_chat()

    def _process_chat(self, text, attachment=None):
        print("Processing chat in background (Streaming)...")
        
        # 1. Create empty AI bubble
        self.chat_win.ai_response_signal.emit("") 
        
        # 2. Stream tokens
        full_response = ""
        for token in self.observer.stream_chat_with_screen(text, attachment):
            full_response += token
            # Update UI incrementally
            self.chat_win.stream_token_signal.emit(token)
            
        print(f"AI Response Complete: {len(full_response)} chars")
        self.chat_win.stream_finished_signal.emit()

    def on_suggestion(self, payload):
        print(f"Proactive Suggestion: {payload.get('reason')}")

        # Pass the full payload to the bubble to render
        QTimer.singleShot(0, lambda: self.bubble.show_payload(payload))

    def show_last_hint(self):
        self.bubble.show_message(self.last_title, self.last_details)

    def handle_overlay_action(self, user_text, internal_prompt):
        print(f"Overlay Action: {user_text}")
        
        # 1. Force Open Chat Window First
        self.open_chat()
        
        # 2. Add USER FRIENDLY message to UI
        self.chat_win.add_user(user_text)
        
        # 3. Process the INTERNAL PROMPT in background
        t = threading.Thread(target=self._process_chat, args=(internal_prompt,))
        t.start()

    def hide_ui_for_capture(self):
        # Store state
        self.was_chat_visible = self.chat_win.isVisible()
        self.was_bubble_visible = self.bubble.icon_widget.isVisible()
        
        # Hide logic
        if self.was_chat_visible:
            self.chat_win.hide()
        if self.was_bubble_visible:
            self.bubble.icon_widget.hide()
            
    def restore_ui_after_capture(self):
        # Restore state
        if self.was_chat_visible:
            self.chat_win.show()
        if self.was_bubble_visible:
            self.bubble.icon_widget.show()

    def quit_app(self):
        self.observer.stop()
        self.app.quit()

if __name__ == "__main__":
    cora = CoraApp()
    cora.start()
