import sys
import threading
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, Qt
import observer
import ui_overlay
import chat_window
from ocr_engine import extract_text_for_window
from system_observer import SystemObserver, SystemEvent
from context_extractor import ContextExtractor
from context_manager import ContextManager
from ai_engine import AIEngine

from dotenv import load_dotenv
load_dotenv()

# Try importing keyboard, fallback if missing
try:
    import keyboard
except ImportError:
    print("Keyboard library not found. Hotkeys disabled.")
    keyboard = None

class ShortcutListener(QObject):
    activated = pyqtSignal()
    exit_triggered = pyqtSignal()
    pick_triggered = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        
    def start(self):
        if keyboard:
            try:
                # Register Ctrl+Shift+Q to toggle chat
                keyboard.add_hotkey('ctrl+shift+q', self.on_hotkey)
                # Register Ctrl+Shift+E to exit app
                keyboard.add_hotkey('ctrl+shift+e', self.on_exit_hotkey)
                # Register Ctrl+Shift+P to pick element
                keyboard.add_hotkey('ctrl+shift+p', self.on_pick_hotkey)
                print("Global Shortcuts Registered: Ctrl+Shift+Q (Toggle), Ctrl+Shift+E (Exit), Ctrl+Shift+P (Pick)")
            except Exception as e:
                print(f"Failed to register hotkey: {e}")
                
    def on_hotkey(self):
        self.activated.emit()

    def on_exit_hotkey(self):
        self.exit_triggered.emit()

    def on_pick_hotkey(self):
        self.pick_triggered.emit()

class CoraApp(QObject):
    _suggestion_ready_signal = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        
        # Load Icon (Support both png formats just in case)
        self.icon = QIcon("icon.png")
        
        # UI Bubble (Proactive)
        self.bubble = ui_overlay.ProactiveBubble()
        self._picker_instance = None  # keep strong reference to prevent GC
        self._pick_active = False
        
        # State for capture
        self.was_chat_visible = False
        self.was_bubble_visible = False

        # Bubble debouncing
        self._last_bubble_payload_hash = None
        self._bubble_debounce_timer    = QTimer(self)
        self._bubble_debounce_timer.setSingleShot(True)
        self._bubble_debounce_timer.timeout.connect(self._flush_bubble_payload)
        self._pending_bubble_payload   = None

        # Heartbeat state
        self._last_suggestion_window = ""
        self._last_youtube_title     = ""
        self._suggestion_cooldown    = 8.0
        self._last_suggestion_time   = 0

        # UI Chat Window (Reactive)
        self.chat_win = chat_window.ChatWindow()
        self.chat_win.setWindowIcon(self.icon)
        self.chat_win.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        
        self.is_chat_active = False
        
        # Shortcut Handler
        self.shortcut = ShortcutListener()
        self.shortcut.activated.connect(self.toggle_chat_thread_safe)
        self.shortcut.exit_triggered.connect(self.quit_app)
        self.shortcut.start()
        
        # Group Picker Interactions
        self.shortcut.pick_triggered.connect(self.start_pick_to_ask)
        
        # Layers
        self.sys_observer = SystemObserver()
        self.ctx_extractor = ContextExtractor(ocr_engine=extract_text_for_window)
        self.ctx_manager = ContextManager()
        self.ai_engine = AIEngine(model_name="models/gemini-2.5-flash")

        # Grammar engine
        from grammar_engine import GrammarEngine
        self.grammar_engine = GrammarEngine(self.ai_engine)
        self.grammar_engine.set_callback(self._on_grammar_result)
        print("[GRAMMAR] Grammar engine ready")

        # Layer wiring
        self.sys_observer.event_emitted.connect(self._on_system_event)
        self.ai_engine.suggestion_ready.connect(self._on_suggestion_ready)
        self.ai_engine.stream_chunk.connect(self.chat_win.append_stream_chunk)
        self.ai_engine.stream_done.connect(self.chat_win.on_stream_done)
        self.bubble.dismissed.connect(self._on_dismissed)
        self.bubble.ask_cora_clicked.connect(self._on_chip_clicked)
        self.bubble.pick_requested.connect(self.start_pick_to_ask)
        self.chat_win.send_message_signal.connect(self._on_chat_message_sent)
        self.chat_win.stop_signal.connect(self._on_stop_requested)
        self.chat_win.closed_signal.connect(self._on_chat_closed)

        print("CORA: All signals wired.")
        self._suggestion_ready_signal.connect(self._generate_suggestion_for_ctx)

        # Heartbeat timer
        self._obs_timer = QTimer(self.app)
        self._obs_timer.setInterval(8000)
        self._obs_timer.timeout.connect(self._observe_tick)
        
        # System Tray
        self.tray_icon = QSystemTrayIcon(self.icon, self.app)
        self.tray_icon.setToolTip("Cora")
        self.tray_icon.activated.connect(self.on_tray_activate)
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

        # Observer Thread (Legacy kept for now but not used in wiring)
        import observer
        self.observer = observer.Observer()
        
        # Bridge Server (Legacy kept for context engine access)
        import bridge_server
        self.bridge_server = bridge_server.BridgeServer(self.observer.context_engine)
        self.bridge_server.start()

        # Wire VS Code diagnostics
        from bridge_server import set_diagnostics_callback
        set_diagnostics_callback(self._on_vscode_diagnostics)

        # Start components
        self.sys_observer.start()
        QTimer.singleShot(3000, self._start_observation)
        print("CORA: Event-driven pipeline started.")
        # Load initial history
        self.refresh_sessions()
        self.is_chat_active = False
        
        # Show instant startup suggestion
        QTimer.singleShot(1000, self._show_startup_suggestion)

    def _show_startup_suggestion(self):
        """Show immediately on startup without waiting for OCR."""
        import pygetwindow as gw
        try:
            win   = gw.getActiveWindow()
            title = win.title.strip() if win else ''
        except:
            title = ''

        if title:
            self._show_instant_chips(title)
        else:
            payload = {
                "type":        "general",
                "reason":      "CORA is ready",
                "reason_long": "Click Pick to select any text on screen",
                "confidence":  0.9,
                "suggestions": [
                    {"label": "What's on screen?", "hint": "Tell me what's on screen and what would be most helpful"},
                    {"label": "Pick & Ask",         "hint": "Use the Pick button to select any text on screen"},
                ],
            }
            self._show_bubble_debounced(payload)

    def _start_observation(self):
        """Called 3s after launch — starts heartbeat on main thread."""
        self._obs_timer.start()
        print("[OBSERVE] Heartbeat started — interval=8s")
        # Fire first tick immediately
        self._observe_tick()

    def start(self):
        # Observer thread is replaced by CopilotController (already started)
        self.app.exec()

    def on_tray_activate(self, reason):
        self.open_chat()

    def toggle_chat_thread_safe(self):
        self.open_chat()

    def open_chat(self):
        if self.chat_win.isVisible():
            self.chat_win.hide()
            self._on_chat_closed()
        else:
            self.is_chat_active = True
            # Do NOT stop sys_observer — just pause suggestions
            # if hasattr(self, 'sys_observer'):
            #     self.sys_observer.stop()  # REMOVE THIS LINE — causes GC crash
            
            self.chat_win.setWindowFlags(
                Qt.WindowType.Window |
                Qt.WindowType.WindowStaysOnTopHint
            )
            self.chat_win.show()
            self.chat_win.activateWindow()
            self.chat_win.raise_()
            
            # Update Mode Indicator
            ctx = self.ctx_manager.get()
            self.chat_win.update_mode_indicator(ctx.mode)

    def _on_chat_closed(self):
        print("Chat closed — resuming suggestions.")
        # Reset cooldown so next tick fires immediately
        self._last_suggestion_time   = 0
        self._last_suggestion_window = ""
        # Fire observation tick after short delay
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2000, self._observe_tick)

    def _on_chat_sent(self, text, attachment=None):
        """User sent a manual message."""
        ctx = self.ctx_manager.get()
        self.chat_win.set_generating_state(True)
        self.chat_win.on_ai_response_start("…") # Initialize bubble
        self.ai_engine.stream_chat_async(text, ctx, self.chat_win.get_history())

    def _on_stop(self):
        """Stop requested."""
        print("Stop requested.")
        # self.ai_engine needs stop logic if supported

    def _on_system_event(self, event_type: str, event_data: dict):
        # Never process events while chat is open
        if self.chat_win.isVisible():
            return

        from system_observer import SystemEvent
        import time

        if event_type == SystemEvent.WINDOW_CHANGED:
            title  = event_data.get('window_title', '').strip()
            if not title or len(title) < 2:
                return

            tl = title.lower()

            # Silent terminal skip
            terminal_skip = ['python', 'powershell', 'command prompt',
                             'windows terminal', 'cmd']
            if any(k == tl.strip() or tl.strip().startswith(k)
                   for k in terminal_skip):
                return

            hard_skip = [
                'cora ai', 'cora picker', 'cora suggestion',
                'snipping tool', 'task switching', 'task manager',
                'new notification', 'system tray',
                'python', 'powershell', 'command prompt', 'windows terminal',
                # Messaging — privacy (Removed whatsapp, telegram, signal)
                'discord', 'messenger', 'slack', 'skype',
                # NOTE: claude, chatgpt, gemini NOT in skip list anymore
            ]

            # Antigravity skip — only skip planning docs
            # NEVER skip if window has VS Code problem count
            import re as _re_skip
            has_vscode_problem = bool(_re_skip.search(r'\d+\s+problem', tl))

            if has_vscode_problem:
                # Always process — VS Code reported an error
                print(f'[OBSERVE] VS Code error in title — processing: {title[:50]}')
            else:
                antigravity_skip = [
                    'antigravity - implementation',
                    'antigravity - walkthrough',
                    'antigravity - settings',
                    'antigravity - icon',
                    'antigravity - main',
                    'antigravity - .env',
                    'antigravity - journal',
                    'antigravity - readme',
                ]
                if any(k in tl for k in antigravity_skip):
                    print(f'[OBSERVE] Hard skip antigravity: {title[:40]}')
                    return

            if any(k in tl for k in hard_skip):
                print(f'[SWITCH] Hard skip: {title[:40]}')
                return


            import time
            now = time.time()
            if (title == self._last_suggestion_window and
                    now - self._last_suggestion_time < 5.0):
                return
            
            # For YouTube — always refresh even if same base URL
            if 'youtube' in tl:
                # Title changes per video — always re-extract
                self._last_suggestion_window = ''  # force refresh
                self._last_bubble_payload_hash = None

            print(f"[SWITCH] → {title[:60]}")
            self._last_suggestion_window   = title
            self._last_suggestion_time     = now
            self._last_bubble_payload_hash = None # Reset bubble debounce

            # Show chips instantly
            self._show_instant_chips(title)

            # Start URL fetch in background for browser (update chips later)
            if any(k in tl for k in ['chrome', 'edge', 'firefox']):
                import threading
                def _get_url_and_show():
                    import time as t
                    t.sleep(0.3)
                    try:
                        from context_extractor import ContextHelpers
                        url = ContextHelpers.get_browser_url(window_title=title)
                        if url:
                            from PyQt6.QtCore import QTimer
                            QTimer.singleShot(0, lambda: self._show_instant_chips(title, url))
                    except:
                        pass
                threading.Thread(target=_get_url_and_show, daemon=True).start()

            # Fire extraction immediately on switch
            import time
            self.ctx_extractor.extract_async(
                'WINDOW_CHANGED',
                {'window_title': title, 'timestamp': time.time()},
                self._on_context_ready_for_suggestion,
            )

            # Also reset heartbeat timer to fire soon
            if hasattr(self, '_obs_timer'):
                self._obs_timer.start(5000)  # next tick in 5s after switch
            return

        # Region/selection events
        self.ctx_extractor.extract_async(
            event_type,
            event_data,
            self._on_context_ready_for_suggestion,
        )

    def _show_instant_chips(self, title: str, url: str = ''):
        """Show instant chips for the new window while AI processes."""
        from PyQt6.QtCore import QTimer
        import re
        tl = title.lower()

        # YouTube — detect from title
        if 'youtube' in tl or '- youtube' in tl:
            yt_title = re.sub(r'^\(\d+\)\s*', '', title).replace('- YouTube', '').strip()
            chips = [
                {"label": "Summarize Video",   "hint": f"Summarize this YouTube video:\nTitle: {yt_title}"},
                {"label": "About this Song",   "hint": f"Tell me about this song/video:\nTitle: {yt_title}"},
                {"label": "Find Lyrics",       "hint": f"Find or explain the lyrics/content of:\nTitle: {yt_title}"},
            ]
            reason = f"YouTube: {yt_title[:35]}"
        elif any(k in tl for k in ['claude', 'chatgpt', 'gemini', 'copilot', 'perplexity']):
            chips = [
                {
                    "label": "Write a Prompt",
                    "hint":  (
                        "I'm using an AI assistant. Help me write a clear, "
                        "detailed prompt for it. Ask me what I want to accomplish "
                        "and then generate the best possible prompt I can paste."
                    )
                },
                {
                    "label": "Improve My Prompt",
                    "hint":  (
                        "Look at the screen content and find my last message/prompt. "
                        "Rewrite it to be clearer, more specific and likely to get "
                        "a better response from the AI."
                    )
                },
                {
                    "label": "Prompt Templates",
                    "hint":  (
                        "Give me 5 powerful prompt templates I can use right now "
                        "for: coding help, writing improvement, summarization, "
                        "explaining concepts, and creative tasks. "
                        "Format each as a ready-to-paste template."
                    )
                },
            ]
            reason = "Claude — Prompt Assistant"
        elif any(k in tl for k in ['whatsapp', 'telegram', 'signal', 'instagram']):
            chips = [
                {"label": "Reply Suggestion",  "hint": f"Based on this conversation on screen, suggest a good reply:\n\n{{best}}"},
                {"label": "Make Formal",       "hint": f"Rewrite this message to be more formal and professional:\n\n{{best}}"},
                {"label": "Summarize Chat",    "hint": f"Summarize the conversation visible on screen in 2-3 sentences"},
            ]
            reason = "WhatsApp"
        elif any(k in tl for k in ['word', '.docx', 'document', 'writer']):
            chips = [
                {"label": "Fix Grammar",   "hint": "Can you fix the grammar and spelling in this document?"},
                {"label": "Improve",       "hint": "How can I improve the clarity and flow of this text?"},
                {"label": "Summarize",     "hint": "Summarize the document and highlight key takeaways"},
            ]
            reason = "Word Document"
        elif any(k in tl for k in ['chrome', 'edge', 'firefox', 'brave']):
            # Parse page title from window title
            # Chrome format: "Page Title - Google Chrome"
            import re
            page_title = re.sub(
                r'\s*[-–]\s*(Google Chrome|Microsoft Edge|Firefox|Brave).*$',
                '', title, flags=re.I
            ).strip()

            # Detect site from page title
            is_youtube = 'youtube' in tl or 'youtube' in page_title.lower()
            is_new_tab = page_title.lower() in [
                'new tab', 'start page', '', 'google', 'newtab'
            ]
            is_github  = 'github' in page_title.lower()
            is_stack   = 'stack overflow' in page_title.lower()
            is_amazon  = 'amazon' in page_title.lower()
            is_gmail   = 'gmail' in page_title.lower() or 'inbox' in page_title.lower()
            is_docs    = 'google docs' in page_title.lower()
            is_maps    = 'google maps' in page_title.lower() or 'maps' in page_title.lower()

            if is_new_tab or not page_title:
                # New tab — generic helpful chips
                chips = [
                    {"label": "What can CORA do?", "hint": "What can you help me with as a desktop AI assistant?"},
                    {"label": "Productivity Tips", "hint": "Give me 5 quick productivity tips for browsing and working on a computer"},
                ]
                reason = "New Tab"

            elif is_youtube:
                # Handled by YouTube-specific code — skip here
                return

            elif is_github:
                chips = [
                    {"label": "Explain Repo",     "hint": f"Explain what this GitHub project does: {page_title}"},
                    {"label": "Review Code",      "hint": f"Review the code visible on screen from: {page_title}"},
                    {"label": "Common Issues",    "hint": f"What are common issues or improvements for: {page_title}"},
                ]
                reason = f"GitHub: {page_title[:40]}"

            elif is_stack:
                chips = [
                    {"label": "Explain Solution", "hint": f"Explain the solution on this Stack Overflow page: {page_title}"},
                    {"label": "Simplify Answer",  "hint": f"Simplify the answer for: {page_title}"},
                    {"label": "Alternative Fix",  "hint": f"Suggest alternative approaches for: {page_title}"},
                ]
                reason = f"Stack Overflow"

            elif is_amazon:
                chips = [
                    {"label": "Pros & Cons",      "hint": f"Give pros and cons for this product: {page_title}"},
                    {"label": "Is it worth it?",  "hint": f"Should I buy this? Analyze: {page_title}"},
                    {"label": "Alternatives",     "hint": f"Suggest alternatives to: {page_title}"},
                ]
                reason = f"Amazon Product"

            elif is_gmail:
                chips = [
                    {"label": "Draft Reply",      "hint": "Help me write a professional email reply based on the email visible on screen"},
                    {"label": "Summarize Email",  "hint": "Summarize the email visible on screen in 2-3 sentences"},
                    {"label": "Make Formal",      "hint": "Rewrite the email draft on screen to be more professional and formal"},
                ]
                reason = "Gmail"

            elif is_docs:
                chips = [
                    {"label": "Fix Grammar",      "hint": "Fix grammar in the document visible on screen"},
                    {"label": "Improve Writing",  "hint": "Improve the writing quality of the document on screen"},
                    {"label": "Summarize",        "hint": "Summarize the Google Doc visible on screen"},
                ]
                reason = "Google Docs"

            else:
                # Generic article/page
                chips = [
                    {"label": "Summarize Page",   "hint": f"Summarize the content of this page: {page_title}"},
                    {"label": "Key Points",       "hint": f"What are the key points from: {page_title}"},
                    {"label": "Explain Simply",   "hint": f"Explain the main topic of this page simply: {page_title}"},
                ]
                reason = page_title[:45] if page_title else "Browser"

            payload = {
                "type":        "browser",
                "reason":      reason,
                "reason_long": f"Reading {page_title}...",
                "confidence":  0.8,
                "suggestions": chips,
            }
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._show_bubble_debounced(payload))
            return
        elif any(k in tl for k in ['code', 'vscode', '.py', '.js', '.ts']):
            chips = [
                {"label": "Review Code",   "hint": "Review the visible code and suggest improvements"},
                {"label": "Find Bugs",     "hint": "Are there any potential bugs or security issues in this code?"},
                {"label": "Explain Code",  "hint": "Explain how this code works and its logic"},
            ]
            reason = "Code Editor"
        elif '.pdf' in tl:
            chips = [
                {"label": "Summarize PDF", "hint": "Summarize the key information in this PDF"},
                {"label": "Key Points",    "hint": "What are the main points discussed in this document?"},
                {"label": "Explain",       "hint": "Explain the technical concepts mentioned here"},
            ]
            reason = "PDF Document"
        else:
            chips = [
                {"label": "What's on my screen?", "hint": "Look at the screen content and tell me specifically what you see and what would be most helpful right now. Focus on the content, not the UI."},
            ]
            reason = "general"

        payload = {
            "type":        "general",
            "reason":      f"Switched to {reason}",
            "reason_long": "Reading screen content...",
            "confidence":  0.7,
            "suggestions": chips,
        }
        QTimer.singleShot(0, lambda: self._show_bubble_debounced(payload))


    def _observe_tick(self):
        import time
        import pygetwindow as gw

        try:
            win            = gw.getActiveWindow()
            current_window = win.title.strip() if win else ""
        except Exception as e:
            print(f"[OBSERVE] Error: {e}")
            return

        print(f"[OBSERVE] Tick: '{current_window[:60]}'")

        if not current_window or len(current_window.strip()) < 2:
            return

        win_lower = current_window.lower()

        # IMMEDIATE skip for terminals — no logging, no processing
        terminal_skip = [
            'python', 'powershell', 'command prompt',
            'windows terminal', 'cmd', 'terminal',
        ]
        if any(k == win_lower.strip() or win_lower.strip().startswith(k)
               for k in terminal_skip):
            return  # Silent skip — no print

        print(f"[OBSERVE] Tick: '{current_window[:60]}'")

        if not current_window or len(current_window.strip()) < 2:
            print("[OBSERVE] Empty window title — skipping")
            return

        win_lower = current_window.lower()

        # Hard skip list
        hard_skip = [
            'cora ai', 'cora picker', 'cora suggestion',
            'snipping tool', 'task switching', 'task manager',
            'new notification', 'system tray',
            'python',
            'powershell',
            'command prompt',
            'windows terminal',
            # Messaging — privacy (Removed whatsapp, telegram, signal)
            'discord', 'messenger', 'slack', 'skype',
            # NOTE: claude, chatgpt, gemini NOT in skip list anymore
        ]

        # Antigravity skip — only skip planning docs
        # NEVER skip if window has VS Code problem count
        import re as _re_skip
        has_vscode_problem = bool(_re_skip.search(r'\d+\s+problem', win_lower))

        if has_vscode_problem:
            # Always process — VS Code reported an error
            print(f'[OBSERVE] VS Code error in title — processing: {current_window[:50]}')
        else:
            antigravity_skip = [
                'antigravity - implementation',
                'antigravity - walkthrough',
                'antigravity - settings',
                'antigravity - icon',
                'antigravity - main',
                'antigravity - .env',
                'antigravity - journal',
                'antigravity - readme',
            ]
            if any(k in win_lower for k in antigravity_skip):
                print(f'[OBSERVE] Hard skip antigravity: {current_window[:40]}')
                return

        # Skip if chat is open
        if self.chat_win.isVisible():
            print("[OBSERVE] Chat open — skipping")
            return

        now             = time.time()
        window_changed  = (current_window != self._last_suggestion_window)
        cooldown_ok     = (now - self._last_suggestion_time) > self._suggestion_cooldown

        if not window_changed and not cooldown_ok:
            remaining = int(self._suggestion_cooldown - (now - self._last_suggestion_time))
            print(f"[OBSERVE] Cooldown {remaining}s remaining")
            return

        print(f"[OBSERVE] → Extracting context for: {current_window[:50]}")
        self._last_suggestion_window = current_window
        self._last_suggestion_time   = now
        self._last_bubble_payload_hash = None # Reset bubble debounce
        
        # YouTube special — refresh if title changed even within cooldown
        if 'youtube' in win_lower:
            if current_window != self._last_youtube_title:
                self._last_youtube_title     = current_window
                self._last_suggestion_time   = 0
                self._last_suggestion_window = ''
                self._last_bubble_payload_hash = None
                print(f'[YT] New video detected: {current_window[:60]}')

        self.ctx_extractor.extract_async(
            'WINDOW_CHANGED',
            {
                'window_title': current_window,
                'timestamp':    now,
                'use_window_capture': True,  # hint to use window-only capture
            },
            self._on_context_ready_for_suggestion,
        )

        # Ensure heartbeat never stops
        if not self._obs_timer.isActive():
            print('[OBSERVE] Restarting timer')
            self._obs_timer.start()

    def _on_context_ready_for_suggestion(self, ctx):
        print(f"[PIPELINE] Context ready: app={ctx.app} text={len(ctx.best_text())}ch")

        skip_apps = ('skip', 'antigravity')
        if ctx.app in skip_apps:
            return

        # No OCR error detection — VS Code extension handles this
        self._suggestion_ready_signal.emit(ctx)

    def _on_vscode_diagnostics(self, errors: list, count: int, error_text: str):
        """Called when VS Code reports real errors via extension."""
        print(f'[VSCODE] {count} real error(s) received')

        from PyQt6.QtCore import QTimer

        # Build chips with exact error info
        chips = [
            {
                "label": "🔴 Fix Error",
                "hint":  (
                    f"VS Code detected {count} error(s):\n\n"
                    f"{error_text}\n\n"
                    f"Fix these errors and show corrected code."
                )
            },
            {
                "label": "Explain Errors",
                "hint":  (
                    f"Explain these VS Code errors:\n\n{error_text}"
                )
            },
            {
                "label": "Show Fix",
                "hint":  (
                    f"Show me the corrected code to fix:\n\n{error_text}"
                )
            },
        ]

        first_error = errors[0] if errors else {}
        reason = (
            f"🔴 {count} error(s) in "
            f"{first_error.get('file','file')} "
            f"line {first_error.get('line','?')}"
        )

        payload = {
            "type":        "error",
            "reason":      reason,
            "reason_long": error_text[:200],
            "confidence":  1.0,
            "suggestions": chips,
        }

        QTimer.singleShot(0, lambda: self.bubble.show_error_alert(payload))

    def _on_grammar_result(self, result: dict):
        """Handle grammar analysis result — show in bubble."""
        from PyQt6.QtCore import QTimer

        issue_count = result.get('issue_count', 0)
        score       = result.get('score', '?')
        tone        = result.get('tone', 'neutral')
        summary     = result.get('summary', '')
        issues      = result.get('issues', [])
        full_fix    = result.get('full_correction', '')
        original    = result.get('original', '')

        if issue_count == 0:
            # Good writing — show positive feedback
            payload = {
                "type":        "grammar",
                "reason":      f"✅ Writing score: {score}/10 — {tone} tone",
                "reason_long": "No grammar issues found",
                "confidence":  0.9,
                "suggestions": [
                    {
                        "label": "Improve Style",
                        "hint":  f"Suggest style improvements for this text:\n\n{original[:2000]}"
                    },
                    {
                        "label": "Check Tone",
                        "hint":  f"Analyze the tone and suggest adjustments:\n\n{original[:2000]}"
                    },
                ],
            }
            QTimer.singleShot(0, lambda: self._show_bubble_debounced(payload))
            return

        # Build issue-specific chips
        chips = []

        # Always add full correction chip first
        if full_fix and full_fix != original:
            chips.append({
                "label": f"✏️ Fix All ({issue_count})",
                "hint":  (
                    f"Here is the corrected version of the text. "
                    f"Issues found: {summary}\n\n"
                    f"ORIGINAL:\n{original[:1000]}\n\n"
                    f"CORRECTED:\n{full_fix[:1000]}"
                )
            })

        # Add individual issue chips
        for i, issue in enumerate(issues[:2]):
            chips.append({
                "label": f"Fix: {issue['issue'][:20]}...",
                "hint":  (
                    f"Grammar issue: {issue['issue']}\n"
                    f"Fix: {issue['fix']}\n"
                    f"Reason: {issue['reason']}\n\n"
                    f"Full corrected text:\n{full_fix[:1500]}"
                )
            })

        # Word choice chip
        chips.append({
            "label": "Better Words",
            "hint":  f"Suggest better word choices for:\n\n{original[:1500]}"
        })

        tone_emoji = {
            'formal':   '👔',
            'informal': '😊',
            'neutral':  '📝',
        }.get(tone.lower(), '📝')

        payload = {
            "type":        "grammar",
            "reason":      f"📝 {issue_count} issue(s) — Score {score}/10",
            "reason_long": summary,
            "confidence":  0.95,
            "suggestions": chips[:4],
        }

        QTimer.singleShot(0, lambda: self._show_bubble_debounced(payload))

        # Pulse orb for grammar issues
        if issue_count >= 2:
            QTimer.singleShot(50, lambda: self.bubble._set_state(
                self.bubble.STATE_PULSING
            ))

    def _generate_suggestion_for_ctx(self, ctx):
        # Skip AI/Antigravity windows
        if ctx.app in ('skip', 'antigravity'):
            print(f'[PIPELINE] Skipping app={ctx.app}')
            return
        # If chat open, no proactive hits
        if self.chat_win.isVisible(): 
            return

        best = ctx.best_text()
        if not best and ctx.window_title:
            ctx.visible_text = f"Active window: {ctx.window_title}"
            best = ctx.visible_text

        if not best:
            return

        # Don't call AI for tiny context — not enough to work with
        if len(best.strip()) < 30 and ctx.app == 'general':
            print(f'[SUGGEST] Too little context ({len(best)}ch) — skipping')
            return

        print(f'[SUGGEST] ✓ Calling AI for app={ctx.app} text={len(best)}ch')
        self.ctx_manager.update(ctx)

        # Grammar check ONLY for word and messaging — never general/editor/terminal
        if ctx.app == 'word':
            self.grammar_engine.check_text(best, source='word')
        elif ctx.app == 'messaging':
            tl = ctx.window_title.lower()
            if any(k in tl for k in ['whatsapp', 'telegram']):
                self.grammar_engine.check_text(best, source='whatsapp')

        # Update bubble with real context-aware chips
        self._update_bubble_chips(ctx, best)
        self.ai_engine.generate_suggestion_async(ctx)

        # FIX 7: Always regenerate YouTube chips — video may have changed
        if ctx.app == 'youtube':
            self._last_suggestion_time = 0

    def _update_bubble_chips(self, ctx, best: str):
        from PyQt6.QtCore import QTimer

        app = ctx.app
        win = ctx.window_title

        if app == 'word':
            chips = [
                {
                    "label": "Fix Grammar",
                    "hint":  f"Fix all grammar and spelling errors:\n\n{best[:2000]}"
                },
                {
                    "label": "Improve Writing",
                    "hint":  f"Improve clarity and style:\n\n{best[:2000]}"
                },
                {
                    "label": "📝 Grammar Check",
                    "hint":  "__GRAMMAR_CHECK__"
                },
                {
                    "label": "Simplify",
                    "hint":  f"Simplify complex sentences in:\n\n{best[:2000]}"
                },
            ]
            reason = "Word Document — ready"

        elif app in ('editor', 'general'):
            error_kw = [
                'error', 'exception', 'traceback', 'syntaxerror',
                'nameerror', 'typeerror', 'attributeerror', 'importerror',
                'valueerror', 'keyerror', 'indexerror', 'runtimeerror',
                'failed', 'undefined', 'cannot', 'unexpected indent',
                'line ', 'col ', '^^^', '~~~', 'errno',
            ]
            best_lower = best.lower()
            error_count = sum(1 for k in error_kw if k in best_lower)
            has_error   = error_count >= 2  # at least 2 error keywords

            if has_error:
                chips = [
                    {"label": "🔴 Fix Error",    "hint": f"Fix this error and show the corrected code:\n\n{best[:2000]}"},
                    {"label": "Explain Error",   "hint": f"Explain what caused this error:\n\n{best[:2000]}"},
                    {"label": "Find Solution",   "hint": f"Suggest a complete solution for:\n\n{best[:2000]}"},
                ]
                reason = "🔴 Error detected — click to fix"

                # Trigger red orb alert
                payload = {
                    "type":        "error",
                    "reason":      reason,
                    "reason_long": best[:120],
                    "confidence":  0.99,
                    "suggestions": chips,
                }
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.bubble.show_error_alert(payload))
                # Also update context manager
                self.ctx_manager.update(ctx)
                self.ai_engine.generate_suggestion_async(ctx)
                return  # skip normal bubble update

            else:
                chips = [
                    {"label": "Explain Code",   "hint": f"Explain what this code does:\n\n{best[:2000]}"},
                    {"label": "Find Bugs",      "hint": f"Find any bugs in this code:\n\n{best[:2000]}"},
                    {"label": "Optimize",       "hint": f"Suggest optimizations:\n\n{best[:2000]}"},
                ]
                reason = "Code ready to review"

        elif app == 'pdf':
            chips = [
                {"label": "Summarize PDF",   "hint": f"Summarize this PDF content in bullet points:\n\n{best[:2000]}"},
                {"label": "Key Points",      "hint": f"Extract the key points from this PDF:\n\n{best[:2000]}"},
                {"label": "Explain Section", "hint": f"Explain this section in simple terms:\n\n{best[:2000]}"},
            ]
            reason = "PDF — ready to analyze"

        elif app == 'ai_chat':
            win_lower = ctx.window_title.lower()
            ai_name   = 'Claude' if 'claude' in win_lower else \
                         'ChatGPT' if 'chatgpt' in win_lower else \
                         'Gemini' if 'gemini' in win_lower else 'AI'
            chips = [
                {
                    "label": "Write a Prompt",
                    "hint":  (
                        f"I'm using {ai_name}. Help me write a perfect prompt. "
                        f"Generate a detailed, specific prompt I can paste into {ai_name} "
                        f"based on what I'm trying to accomplish. "
                        f"Ask me clarifying questions if needed, then give the prompt."
                    )
                },
                {
                    "label": "Improve My Prompt",
                    "hint":  (
                        f"I'm about to send a prompt to {ai_name}. "
                        f"Here is the screen content:\n\n{best[:1000] if best else 'No screen content captured'}\n\n"
                        f"Find my last message or prompt intention and rewrite it "
                        f"to be more specific, clear and effective."
                    )
                },
                {
                    "label": "Prompt Templates",
                    "hint":  (
                        f"Give me 5 powerful ready-to-paste prompt templates for {ai_name}:\n"
                        f"1. For coding and debugging\n"
                        f"2. For writing and editing\n"
                        f"3. For summarizing long content\n"
                        f"4. For explaining complex topics simply\n"
                        f"5. For creative and brainstorming tasks\n"
                        f"Format each template with [PLACEHOLDERS] for customization."
                    )
                },
            ]
            reason = f"{ai_name} — Prompt Engineer"

            payload = {
                "type":        "ai_chat",
                "reason":      reason,
                "reason_long": f"CORA can help you write better prompts for {ai_name}",
                "confidence":  0.95,
                "suggestions": chips,
            }
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._show_bubble_debounced(payload))
            return

        elif app == 'messaging':
            tl = ctx.window_title.lower()
            platform = (
                'WhatsApp' if 'whatsapp' in tl else
                'Telegram' if 'telegram' in tl else
                'Instagram' if 'instagram' in tl else
                'Messenger'
            )
            chips = [
                {
                    "label": "Fix Grammar",
                    "hint":  f"Fix grammar in this {platform} message:\n\n{best[:1000]}"
                },
                {
                    "label": "Better Reply",
                    "hint":  f"Suggest a better reply for this conversation:\n\n{best[:1000]}"
                },
                {
                    "label": "📝 Check Writing",
                    "hint":  "__GRAMMAR_CHECK__"
                },
                {
                    "label": "Make Formal",
                    "hint":  f"Make this message more professional:\n\n{best[:1000]}"
                },
            ]
            reason = f"{platform} — writing assistant"

            payload = {
                "type":        "messaging",
                "reason":      reason,
                "reason_long": best[:100],
                "confidence":  0.9,
                "suggestions": chips,
            }
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._show_bubble_debounced(payload))
            return

        elif app in ('youtube', 'browser'):
            extra    = getattr(ctx, 'extra', {}) or {}
            yt_title = extra.get('title', '')
            url      = getattr(ctx, 'url', '') or ''
            
            # Fallback to window title for Shorts
            if not yt_title and ctx.window_title:
                import re
                yt_title = re.sub(
                    r'\s*[-–]\s*(YouTube|Watch|Shorts).*$', '',
                    ctx.window_title, flags=re.I
                ).strip()
                
            is_short = 'shorts' in url.lower() or 'shorts' in ctx.window_title.lower()
            
            if is_short:
                chips = [
                    {"label": "About this Short", "hint": f"Tell me about this YouTube Short: {yt_title}"},
                    {"label": "Related Topics",   "hint": f"What topics are related to: {yt_title}"},
                    {"label": "Key Facts",        "hint": f"Give me key facts about the topic: {yt_title}"},
                ]
                reason = f"▶ Short: {yt_title[:40]}" if yt_title else "YouTube Short"
            else:
                yt_desc = extra.get('description', '')[:800] if extra else ''
                yt_chan  = extra.get('channel', '') if extra else ''
                yt_dur  = extra.get('duration', 0) if extra else 0
                chips = [
                    {"label": "Summarize Video",  "hint": f"Summarize this YouTube video:\nTitle: {yt_title}\nChannel: {yt_chan}\nDescription: {yt_desc}"},
                    {"label": f"About {yt_chan[:15]}" if yt_chan else "About Channel", "hint": f"Tell me about {yt_chan} — their content, style and background"},
                    {"label": "Key Topics",       "hint": f"What key topics are covered in: {yt_title}"},
                ]
                reason = f"▶ {yt_title[:45]}" if yt_title else "YouTube Video"

        elif app == 'explorer':
            chips = [
                {"label": "Find Files",      "hint": f"Help me find files in this folder based on what's visible:\n\n{best[:1000]}"},
                {"label": "Organize Tips",   "hint": f"Give tips for organizing the files visible on screen:\n\n{best[:1000]}"},
                {"label": "Explain Files",   "hint": f"What are the files/folders visible on screen used for?\n\n{best[:1000]}"},
            ]
            reason = "File Explorer"

        elif app == 'settings':
            chips = [
                {"label": "Explain Setting", "hint": f"Explain what the settings visible on screen do:\n\n{best[:1000]}"},
                {"label": "Recommend",       "hint": f"What do you recommend for these settings:\n\n{best[:1000]}"},
                {"label": "Troubleshoot",    "hint": f"Help troubleshoot based on these settings:\n\n{best[:1000]}"},
            ]
            reason = "Windows Settings"

        else:
            chips = [
                {"label": "What's on screen?", "hint": f"Tell me what's on screen and what would be most helpful:\n\n{best[:1000]}"},
                {"label": "Summarize",         "hint": f"Summarize what's visible on screen:\n\n{best[:1000]}"},
            ]
            reason = win[:40] if win else "Current Window"

        payload = {
            "type":        app,
            "reason":      reason,
            "reason_long": best[:120],
            "confidence":  0.95,
            "suggestions": chips,
        }
        QTimer.singleShot(0, lambda: self._show_bubble_debounced(payload))

    def _show_bubble_debounced(self, payload: dict):
        """Only show bubble if payload changed. Debounce 400ms."""
        import hashlib, json
        try:
            h = hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        except Exception:
            h = str(payload.get('reason', ''))

        if h == self._last_bubble_payload_hash:
            return  # Same payload — don't flicker

        self._last_bubble_payload_hash = h
        self._pending_bubble_payload   = payload
        self._bubble_debounce_timer.start(400)

    def _flush_bubble_payload(self):
        if self._pending_bubble_payload:
            self.bubble.show_suggestion(self._pending_bubble_payload)
            self._pending_bubble_payload = None

    def _on_suggestion_ready(self, payload: dict):
        """Layer 4 → Layer 5: Show suggestion in UI."""
        print(f"[UI] Suggestion ready: {payload.get('reason','')[:50]}")
        if self.chat_win.isVisible(): 
            return
        self._show_bubble_debounced(payload)

    def _on_dismissed(self):
        """User dismissed — clear region context."""
        print('[BUBBLE] Dismissed — resetting cooldown')
        self._last_suggestion_time   = 0
        self._last_suggestion_window = ''
        self._last_bubble_payload_hash = None
        if hasattr(self, 'ctx_manager'):
            self.ctx_manager.clear_region()

    def _on_chip_clicked(self, label: str, hint: str):
        # Special grammar check action
        if hint == '__GRAMMAR_CHECK__':
            ctx = self.ctx_manager.get()
            best = ctx.best_text() if ctx else ''
            if best:
                print('[GRAMMAR] On-demand check triggered')
                source = 'word' if ctx.app == 'word' else 'ocr'
                self.grammar_engine.check_on_demand(best, source)
                # Show loading state in bubble
                loading_payload = {
                    "type":        "grammar",
                    "reason":      "📝 Checking grammar...",
                    "reason_long": "Analyzing your text",
                    "confidence":  0.5,
                    "suggestions": [
                        {"label": "Analyzing...", "hint": "Please wait"}
                    ],
                }
                self._show_bubble_debounced(loading_payload)
            return

        ctx = self.ctx_manager.get()
        print(f'[CHIP] label={label} hint_len={len(hint)}ch app={ctx.app if ctx else "none"}')

        # Show chat window
        self.chat_win.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.chat_win.show()
        self.chat_win.raise_()
        self.chat_win.activateWindow()

        if hasattr(self.chat_win, 'set_context'):
            self.chat_win.set_context(ctx)

        # Add user message
        if hasattr(self.chat_win, 'add_user_message'):
            self.chat_win.add_user_message(label)
        elif hasattr(self.chat_win, 'chat_display'):
            self.chat_win.chat_display.add_message(label, is_user=True)

        self.chat_win.set_generating_state(True)

        # hint already contains the content for picker chips
        # Only inject screen context for non-picker chips
        if ctx and ctx.source == 'region':
            # Picker — hint already has the text embedded
            full_hint = hint
        else:
            # Window context — inject screen text
            best = ctx.best_text() if ctx else ''
            url  = getattr(ctx, 'url', '') if ctx else ''
            if best and best not in hint:
                full_hint = f"{hint}\n\nSCREEN CONTENT:\n{best[:3000]}"
            elif url and url not in hint:
                full_hint = f"{hint}\n\nURL: {url}"
            else:
                full_hint = hint

        print(f'[CHIP] Sending: {full_hint[:80]}...')

        # Delay 300ms so chat window fully renders before stream starts
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(300, lambda: self.ai_engine.stream_chat_async(
            full_hint, ctx, []
        ))

    def _process_chat(self, text, attachment=None, proactive_context=None):
        print("Processing chat in background (Streaming)...")
        
        # Legacy _process_chat kept for backward compatibility if needed, 
        # but UI triggers now use self.ai_engine.stream_chat_async
        self.chat_win.ai_response_signal.emit("") 
        
        # ... legacy streaming logic omitted for brevity as it's replaced by AI Engine ...

    # reset_chat removed (replaced by handle_new_chat)

    def handle_new_chat(self):
        print("Creating new session...")
        self.observer.create_new_session()
        # Clear UI without re-emitting signal
        self.chat_win.chat_display.clear()
        self.refresh_sessions()

    def handle_switch_session(self, session_id):
        print(f"Switching session: {session_id}")
        if self.observer.switch_session(session_id):
            # Reload UI
            self.chat_win.chat_display.clear()
            
            # Replay History
            for msg in self.observer.chat_history:
                role = "Cora" if msg['role'] == 'assistant' else "You"
                is_user = (role == "You")
                content = msg.get('content', '')
                if is_user:
                     if "USER:" in content:
                          content = content.split("USER:")[-1].strip()
                self.chat_win.append_message(role, content, is_user)
            self.refresh_sessions()

    def handle_delete_session(self, session_id):
        print(f"Deleting session: {session_id}")
        if self.observer.delete_session(session_id):
            if self.observer.current_session_id == session_id:
                self.chat_win.start_new_chat()
            self.refresh_sessions()

    def refresh_sessions(self):
        sessions = self.observer.get_sessions()
        self.chat_win.load_sessions(sessions)

    def on_suggestion(self, payload):
        print(f"Proactive Suggestion: {payload.get('reason')}")

        # Pass the full payload to the bubble to render
        self._show_bubble_debounced(payload)

    def _on_suggestion_ready(self, data: dict):
        if self._pick_active:
            print('[SUGGEST] Pick active — blocking AI overwrite')
            return
        print(f"[UI] Suggestion ready: {data.get('reason','')[:50]}")
        self._show_bubble_debounced(data)

    def _on_dismissed(self):
        print('[BUBBLE] Dismissed — resetting cooldown')
        self._pick_active              = False
        self._last_suggestion_time     = 0
        self._last_suggestion_window   = ''
        self._last_bubble_payload_hash = None

    def show_last_hint(self):
        self.bubble.show_message(self.last_title, self.last_details)

    def handle_overlay_action(self, user_text, internal_prompt):
        print(f"Overlay Action: {user_text}")

        proactive_ctx = self.copilot.last_proactive_context or {}
        reason = proactive_ctx.get("reason", "")
        app_type = proactive_ctx.get("mode_primary", "general")
        self.chat_win.update_mode_indicator(app_type, reason=reason)

        # 1. Force Open Chat Window First (Avoid toggling closed if already open)
        if not self.chat_win.isVisible():
             self.open_chat()
        else:
             self.chat_win.activateWindow()
             self.chat_win.raise_()

        # Special Case: Welcome
        # If the ID was "welcome" or prompt is empty, we stop here (Open Chat is done)
        if internal_prompt == "welcome" or internal_prompt == "":
            return

        # 2. Add clean USER FRIENDLY message to UI (hide prompt details)
        self.chat_win.add_user(user_text)
        
        # 3. Grab stored proactive context for grounded chat (FIX 7)
        proactive_ctx = None
        if hasattr(self, 'copilot') and self.copilot.last_proactive_context:
            proactive_ctx = self.copilot.last_proactive_context
            print(f"Grounding chat with proactive context: mode={proactive_ctx.get('mode_primary')}")
        
        # 4. Process the INTERNAL PROMPT in background
        # FORCE BUTTON UPDATE
        self.chat_win.set_generating_state(True)
        t = threading.Thread(target=self._process_chat, args=(internal_prompt,), kwargs={'proactive_context': proactive_ctx})
        t.start()

    def hide_ui_for_capture(self):
        # Store state
        self.was_chat_visible = self.chat_win.isVisible()
        # In new UI, bubble itself is the widget
        self.was_bubble_visible = self.bubble.isVisible()
        
        # Hide logic
        # if self.was_chat_visible:
        #    self.chat_win.hide()  <-- CAUSING BLINKING. User prefers it content visible.
        if self.was_bubble_visible:
            self.bubble.hide()
            
    def restore_ui_after_capture(self):
        # Restore state
        if self.was_chat_visible:
            # self.chat_win.show()
            pass
        if self.was_bubble_visible:
            self.bubble.show()

    def quit_app(self):
        self.observer.stop()
        self.app.quit()

    def start_pick_to_ask(self):
        print('[PICK] start_pick_to_ask called')
        # Do NOT set _pick_active here — only set it when region is received
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(200, self._launch_picker_delayed)

    def _launch_picker_delayed(self):
        from screen_picker import ScreenPicker
        picker = ScreenPicker(None)
        picker.region_selected.connect(self.on_region_picked)
        picker.cancelled.connect(self.on_pick_cancelled)
        self._picker_instance = picker  # strong reference
        picker.showFullScreen()
        print('[PICK] Picker shown')

    def on_pick_cancelled(self):
        print('[PICK] Cancelled')
        self._picker_instance = None
        self._pick_active     = False

    def on_region_picked(self, x: int, y: int, image_bytes: bytes, ocr_text: str):
        print(f'[PICK] ✓ on_region_picked called: ocr={len(ocr_text)}ch')

        import time
        import re
        from context_extractor import Context

        # Build context from picked region
        ctx = Context(
            app          = 'region',
            mode         = 'general',
            window_title = self._last_suggestion_window or '',
            visible_text = ocr_text,
            image        = image_bytes,
            source       = 'region',
            timestamp    = time.time(),
        )
        self.ctx_manager.update(ctx)

        # Detect content type and build chips
        text       = ocr_text.strip()
        words      = [w for w in text.split() if len(w) > 1]
        word_count = len(words)
        char_count = len(text)

        # Tighter error detection for picker
        is_error = any(k in text.lower() for k in [
            'traceback', 'syntaxerror', 'nameerror', 'typeerror',
            'attributeerror', 'exception occurred', 'errno',
            'error:', 'fatal error',
        ]) and not any(k in ctx.window_title.lower() for k in [
            'word', 'document', 'whatsapp', 'telegram'
        ])
        is_code = any(k in text for k in [
            'def ', 'class ', 'import ', 'function',
            'const ', 'var ', 'let ', '{', '}', '=>',
            'if (', 'for (', 'while (',
        ]) or bool(re.search(r'^\s{2,}', text, re.MULTILINE))

        is_url = text.startswith('http') or text.startswith('www.')

        is_single_word = (
            word_count == 1 or
            (word_count <= 2 and char_count <= 20)
        )
        is_sentence = 4 <= word_count <= 30
        is_paragraph = word_count > 30

        # Build chips based on content
        if is_error:
            chips = [
                {"label": "🔴 Fix Error",    "hint": f"Fix this error and show corrected code:\n\n{text}"},
                {"label": "Explain Cause",   "hint": f"Explain what caused this error:\n\n{text}"},
                {"label": "Find Solution",   "hint": f"Suggest a complete solution for:\n\n{text}"},
            ]
            content_type = 'error'

        elif is_code:
            chips = [
                {"label": "Explain Code",   "hint": f"Explain what this code does:\n\n{text}"},
                {"label": "Find Bugs",      "hint": f"Find bugs in this code:\n\n{text}"},
                {"label": "Optimize",       "hint": f"Optimize this code:\n\n{text}"},
                {"label": "Add Comments",   "hint": f"Add helpful comments to:\n\n{text}"},
            ]
            content_type = 'code'

        elif is_single_word:
            word = words[0] if words else text
            chips = [
                {"label": "Define",          "hint": f"Give a clear definition of the word '{word}' with examples"},
                {"label": "Synonyms",        "hint": f"Give 8-10 synonyms for '{word}' with brief meaning differences"},
                {"label": "Use in Sentence", "hint": f"Show 3 example sentences using the word '{word}' in different contexts"},
                {"label": "Etymology",       "hint": f"What is the origin and etymology of the word '{word}'?"},
            ]
            content_type = 'word'

        elif is_sentence:
            chips = [
                {"label": "Fix Grammar",    "hint": f"Fix grammar and show corrected version:\n{text}"},
                {"label": "Rewrite Better", "hint": f"Rewrite this more clearly and professionally:\n{text}"},
                {"label": "Make Formal",    "hint": f"Make this more formal:\n{text}"},
                {"label": "Explain",        "hint": f"Explain what this means:\n{text}"},
            ]
            content_type = 'sentence'

        elif is_paragraph:
            chips = [
                {"label": "Summarize",      "hint": f"Summarize in 2-3 sentences:\n\n{text}"},
                {"label": "Improve",        "hint": f"Improve clarity and flow of:\n\n{text}"},
                {"label": "Fix Grammar",    "hint": f"Fix all grammar issues in:\n\n{text}"},
                {"label": "Make Formal",    "hint": f"Make this more formal and professional:\n\n{text}"},
            ]
            content_type = 'paragraph'

        elif is_url:
            chips = [
                {"label": "What is this?",  "hint": f"What is this URL/website: {text}"},
                {"label": "Summarize Site", "hint": f"Describe what this website is about: {text}"},
            ]
            content_type = 'url'

        else:
            chips = [
                {"label": "Explain",        "hint": f"Explain this: {text}"},
                {"label": "Summarize",      "hint": f"Summarize this: {text}"},
            ]
            content_type = 'general'

        print(f"[PICK] type={content_type} words={word_count} chips={len(chips)}")

        preview = text[:50] + "..." if len(text) > 50 else text
        payload = {
            "type":        content_type,
            "reason":      f"📌 {preview}" if text else "Region selected",
            "reason_long": text[:200],
            "confidence":  0.98,
            "suggestions": chips,
        }

        # Set pick lock AFTER building payload — prevent AI from overwriting
        self._pick_active              = True
        self._last_bubble_payload_hash = None

        print(f'[PICK] Showing payload: {payload["reason"][:50]}')

        # Show bubble directly — bypass all debouncing
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0,   lambda: self.bubble.show_suggestion(payload))
        QTimer.singleShot(50,  lambda: self.bubble.show())
        QTimer.singleShot(100, lambda: self.bubble.raise_())

        # Clear lock after 30s
        QTimer.singleShot(30000, self._clear_pick_lock)

    def _clear_pick_lock(self):
        self._pick_active = False
        print('[PICK] Lock cleared — resuming proactive suggestions')

    def on_pick_cancelled(self):
        print("Pick to Ask: Cancelled.")
        self.bubble.show()


    def _on_chat_message_sent(self, text: str, attachment):
        if not text.strip() and not attachment:
            return

        ctx     = self.ctx_manager.get()
        history = self.chat_win.get_history()
        self.chat_win.set_generating_state(True)

        if attachment:
            import threading
            def _send_with_attachment():
                enriched = self._read_attachment(attachment, text)
                self.ai_engine.stream_chat_async(enriched, ctx, history)
            threading.Thread(target=_send_with_attachment, daemon=True).start()
        else:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(300, lambda:
                self.ai_engine.stream_chat_async(text, ctx, history)
            )

    def _read_attachment(self, file_path: str, user_message: str) -> str:
        import os
        ext = os.path.splitext(file_path)[1].lower()
        content = ''
        try:
            if ext == '.pdf':
                import fitz
                doc  = fitz.open(file_path)
                text = ''
                for i in range(min(10, len(doc))):
                    text += doc[i].get_text()
                content = text[:6000]
            elif ext in ('.txt', '.md', '.py', '.js', '.ts', '.html', '.css', '.json'):
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()[:6000]
                print(f"[ATTACH] Text file read: {len(content)}ch")

            elif ext in ('.docx',):
                try:
                    import docx
                    doc     = docx.Document(file_path)
                    content = '\n'.join([p.text for p in doc.paragraphs])[:6000]
                    print(f"[ATTACH] DOCX read: {len(content)}ch")
                except ImportError:
                    content = f"[Word document attached: {os.path.basename(file_path)}]"

            elif ext in ('.png', '.jpg', '.jpeg', '.webp'):
                # Image — handled via vision if passed to ai_engine
                # For now, just pass the message — vision logic exists in AIEngine._stream_llm
                return user_message

        except Exception as e:
            print(f"[ATTACH] Error reading {file_path}: {e}")
            content = f"[Could not read file: {os.path.basename(file_path)}]"

        if content:
            filename = os.path.basename(file_path)
            return (
                f"FILE: {filename}\n"
                f"{'='*50}\n"
                f"{content}\n"
                f"{'='*50}\n\n"
                f"USER REQUEST: {user_message or 'Please analyze this file.'}"
            )
        return user_message or "Please analyze the attached file."

    def _on_stop_requested(self):
        # Signal AI engine to stop (implement if needed)
        print("[UI] Stop generated requested.")
        self.ai_engine.stop_stream()
        self.chat_win.on_stream_done()

if __name__ == "__main__":
    cora = CoraApp()
    cora.start()
