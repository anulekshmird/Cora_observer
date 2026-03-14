"""
Layer 2: CONTEXT EXTRACTOR
Converts raw OS events into structured Context objects.
Runs in background thread. No LLM calls.
"""
import time
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Context:
    app:          str   = 'general'
    mode:         str   = 'general'
    window_title: str   = ''
    visible_text: str   = ''
    selected_text:str   = ''
    image:        bytes = None
    url:          str   = ''
    extra:        dict  = None
    source:       str   = 'window'
    timestamp:    float = 0.0

    def best_text(self) -> str:
        if self.selected_text:
            return self.selected_text
        return self.visible_text


class ContextHelpers:

    @staticmethod
    def get_browser_url(window_title: str = '') -> str:
        """Read URL from Chrome by matching window title, not requiring focus."""
        try:
            import uiautomation as auto

            root = auto.GetRootControl()
            best_match = None
            best_score = 0

            for w in root.GetChildren():
                if 'Chrome_WidgetWin' not in w.ClassName:
                    continue

                win_name = w.Name or ''

                # Score this window — higher = better match
                score = 0
                if window_title and window_title[:30].lower() in win_name.lower():
                    score += 10
                if win_name and len(win_name) > 5:
                    score += 1

                # Skip CORA's own windows
                skip = ['antigravity', 'cora ai', 'cora suggestion']
                if any(k in win_name.lower() for k in skip):
                    continue

                if score >= best_score:
                    best_score   = score
                    best_match   = w

            if best_match is None:
                return ''

            # Read URL from address bar
            try:
                edit = best_match.EditControl(searchDepth=15)
                if edit.Exists(0.3):
                    val = edit.GetValuePattern().Value
                    if val and '.' in val:
                        url = val if val.startswith('http') else f'https://{val}'
                        print(f'[URL] Found: {url[:80]}')
                        return url
            except Exception as e:
                print(f'[URL] Edit error: {e}')

        except Exception as e:
            print(f'[URL] Error: {e}')
        return ''

    @staticmethod
    def get_word_text() -> tuple:
        try:
            import win32com.client
            word = win32com.client.GetActiveObject('Word.Application')
            doc  = word.ActiveDocument
            # Read full document content
            full = doc.Range(0, doc.Content.End - 1).Text
            full = full[:6000].strip()
            try:
                sel = word.Selection.Text.strip()
                if len(sel) < 3:
                    sel = ''
            except:
                sel = ''
            print(f'[WORD COM] Read {len(full)}ch, selection={len(sel)}ch')
            return full, sel
        except Exception as e:
            print(f'[WORD COM] Error: {e}')
            return '', ''

    @staticmethod
    def get_pdf_text(window_title: str) -> str:
        try:
            import fitz, os, re
            name = re.sub(r'\s*[-–]\s*(Adobe|Foxit|PDF|Acrobat).*$', '', window_title, flags=re.I).strip()
            search_dirs = [
                os.path.expanduser('~/Desktop'),
                os.path.expanduser('~/Downloads'),
                os.path.expanduser('~/Documents'),
            ]
            for d in search_dirs:
                if not os.path.exists(d): continue
                for f in os.listdir(d):
                    if f.lower().endswith('.pdf') and name.lower() in f.lower():
                        path = os.path.join(d, f)
                        doc  = fitz.open(path)
                        text = ''
                        for i in range(min(8, len(doc))):
                            text += doc[i].get_text()
                        return text[:5000]
        except Exception as e:
            print(f'[PDF] {e}')
        return ''

    @staticmethod
    def capture_active_window_image():
        try:
            import mss
            from PIL import Image
            import pygetwindow as gw

            win = gw.getActiveWindow()
            if not win:
                return None

            left   = max(0, win.left)
            top    = max(0, win.top)
            width  = min(win.width,  3840)
            height = min(win.height, 2160)

            if width < 50 or height < 50:
                return None

            with mss.mss() as sct:
                region = {"top": top, "left": left, "width": width, "height": height}
                shot   = sct.grab(region)
                img    = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            return img
        except Exception as e:
            print(f'[CAPTURE] {e}')
            return None

    @staticmethod
    def get_youtube_info(url: str) -> dict:
        try:
            import yt_dlp
            opts = {
                'quiet':           True,
                'skip_download':   True,
                'extract_flat':    True,
                'socket_timeout':  5,  # 5s timeout
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return {
                    'title':       info.get('title', ''),
                    'description': (info.get('description') or '')[:1000],
                    'duration':    info.get('duration', 0),
                    'channel':     info.get('channel', ''),
                    'chapters':    info.get('chapters', []),
                }
        except Exception as e:
            print(f'[YT] {e}')
        return {}

    @staticmethod
    def clean_ocr(text: str, mode: str = 'general') -> str:
        import re
        lines   = text.splitlines()
        cleaned = []

        # For code mode — be more aggressive about keeping code
        ui_noise = [
            'file', 'edit', 'view', 'help', 'format', 'tools',
            'window', 'insert', 'home', 'layout', 'references',
            'terminal', 'run', 'debug', 'source control',
            'ask anything', 'message cora', 'cora suggestion',
            'walkthrough', 'open', 'dismiss', 'pick',
        ]

        for line in lines:
            line = line.strip()
            if len(line) < 2:
                continue
            if re.match(r'^\d+$', line):
                continue
            if line.lower() in ui_noise:
                continue
            # Skip lines that look like UI buttons
            if len(line) < 20 and line.lower() in [w.lower() for w in ui_noise]:
                continue
            cleaned.append(line)

        return '\n'.join(cleaned)


class ContextExtractor:
    """Converts raw event data into Context objects asynchronously."""

    def __init__(self, ocr_engine=None):
        self._ocr_engine = ocr_engine
        self._executor   = None
        self._busy       = False
        self._busy_since = 0

    def extract_async(self, event_type: str, event_data: dict,
                      callback) -> None:
        """Extract context in background thread, call callback with Context."""
        def _run():
            import time
            if self._busy:
                # Force reset if stuck for more than 8s
                if hasattr(self, '_busy_since') and time.time() - self._busy_since > 8.0:
                    print('[EXTRACTOR] Force reset busy flag')
                    self._busy = False
                else:
                    print('[EXTRACTOR] Busy — skipping')
                    return

            self._busy       = True
            self._busy_since = time.time()
            try:
                import asyncio
                # Use asyncio to run the async _build_context
                ctx = asyncio.run(self._build_context(event_type, event_data))
                if ctx and callback:
                    callback(ctx)
            except Exception as e:
                print(f"ContextExtractor error: {e}")
            finally:
                self._busy       = False
                self._busy_since = 0

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def _extract(self, event_type: str, event_data: dict, callback) -> None:
        try:
            import asyncio
            # Use asyncio to run the async _build_context
            ctx = asyncio.run(self._build_context(event_type, event_data))
            callback(ctx)
        except Exception as e:
            print(f"ContextExtractor error: {e}")

    async def _build_context(self, event_type: str, event_data: dict) -> Context:
        from system_observer import SystemEvent

        if event_type == SystemEvent.WINDOW_CHANGED:
            return await self._from_window(event_data)
        elif event_type == SystemEvent.TEXT_SELECTED:
            return self._from_selection(event_data)
        elif event_type == SystemEvent.REGION_CAPTURED:
            return self._from_region(event_data)
        else:
            return Context()

    async def _from_window(self, data: dict) -> 'Context':
        import time
        title = data.get('window_title', '')
        tl    = title.lower()
        ts    = data.get('timestamp', time.time())

        # ── STEP 1: Classification ───────────────────────────
        app  = 'general'
        mode = 'general'

        # Messaging apps — check BEFORE editor for privacy
        if any(k in tl for k in ['whatsapp', 'telegram', 'signal', 'instagram', 'discord']):
            app  = 'messaging'
            mode = 'general'
            # OCR the window for message context
            img = ContextHelpers.capture_active_window_image()
            if img:
                from ocr_engine import extract_text_for_window
                raw          = extract_text_for_window(image=img, window_title=title, mode_primary='general')
                visible_text = ContextHelpers.clean_ocr(raw)[:2000]
                return Context(app=app, mode=mode, window_title=title, visible_text=visible_text, source='window', timestamp=ts)

        # File Explorer
        elif any(k in tl for k in ['file explorer', 'windows explorer', 'this pc', '> documents', '> downloads', '> desktop', 'local disk']):
            app  = 'explorer'
            mode = 'general'

        # Browser
        elif any(k in tl for k in ['chrome', 'edge', 'firefox', 'opera', 'brave']):
            app  = 'browser'
            mode = 'browser'

        # Word
        elif any(k in tl for k in ['word', '.docx', 'document', 'writer']):
            app  = 'word'
            mode = 'document'

        # Code editor
        # VS Code modified files: "● filename.py" or "• filename"
        # Generic: "file.ext — folder — Editor"
        elif (
            any(k in tl for k in [
                '.js', '.py', '.html', '.css', '.c', '.cpp', '.java',
                '.go', '.rs', '.php', '.sql', '.sh', '.md', '.json',
                'visual studio code', 'vscode', 'intellij', 'sublime',
                'notepad++', 'pycharm', 'cursor',
            ]) or
            bool(re.search(r'[●•]\s*\w+\.\w+', title)) or
            bool(re.search(r'\.\w{2,4}\s*[-–—]', title))
        ):
            app  = 'editor'
            mode = 'developer'

        # PDF viewer (standalone)
        elif '.pdf' in tl or 'adobe' in tl or 'foxit' in tl or 'sumatra' in tl:
            app  = 'pdf'
            mode = 'document'

        # AI assistants
        elif any(k in tl for k in ['claude', 'chatgpt', 'gemini', 'copilot', 'perplexity']):
            app  = 'ai_chat'
            mode = 'general'
            return Context(
                app          = 'ai_chat',
                mode         = 'general',
                window_title = title,
                visible_text = '',
                source       = 'window',
                timestamp    = ts,
            )

        # Skip apps
        elif any(k in tl for k in ['antigravity', 'cora ai', 'cora suggestion']):
            return Context(
                app='skip', mode='skip',
                window_title=title,
                source='window', timestamp=ts,
            )

        # ── STEP 2: Early Exit for Skips/Privacy ─────────────
        if app in ['skip']:
            return Context(app='skip', mode='skip', window_title=title, source='window', timestamp=ts)

        # ── STEP 3: Extraction ──────────────────────────────
        visible_text = ''
        url          = ''
        extra        = {}

        if app == 'browser':
            url = ContextHelpers.get_browser_url(window_title=title)
            if url:
                ul = url.lower()
                if 'youtube.com/watch' in ul or 'youtu.be/' in ul:
                    app  = 'youtube'
                    mode = 'video'
                    yt   = ContextHelpers.get_youtube_info(url)
                    if yt:
                        extra        = yt
                        visible_text = (
                            f"YouTube Video: {yt.get('title','')}\n"
                            f"Channel: {yt.get('channel','')}\n"
                            f"Duration: {yt.get('duration',0)//60}m\n"
                            f"Description: {yt.get('description','')[:500]}"
                        )
                elif ul.endswith('.pdf') or '/pdf' in ul:
                    app  = 'pdf'
                    mode = 'document'

        # Force OCR for Editors
        if app == 'editor':
            img = ContextHelpers.capture_active_window_image()
            if img:
                from ocr_engine import extract_text_for_window
                raw = extract_text_for_window(image=img, window_title=title, mode_primary='code')
                visible_text = ContextHelpers.clean_ocr(raw, mode='code')[:3000]
                print(f'[EDITOR] OCR: {len(visible_text)}ch')
            
            # Detect problems in VS Code title
            problem_count = 0
            import re
            prob_match = re.search(r'(\d+)\s+problem', title.lower())
            if prob_match:
                problem_count = int(prob_match.group(1))
                print(f'[EDITOR] Problems detected in title: {problem_count}')
                visible_text = f"[VS Code: {problem_count} problem(s) detected in this file]\n\n{visible_text}"

        elif app == 'youtube' and not visible_text:
            # Standalone YouTube detection fallback
            visible_text = f"YouTube: {title}"

        elif app == 'word':
            full, sel = ContextHelpers.get_word_text()
            visible_text = full
            extra['selection'] = sel

        elif app == 'pdf':
            visible_text = ContextHelpers.get_pdf_text(title)
            if not visible_text:
                img = ContextHelpers.capture_active_window_image()
                if img:
                    from ocr_engine import extract_text_for_window
                    raw = extract_text_for_window(image=img, window_title=title, mode_primary='document')
                    visible_text = ContextHelpers.clean_ocr(raw)[:3000]

        # Generic OCR fallback if still empty
        if not visible_text and app not in ['skip', 'messaging']:
            img = ContextHelpers.capture_active_window_image()
            if img:
                from ocr_engine import extract_text_for_window
                raw = extract_text_for_window(image=img, window_title=title, mode_primary='general')
                visible_text = ContextHelpers.clean_ocr(raw)[:3000]

        return Context(
            app=app, mode=mode,
            window_title=title,
            visible_text=visible_text,
            url=url,
            extra=extra,
            source='window',
            timestamp=ts
        )

    def _from_selection(self, data: dict) -> Context:
        text = data.get('text', '')
        return Context(
            selected_text = text,
            source        = 'selection',
            timestamp     = data.get('timestamp', time.time()),
        )

    def _from_region(self, data: dict) -> Context:
        return Context(
            visible_text = data.get('ocr_text', ''),
            image        = data.get('image'),
            source       = 'region',
            timestamp    = data.get('timestamp', time.time()),
        )




