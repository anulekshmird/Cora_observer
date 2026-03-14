import sys
import os
import json
import re
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QPoint, QEasingCurve, QRect, QSize, QTimer
from PyQt6.QtGui import QIcon, QPainter, QColor, QBrush, QPainterPath
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QGraphicsOpacityEffect,
    QLineEdit, QSizePolicy
)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_chip_prompt(task: str, screen_ctx: str, reason: str, win_title: str,
                       page_title: str = "", site_name: str = "", selected_text: str = "") -> str:
    clean_ctx = re.sub(r'\n{3,}', '\n\n', screen_ctx.strip())
    clean_ctx = clean_ctx[:3000]

    context_label = page_title or site_name or win_title or "Unknown"

    selected_section = (
        f"\nUSER-SELECTED TEXT (user highlighted this — highest priority):\n"
        f"{'='*50}\n{selected_text}\n{'='*50}\n"
        if selected_text else ""
    )

    return f"""You are Cora, a helpful desktop AI assistant.

TASK: {task}

ACTIVE CONTENT: {context_label}
ACTIVE APPLICATION: {win_title or 'Unknown'}
WHAT CORA NOTICED: {reason}
{selected_section}
SCREEN TEXT (OCR extracted — use this as ground truth):
{clean_ctx if clean_ctx else '(no text captured — respond based on task and active content above)'}

RESPONSE RULES:
- You DO have access to the screen via OCR text above and the image provided.
- NEVER say "I don't have access to your screen" or "I cannot see your screen".
- If screen text is present, base your response on it directly.
- If no screen text, use the ACTIVE CONTENT label to answer.
- Respond directly and helpfully in clear prose or bullet points.
- Do NOT use Error / Cause / Fix / Commands structure unless the task
  is explicitly about fixing a code or terminal error.
- Do NOT output JSON.
- Do NOT add preamble like "Sure!" or "Of course!".
- Keep the response focused and concise."""


def _build_error_prompt(action_type: str, data: dict) -> tuple:
    error_file    = data.get('error_file',    'Unknown')
    error_line    = data.get('error_line',    '?')
    error_msg     = data.get('error_message', '') or data.get('reason', '')
    error_context = data.get('error_context', '') or data.get('code', '')

    if isinstance(error_context, dict):
        error_context = json.dumps(error_context, indent=2)
    error_context = re.sub(r'\n{4,}', '\n\n', str(error_context).strip())
    error_context = error_context[:2000]

    header = (
        f"FILE:  {error_file}\n"
        f"LINE:  {error_line}\n"
        f"ERROR: {error_msg}\n\n"
        f"CODE:\n{error_context}\n\n"
    )

    if action_type == "fix_error":
        prompt = (
            header +
            "TASK: Explain the fix in one sentence, then provide the fully corrected code.\n\n"
            "Use this exact format — write the actual code, never use placeholders:\n\n"
            "⚠ Error\n"
            f"{error_msg}\n\n"
            "Fix\n"
            "Brief explanation here.\n\n"
            "Commands\n"
            "```python\n"
            "# write the actual corrected code here — never write CODE_BLOCK or placeholders\n"
            "```"
        )
        display = "Fixing Syntax Error..."

    elif action_type == "explain_error":
        prompt = (
            header +
            "TASK: Explain what caused this error and why it occurs. "
            "Write in clear prose — no code block needed unless it helps."
        )
        display = "Explaining Error..."

    elif action_type == "show_code":
        prompt = (
            header +
            "TASK: Provide ONLY the corrected code block. No explanation, no prose."
        )
        display = "Showing Corrected Code..."

    else:
        return "", ""

    return display, prompt


# ─────────────────────────────────────────────────────────────────────────────
# ProactiveBubble
# ─────────────────────────────────────────────────────────────────────────────

class DraggableOrb(QPushButton):
    drag_moved = pyqtSignal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_active = False
        self._drag_start  = QPoint()
        self._drag_threshold = 5
        self._moved       = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_start  = event.globalPosition().toPoint()
            self._moved       = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active:
            delta = event.globalPosition().toPoint() - self._drag_start
            if delta.manhattanLength() > self._drag_threshold:
                self._moved = True
                self.drag_moved.emit(event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = False
            if self._moved:
                event.accept()  # swallow click if dragged
                return
        super().mouseReleaseEvent(event)

class ProactiveBubble(QWidget):
    dismissed        = pyqtSignal()
    ask_cora_clicked = pyqtSignal(str, str)
    pick_requested   = pyqtSignal()

    # Orb states
    STATE_IDLE       = 'idle'
    STATE_PULSING    = 'pulsing'
    STATE_EXPANDED   = 'expanded'
    STATE_ERROR      = 'error'

    def __init__(self, parent=None):
        super().__init__(parent)

        # Window setup — always on top, no frame
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._state        = self.STATE_IDLE
        self._current_data = None
        self._pulse_step   = 0

        # Position — bottom right corner
        screen          = QApplication.primaryScreen().availableGeometry()
        self._orb_size  = 52
        self._panel_w   = 380
        self._panel_h   = 400

        # Start at bottom right
        self._orb_x = screen.width()  - self._orb_size - 20
        self._orb_y = screen.height() - self._orb_size - 20

        self.setGeometry(
            self._orb_x - self._panel_w,
            self._orb_y - self._panel_h,
            self._panel_w + self._orb_size + 20,
            self._panel_h + self._orb_size + 20,
        )

        # Drag state
        self._drag_active = False
        self._drag_offset = QPoint(0, 0)

        self._build_ui()
        self._build_pulse_timer()
        self.show()
        self._set_state(self.STATE_IDLE)

    # ── UI Construction ───────────────────────────────────────

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Panel (hidden by default) ──────────────────────
        self.panel = QFrame()
        self.panel.setFixedWidth(self._panel_w)
        self.panel.setMinimumWidth(360)
        self.panel.setMaximumWidth(400)
        self.panel.setStyleSheet("""
            QFrame {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1e2433,
                    stop:1 #161b27
                );
                border: 1px solid #2d3748;
                border-radius: 16px;
            }
        """)
        self.panel.hide()

        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(14, 12, 14, 12)
        panel_layout.setSpacing(8)

        # Title row
        title_row = QHBoxLayout()
        icon_lbl  = QLabel("✨")
        icon_lbl.setStyleSheet("font-size: 14px; background: transparent; border: none;")
        self.title_lbl = QLabel("Cora Suggestion")
        self.title_lbl.setStyleSheet("""
            color: #f1f5f9;
            font-size: 13px;
            font-weight: bold;
            background: transparent;
            border: none;
        """)
        title_row.addWidget(icon_lbl)
        title_row.addWidget(self.title_lbl)
        title_row.addStretch()
        panel_layout.addLayout(title_row)

        # Reason label
        self.reason_lbl = QLabel("")
        self.reason_lbl.setWordWrap(True)
        self.reason_lbl.setMaximumWidth(360)
        self.reason_lbl.setStyleSheet("""
            color: #94a3b8;
            font-size: 11px;
            background: transparent;
            border: none;
            line-height: 1.4;
        """)
        panel_layout.addWidget(self.reason_lbl)

        # Chips container
        self.chips_widget = QWidget()
        self.chips_widget.setStyleSheet("background: transparent; border: none;")
        self.chips_layout = QVBoxLayout(self.chips_widget)
        self.chips_layout.setContentsMargins(0, 0, 0, 0)
        self.chips_layout.setSpacing(6)
        panel_layout.addWidget(self.chips_widget)

        # Ask input row
        ask_row = QHBoxLayout()
        self.ask_input = QLineEdit()
        self.ask_input.setPlaceholderText("Ask about this...")
        self.ask_input.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,0.07);
                border: 1px solid #334155;
                border-radius: 8px;
                color: #e2e8f0;
                padding: 7px 10px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #3b82f6;
                background: rgba(255,255,255,0.10);
            }
        """)
        self.ask_input.returnPressed.connect(self._on_ask_submitted)

        send_btn = QPushButton("→")
        send_btn.setFixedSize(30, 30)
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setStyleSheet("""
            QPushButton {
                background: #3b82f6;
                border: none;
                border-radius: 8px;
                color: white;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background: #2563eb; }
        """)
        send_btn.clicked.connect(self._on_ask_submitted)

        ask_row.addWidget(self.ask_input)
        ask_row.addWidget(send_btn)
        panel_layout.addLayout(ask_row)

        # Bottom buttons
        btn_row = QHBoxLayout()
        self.dismiss_btn = QPushButton("✕ Dismiss")
        self.dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dismiss_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #475569;
                color: #94a3b8;
                border-radius: 6px;
                padding: 5px 14px;
                font-size: 11px;
            }
            QPushButton:hover {
                background: rgba(239,68,68,0.15);
                border-color: #ef4444;
                color: #ef4444;
            }
        """)
        self.dismiss_btn.clicked.connect(self._on_dismiss)

        self.pick_btn = QPushButton("🎯 Pick")
        self.pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pick_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #3b82f6;
                color: #3b82f6;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 11px;
            }
            QPushButton:hover {
                background: rgba(59,130,246,0.15);
                color: #60a5fa;
            }
        """)
        self.pick_btn.clicked.connect(self.pick_requested.emit)

        btn_row.addWidget(self.dismiss_btn)
        btn_row.addWidget(self.pick_btn)
        btn_row.addStretch()
        panel_layout.addLayout(btn_row)

        # ── Orb button ─────────────────────────────────────
        self.orb_btn = DraggableOrb()
        self.orb_btn.drag_moved.connect(self._on_orb_dragged)
        self.orb_btn.setFixedSize(self._orb_size, self._orb_size)
        self.orb_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.orb_btn.clicked.connect(self._on_orb_clicked)

        # Set icon.png as orb image
        import os
        icon_path = os.path.join(os.path.dirname(__file__), 'icon.png')
        if os.path.exists(icon_path):
            from PyQt6.QtGui import QIcon, QPixmap
            pixmap = QPixmap(icon_path).scaled(
                self._orb_size - 8,
                self._orb_size - 8,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.orb_btn.setIcon(QIcon(pixmap))
            self.orb_btn.setIconSize(QSize(self._orb_size - 8, self._orb_size - 8))
        self._set_orb_style('#111827', '#1e3a5f')

        # ── Assemble ───────────────────────────────────────
        main_layout.addWidget(self.panel,   0, Qt.AlignmentFlag.AlignRight)
        main_layout.addWidget(self.orb_btn, 0, Qt.AlignmentFlag.AlignRight)

    def _build_pulse_timer(self):
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(500)
        self._pulse_timer.timeout.connect(self._pulse_tick)

    # ── Orb style ─────────────────────────────────────────────

    def _set_orb_style(self, bg: str, border: str, glow: str = ''):
        border_width = '3px' if glow else '2px'
        border_color = glow if glow else border
        self.orb_btn.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                border: {border_width} solid {border_color};
                border-radius: {self._orb_size//2}px;
            }}
            QPushButton:hover {{
                background: #1e3a5f;
                border: 2px solid #60a5fa;
            }}
        """)
        # Use icon.png — no emoji text
        self.orb_btn.setText("")

    # ── State machine ─────────────────────────────────────────

    def _set_state(self, state: str):
        self._state = state

        if state == self.STATE_IDLE:
            self._pulse_timer.stop()
            self._set_orb_style('#111827', '#1e3a5f')
            self.orb_btn.setText("🤖")

        elif state == self.STATE_PULSING:
            self._pulse_step = 0
            self._pulse_timer.setInterval(500)
            self._pulse_timer.start()

        elif state == self.STATE_ERROR:
            self._pulse_step = 0
            self._pulse_timer.setInterval(300)  # faster for error
            self._pulse_timer.start()

        elif state == self.STATE_EXPANDED:
            self._pulse_timer.stop()
            self._set_orb_style('#1e3a5f', '#3b82f6')
            self.orb_btn.setText("🤖")

    def _pulse_tick(self):
        self._pulse_step += 1

        if self._state == self.STATE_ERROR:
            # Aggressive red pulse — alternates bright red and dark red
            if self._pulse_step % 2 == 0:
                self.orb_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #7f1d1d;
                        border: 3px solid #ef4444;
                        border-radius: {self._orb_size//2}px;
                        font-size: 22px;
                    }}
                """)
                self.orb_btn.setText("🔴")
            else:
                self.orb_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #dc2626;
                        border: 3px solid #fca5a5;
                        border-radius: {self._orb_size//2}px;
                        font-size: 22px;
                    }}
                """)
                self.orb_btn.setText("🔴")
            # Error keeps pulsing until dismissed — no timeout
            return

        # Normal blue pulse — stops after 6 blinks
        if self._pulse_step % 2 == 0:
            self._set_orb_style('#0f172a', '#1d4ed8')
        else:
            self._set_orb_style('#1e3a5f', '#60a5fa', glow='#3b82f6')

        if self._pulse_step >= 12:
            self._set_state(self.STATE_IDLE)

    # ── Public API ────────────────────────────────────────────

    def show_suggestion(self, data: dict):
        """New suggestion ready — store and start pulsing."""
        self._current_data = data
        self._set_state(self.STATE_PULSING)
        # If already expanded, update content immediately
        if self.panel.isVisible():
            self._render_panel(data)

    def hide_bubble(self):
        pass  # Never hide the orb

    # ── Panel render ──────────────────────────────────────────

    def show_error_alert(self, data: dict):
        """Show red pulsing orb for confirmed errors."""
        self._current_data = data
        self._set_state(self.STATE_ERROR)
        # Auto-expand panel for errors so user sees chips immediately
        self._render_panel(data)
        self.panel.show()
        self.is_expanded = True
        self.raise_()
        self.adjustSize()

    def _render_panel(self, data: dict):
        if not data:
            return

        reason = data.get('reason', 'Cora Suggestion')
        self.title_lbl.setText("✨ Cora Suggestion")
        self.reason_lbl.setText(reason)

        # Clear old chips
        while self.chips_layout.count():
            item = self.chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                # Clear nested layouts
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

        suggestions = data.get('suggestions', [])

        i = 0
        while i < len(suggestions):
            row_widget = QWidget()
            row_widget.setStyleSheet("background: transparent; border: none;")
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.setSpacing(8)

            for j in range(2):
                if i >= len(suggestions):
                    row_layout.addStretch()
                    break

                sug   = suggestions[i]
                label = sug.get('label', '')
                hint  = sug.get('hint', label)

                chip = QPushButton(label)
                chip.setCursor(Qt.CursorShape.PointingHandCursor)
                chip.setFixedHeight(34)
                chip.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Fixed,
                )
                chip.setStyleSheet("""
                    QPushButton {
                        background: rgba(59,130,246,0.1);
                        border: 1px solid #3b82f6;
                        color: #e2e8f0;
                        border-radius: 8px;
                        padding: 4px 8px;
                        font-size: 11px;
                        text-align: center;
                    }
                    QPushButton:hover {
                        background: rgba(59,130,246,0.3);
                        color: white;
                        border-color: #60a5fa;
                    }
                """)
                chip.clicked.connect(
                    lambda checked, l=label, h=hint:
                    self.ask_cora_clicked.emit(l, h)
                )
                row_layout.addWidget(chip)
                i += 1

            self.chips_layout.addWidget(row_widget)

        # Add spacing after chips
        self.chips_layout.addSpacing(4)

        if hasattr(self, 'ask_input'):
            self.ask_input.clear()

    def _on_orb_dragged(self, global_pos: QPoint):
        """Move entire widget when orb is dragged."""
        new_top_left = global_pos - QPoint(
            self.width()  - self._orb_size // 2,
            self.height() - self._orb_size // 2,
        )
        # Clamp to screen
        screen = QApplication.primaryScreen().availableGeometry()
        new_top_left.setX(max(0, min(new_top_left.x(), screen.width()  - self.width())))
        new_top_left.setY(max(0, min(new_top_left.y(), screen.height() - self.height())))
        self.move(new_top_left)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_active:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = False
            event.accept()

    def _on_orb_clicked(self):
        if self._state == self.STATE_EXPANDED:
            # Collapse
            self.panel.hide()
            self._set_state(self.STATE_IDLE)
            self.adjustSize()
        else:
            # Expand and show current suggestion
            if self._current_data:
                self._render_panel(self._current_data)
            self.panel.show()
            self._set_state(self.STATE_EXPANDED)
            self.ask_input.setFocus()
            self.adjustSize()

    def _on_dismiss(self):
        self._current_data         = None
        self.is_expanded           = False
        self.is_read_more_expanded = False if hasattr(self, 'is_read_more_expanded') else False
        self.panel.hide()
        self._set_state(self.STATE_IDLE)  # This resets orb color and stops pulse
        self.adjustSize()
        self.dismissed.emit()

    def _on_ask_submitted(self):
        text = self.ask_input.text().strip()
        if not text:
            return
        self.ask_input.clear()
        self.ask_cora_clicked.emit(text, text)

    def toggle_expand(self):
        self._on_orb_clicked()

    def update_layout_pos(self):
        pass  # Position is fixed

    def is_visible_and_expanded(self):
        return self._state == self.STATE_EXPANDED

    def fade_out(self, force=False):
        pass

    def enter_idle_mode(self):
        pass

    def show_message(self, title, message):
        self.show_suggestion({'reason': message})
        self.title_lbl.setText(title)

    def trigger_reading_action(self, hint: str):
        # Compatibility stub
        self.ask_cora_clicked.emit(hint, hint)