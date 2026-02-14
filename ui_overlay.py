from PyQt6.QtWidgets import (QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLineEdit, QApplication, QGraphicsOpacityEffect, QFrame)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal, QSize, QPoint, QRect
from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap, QPainter, QPainterPath

class FloatingIcon(QWidget):
    clicked = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(60, 60)
        self.setStyleSheet("background-color: transparent;")
        
        # Close Button
        self.btn_close = QPushButton("×", self)
        self.btn_close.setGeometry(42, 2, 16, 16)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background-color: #d42020;
                color: white;
                border: 1px solid white;
                border-radius: 8px;
                font-weight: bold;
                padding-bottom: 2px;
            }
            QPushButton:hover { background-color: #ff0000; }
        """)
        self.btn_close.clicked.connect(self.hide)
        
        self.drag_start_pos = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Load icon - fallback if not found
        icon_path = "icon.png"
        icon = QPixmap(icon_path)
        
        if not icon.isNull():
            # Draw the Icon filling the entire widget (60x60)
            painter.drawPixmap(self.rect(), icon)
        else:
            # Fallback
            painter.setPen(QColor("#0078d4"))
            painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Cora")


    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.click_start_pos = event.globalPosition().toPoint() # Correct logic to distinguish click vs drag

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self.drag_start_pos:
            self.move(event.globalPosition().toPoint() - self.drag_start_pos)

    def mouseReleaseEvent(self, event):
        # Calculate distance moved to distinguish click from drag
        if event.button() == Qt.MouseButton.LeftButton:
            if hasattr(self, 'click_start_pos'):
                current_pos = event.globalPosition().toPoint()
                dist = (current_pos - self.click_start_pos).manhattanLength()
                if dist < 5: # Threshold for click
                    self.clicked.emit()
            else:
                 self.clicked.emit()
            self.drag_start_pos = None

class AISuggestion:
    def __init__(self, id, label, hint=""):
        self.id = id
        self.label = label
        self.hint = hint

class SuggestionMenu(QWidget):
    action_clicked = pyqtSignal(str) # action text

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(320, 400)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        # Main Container
        self.container = QFrame()
        self.container.setStyleSheet("""
            QFrame {
                background-color: white;
                border-radius: 12px;
                border: 1px solid #e0e0e0;
            }
            QLabel { color: #333; border: none; }
            QPushButton {
                text-align: left;
                padding: 10px;
                background-color: transparent;
                border: none;
                border-radius: 6px;
                color: #444;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #f0f0f0; }
        """)
        
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(8)
        
        # Header Row
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(10, 10, 10, 0)
        
        self.header = QLabel("Cora Suggestions")
        self.header.setStyleSheet("color: #0078d4; font-weight: bold; font-size: 16px;")
        
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background-color: #d42020;
                color: white;
                border: none;
                border-radius: 12px;
                font-weight: bold;
                padding-bottom: 2px;
            }
            QPushButton:hover { background-color: #ff0000; }
        """)
        self.btn_close.clicked.connect(self.hide) 
        
        header_layout.addWidget(self.header)
        header_layout.addStretch()
        header_layout.addWidget(self.btn_close)
        
        self.vbox.addLayout(header_layout)
        
        # Dynamic Actions Area
        self.actions_layout = QVBoxLayout()
        self.vbox.addLayout(self.actions_layout)
        
        self.vbox.addStretch()
        
        # Input Area (Always present)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Tell us to...")
        self.input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #ddd;
                border-radius: 8px;
                padding: 8px;
                background-color: #f9f9f9;
                color: #333;
            }
        """)
        self.input.returnPressed.connect(lambda: self.action_clicked.emit(self.input.text()))
        self.vbox.addWidget(self.input)
        
        layout.addWidget(self.container)

    def load_suggestions(self, suggestions):
        # Clear existing buttons
        while self.actions_layout.count():
            item = self.actions_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # Add new buttons from payload
        for sugg in suggestions:
            # sugg is AISuggestion object
            btn = QPushButton(f"✨  {sugg.label}")
            if sugg.hint:
                btn.setToolTip(sugg.hint)
            # Connect signal
            # We pass the ID or Label back to main
            btn.clicked.connect(lambda checked, s=sugg: self.action_clicked.emit(s.label))
            self.actions_layout.addWidget(btn)

class ProactiveBubble(QWidget):
    ask_cora_clicked = pyqtSignal(str, str) # Emits (UserVisibleText, InternalPrompt)
    
    def __init__(self):
        super().__init__()
        # We don't show *this* widget directly, we manage the icon and menu
        self.icon_widget = FloatingIcon()
        self.menu_widget = SuggestionMenu()
        
        self.icon_widget.clicked.connect(self.show_menu)
        self.menu_widget.action_clicked.connect(self.on_action)
        
        self.current_context = ""

    def show_payload(self, payload):
        reason = payload.get("reason", "Suggestion")
        suggestions = payload.get("suggestions", [])
        
        # Convert JSON dicts to Objects
        sugg_objects = []
        for s in suggestions:
            s_obj = AISuggestion(s.get("id", "unknown"), s.get("label", "Action"), s.get("hint", ""))
            sugg_objects.append(s_obj)
            
        # Update Menu
        self.menu_widget.load_suggestions(sugg_objects)
        self.current_context = reason
        
        # Position Icon near bottom right
        screen = QApplication.primaryScreen().geometry()
        x = screen.width() - 80
        y = screen.height() - 150
        
        self.icon_widget.move(x, y)
        self.icon_widget.show()
        
    def show_message(self, title, message):
        # Legacy adapter for manual tests
        self.show_payload({
            "reason": f"{title}: {message}",
            "suggestions": [{"id": "chat", "label": "Ask Cora", "hint": "Discuss this"}]
        })

    def show_menu(self):
        # Position menu near icon
        pos = self.icon_widget.pos()
        self.menu_widget.move(pos.x() - 280, pos.y() - 350) # Shift up and left
        self.menu_widget.show()
        self.icon_widget.hide()

    def on_action(self, action_text):
        # Construct a strong prompt for the AI to execute the action
        query = (
            f"COMMAND: {action_text}\n"
            f"REASON: {self.current_context}\n"
            f"INSTRUCTION: internal_monologue='User wants me to fix this specific issue.' "
            f"Look strictly at the part of the screen relevant to the Reason. "
            f"Provide the solution or fix immediately. Do not describe the rest of the screen."
        )
        self.ask_cora_clicked.emit(action_text, query)
        self.menu_widget.hide()
