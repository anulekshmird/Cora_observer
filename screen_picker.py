import io
import mss
from PIL import Image
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, pyqtSignal, QRect
from PyQt6.QtGui import QPainter, QColor, QCursor, QPen, QFont


class ScreenPicker(QWidget):
    region_selected = pyqtSignal(int, int, bytes, str)
    cancelled       = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Get real screen geometry including all monitors
        from PyQt6.QtWidgets import QApplication
        screen  = QApplication.primaryScreen()
        geom    = screen.geometry()
        v_geom  = screen.virtualGeometry()

        self.setGeometry(v_geom)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._start_point = None
        self._end_point   = None
        self._is_drawing  = False

        # Store screen offset for coordinate mapping
        self._screen_offset_x = v_geom.x()
        self._screen_offset_y = v_geom.y()

    def paintEvent(self, event):
        painter = QPainter(self)
        # Dark overlay
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

        if self._is_drawing and self._start_point and self._end_point:
            # Convert global screen coords to local widget coords
            local_start = self.mapFromGlobal(self._start_point)
            local_end   = self.mapFromGlobal(self._end_point)
            rect = QRect(local_start, local_end).normalized()

            # Clear selection area
            painter.fillRect(rect, QColor(0, 0, 0, 0))
            painter.setPen(QPen(QColor('#3b82f6'), 2))
            painter.drawRect(rect)

            # Show dimensions
            painter.setPen(QColor('white'))
            painter.drawText(
                rect.x(), rect.y() - 6,
                f'{rect.width()}×{rect.height()}'
            )
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            gp = event.globalPosition().toPoint()
            self._start_point = gp
            self._end_point   = gp
            self._is_drawing  = True
            self.update()

    def mouseMoveEvent(self, event):
        if self._is_drawing:
            self._end_point = event.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            gp               = event.globalPosition().toPoint()
            self._end_point  = gp
            self._is_drawing = False
            self.hide()
            QApplication.processEvents()

            x1 = self._start_point.x()
            y1 = self._start_point.y()
            x2 = self._end_point.x()
            y2 = self._end_point.y()

            # Single click → tight line around cursor
            if abs(x2 - x1) < 8 and abs(y2 - y1) < 8:
                cx, cy = x1, y1
                x1, y1 = cx - 300, cy - 18
                x2, y2 = cx + 300, cy + 18

            import time
            time.sleep(0.25)
            self._capture_region(
                max(0, min(x1, x2)),
                max(0, min(y1, y2)),
                abs(x2 - x1) or 40,
                abs(y2 - y1) or 36,
            )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def _detect_content_type(self, text: str) -> str:
        t = text.strip()
        if not t:
            return "visual"
        words = t.split()

        # Error detection — highest priority
        error_keywords = [
            "error", "exception", "traceback", "failed", "undefined",
            "syntaxerror", "typeerror", "nameerror", "valueerror",
            "cannot", "could not", "no module", "line "
        ]
        if any(k in t.lower() for k in error_keywords):
            return "error"

        # Code detection — before data, because code contains numbers too
        code_keywords = [
            "def ", "class ", "import ", "return ", "function ",
            "const ", "let ", "var ", "=>", "()", "==",
            "!=", "+=", "if ", "for ", "while ", "print(",
            "self.", ".py", "{}",  "#", "//", "/*", "*/",
            "setAttr", "QtCore", "PyQt", "Widget", "Layout",
            "WindowType", "Signal", "pyqtSignal",
        ]
        code_matches = sum(1 for k in code_keywords if k in t)
        if code_matches >= 2:
            return "code"

        # Data/numbers detection
        import re
        number_count = len(re.findall(r'\b\d+\.?\d*\b', t))
        if number_count >= 4 and code_matches == 0:
            return "data"

        # Word, sentence, paragraph
        if len(words) <= 3:
            return "word"
        elif len(words) <= 25:
            return "sentence"
        else:
            return "paragraph"

    def _build_chips(self, content_type: str, text: str) -> list:
        """Return smart chips based on content type."""
        preview = text[:60] + "..." if len(text) > 60 else text
        chips = {
            "word": [
                {"label": "Synonyms",     "hint": f"Give synonyms for: {preview}"},
                {"label": "Define",       "hint": f"Define the word: {preview}"},
                {"label": "Fix Spelling", "hint": f"Check spelling of: {preview}"},
                {"label": "Use in sentence", "hint": f"Use '{preview}' in an example sentence"},
            ],
            "sentence": [
                {"label": "Fix Grammar",    "hint": f"Fix grammar in: {preview}"},
                {"label": "Rewrite",        "hint": f"Rewrite this more clearly: {preview}"},
                {"label": "Check Passive",  "hint": f"Is this passive voice? Fix if so: {preview}"},
                {"label": "Make Formal",    "hint": f"Make this more formal: {preview}"},
            ],
            "paragraph": [
                {"label": "Summarize",      "hint": f"Summarize this paragraph: {preview}"},
                {"label": "Improve",        "hint": f"Improve clarity and flow: {preview}"},
                {"label": "Fix Grammar",    "hint": f"Fix all grammar issues in: {preview}"},
                {"label": "Expand",         "hint": f"Expand this with more detail: {preview}"},
            ],
            "code": [
                {"label": "Explain Code",   "hint": f"Explain what this code does: {preview}"},
                {"label": "Fix Bugs",       "hint": f"Find and fix bugs in: {preview}"},
                {"label": "Optimize",       "hint": f"Suggest optimizations for: {preview}"},
                {"label": "Add Comments",   "hint": f"Add docstrings and comments to: {preview}"},
            ],
            "error": [
                {"label": "Fix Error",      "hint": f"Fix this error: {preview}"},
                {"label": "Explain Cause",  "hint": f"Explain what caused: {preview}"},
                {"label": "Find Solution",  "hint": f"Find the solution to: {preview}"},
            ],
            "data": [
                {"label": "Analyze",        "hint": f"Analyze this data: {preview}"},
                {"label": "Explain",        "hint": f"Explain these numbers: {preview}"},
                {"label": "Summarize",      "hint": f"Summarize key figures in: {preview}"},
            ],
            "visual": [
                {"label": "Describe",       "hint": "Describe what is in this region"},
                {"label": "Explain",        "hint": "Explain what this shows"},
                {"label": "Ask Question",   "hint": "I have a question about this"},
            ],
        }
        return chips.get(content_type, chips["visual"])

    def _capture_region(self, left: int, top: int, width: int, height: int):
        try:
            import mss
            from PIL import Image
            import io
            import time

            # Extra delay to ensure picker overlay is fully hidden
            time.sleep(0.3)

            print(f'[PICK] Capture: left={left} top={top} w={width} h={height}')

            with mss.mss() as sct:
                # Use monitor 1 (primary) not 0 (all monitors combined)
                # This avoids coordinate offset issues
                mon = sct.monitors[1]
                region = {
                    "top":    max(0, top),
                    "left":   max(0, left),
                    "width":  min(max(40, width),  mon["width"]  - left),
                    "height": min(max(20, height), mon["height"] - top),
                }
                shot = sct.grab(region)
                img  = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

            # Upscale small captures
            if img.width < 800:
                scale = max(2, 800 // img.width)
                img   = img.resize(
                    (img.width * scale, img.height * scale),
                    Image.LANCZOS,
                )

            # OCR
            ocr_text = ''
            try:
                import pytesseract
                ocr_text = pytesseract.image_to_string(img).strip()
            except Exception:
                try:
                    from ocr_engine import extract_text_for_window
                    ocr_text = extract_text_for_window(
                        image=img,
                        window_title='',
                        mode_primary='general',
                    ).strip()
                except Exception as e:
                    print(f'[PICK] OCR error: {e}')

            ocr_text = ocr_text[:2000]
            print(f'[PICK] OCR ({len(ocr_text)}ch): "{ocr_text[:80]}"')

            buf = io.BytesIO()
            img.save(buf, format='PNG')

            self.region_selected.emit(left, top, buf.getvalue(), ocr_text)
            self.close()

        except Exception as e:
            import traceback
            print(f'[PICK] Error: {e}')
            traceback.print_exc()
            self.cancelled.emit()
            self.close()
