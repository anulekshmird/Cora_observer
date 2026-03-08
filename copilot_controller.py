import time
import json
import os
import hashlib
from PyQt6.QtCore import QThread, pyqtSignal

import config


class CopilotController(QThread):
    def __init__(self, context_engine, observer, overlay):
        super().__init__()
        self.context_engine = context_engine
        self.observer       = observer
        self.overlay        = overlay
        self.running        = False
        self.paused         = False

        self.last_error_signature  = None
        self.last_visual_sig       = None
        self.loop_count            = 0
        self.last_llm_call_time    = 0

        self.dismissed_signatures  = set()
        self.snoozed_until         = 0.0

        self.overlay.dismissed.connect(self.on_user_dismissed)
        self.overlay.snoozed.connect(self.on_user_snoozed)

        self.last_active_window      = None
        self.last_writing_check_time = 0
        self.last_doc_check_time     = 0

        self.last_proactive_context  = None
        self.last_ocr_text_cache     = ""
        self.last_screen_hash        = None

        self.last_switch_time        = time.time()
        self.window_focus_start      = time.time()
        self.presence_message_shown  = False
        self.last_error_time         = 0.0

        self.last_suggestion_time    = time.time()
        self.last_presence_time      = 0.0
        self.last_suggestion_sig     = None
        self.analysis_cooldown       = 0

    # ------------------------------------------------------------------
    # User interaction callbacks
    # ------------------------------------------------------------------

    def on_user_dismissed(self):
        if self.last_error_signature:
            self.dismissed_signatures.add(self.last_error_signature)
        if self.last_visual_sig:
            self.dismissed_signatures.add(self.last_visual_sig)

    def on_user_snoozed(self, mins):
        self.snoozed_until = time.time() + (mins * 60)
        print(f"Copilot: Snoozed for {mins} minutes.")

    def pause(self):
        self.paused = True
        print("Copilot Controller: Paused.")

    def resume(self):
        self.paused = False
        print("Copilot Controller: Resumed.")

    def run(self):
        self.start_proactive_loop()

    # ------------------------------------------------------------------
    # Main proactive loop
    # ------------------------------------------------------------------

    def start_proactive_loop(self):
        self.running = True
        self.paused  = False
        print("Copilot Controller: Proactive Loop Started.")

        while self.running:
            try:
                if self.paused:
                    time.sleep(0.5)
                    continue

                if time.time() < self.snoozed_until:
                    time.sleep(2)
                    continue

                # ── Snapshot ──────────────────────────────────────────
                snapshot       = self.context_engine.get_context_snapshot()
                current_window = snapshot.get('window_title', '')
                mode_primary   = snapshot.get('mode_primary', 'general')
                mode_secondary = snapshot.get('mode_secondary', 'unknown')
                page_title     = snapshot.get('page_title', '')   # e.g. "BLIND FOOD CHALLENGE"
                site_name      = snapshot.get('site_name', '')    # e.g. "YouTube"
                browser_name   = snapshot.get('browser_name', '') # e.g. "Google Chrome"
                idle_time      = self.context_engine.get_idle_time()

                if self.loop_count % 5 == 0:
                    print(f"Copilot Pulse: Mode=[{mode_primary}/{mode_secondary}] "
                          f"Idle=[{idle_time:.1f}s] Window=[{current_window}]")

                # ── Internal / system guard ───────────────────────────
                if mode_primary == "internal":
                    time.sleep(0.5)
                    continue

                cw_lower = (current_window or "").lower()
                if any(kw in cw_lower for kw in ["cora suggestion", "cora ai"]):
                    time.sleep(1.0)
                    continue

                # ── Clear stale error state ───────────────────────────
                if not snapshot.get("error") and self.last_error_signature:
                    print("Copilot: Error resolved, clearing state.")
                    self.last_error_signature = None
                    self.last_visual_sig      = None
                    self.dismissed_signatures.clear()

                # ── Window switch — clear stale suggestion ────────────
                if current_window != self.last_active_window:
                    print(f"Copilot: Window changed → {current_window[:60]}")
                    self.last_active_window     = current_window
                    self.last_suggestion_sig    = None
                    self.last_visual_sig        = None
                    self.presence_message_shown = False
                    self.dismissed_signatures.clear()   # ← fresh start for new window
                    try:
                        self.overlay.hide_bubble()
                    except Exception:
                        pass
                    time.sleep(0.5)
                    continue

                # ── OCR change detection ──────────────────────────────
                current_ocr  = getattr(self.observer, 'last_ocr_text', '')
                current_hash = hashlib.md5(current_ocr.encode()).hexdigest() if current_ocr else None
                if current_hash != self.last_ocr_text_cache:
                    print("Copilot: Context change detected.")
                    self.dismissed_signatures.clear()
                    self.last_ocr_text_cache = current_hash

                # ── Idle threshold — relax for video/youtube ──────────
                idle_required = 0.3 if mode_primary in ('video', 'youtube') else 0.8
                if idle_time < idle_required:
                    time.sleep(0.2)
                    continue

                time_since_switch          = time.time() - self.last_switch_time
                time_since_last_suggestion = time.time() - self.last_suggestion_time
                suggestion_triggered       = False

                # ── Classify window ───────────────────────────────────
                win_lower  = current_window.lower()
                is_youtube = "youtube" in win_lower or site_name.lower() == "youtube"
                # Strict word match — must be a doc app, not just containing "word"
                is_word  = (
                    not is_youtube and (
                        "microsoft word" in win_lower
                        or win_lower.endswith(".docx")
                        or " - word" in win_lower
                        or "word - " in win_lower
                        or ("compatibility mode" in win_lower and "word" in win_lower)
                    )
                )
                is_excel = (
                    not is_youtube and (
                        "microsoft excel" in win_lower
                        or win_lower.endswith(".xlsx")
                        or " - excel" in win_lower
                    )
                )
                is_pdf   = (
                    not is_youtube and (
                        win_lower.endswith(".pdf")
                        or "adobe acrobat" in win_lower
                        or "foxit" in win_lower
                        or "pdf reader" in win_lower
                    )
                )
                is_browser = (
                    any(x in win_lower for x in ["- google chrome", "- mozilla firefox",
                                                  "- microsoft edge", "- brave"])
                    and not is_youtube
                    and site_name.lower() not in ("youtube", "netflix", "twitch")
                )

                # ── P1: Error suggestions ─────────────────────────────
                if snapshot.get("error"):
                    err_sig = snapshot.get("error_signature")
                    if (err_sig != self.last_error_signature
                            and err_sig not in self.dismissed_signatures):
                        if time.time() - self.last_error_time > 2.0:
                            self.last_error_time = time.time()
                            self.handle_new_error(snapshot)
                            suggestion_triggered = True

                # ── P2: YouTube — use real title ──────────────────────
                if not suggestion_triggered and is_youtube:
                    import re as _re
                    # Strip notification badge e.g. "(85) Title - YouTube - Google Chrome"
                    raw = _re.sub(r'^\(\d+\)\s*', '', current_window).strip()
                    parts = _re.split(r'\s*[-—|]\s*', raw)
                    skip  = {
                        "youtube", "google chrome", "mozilla firefox",
                        "microsoft edge", "brave", "opera", "safari",
                        browser_name.lower(),
                    }
                    page_parts  = [p.strip() for p in parts
                                   if p.strip() and p.strip().lower() not in skip]
                    clean_title = page_title or (page_parts[0] if page_parts else "")

                    # Determine if on homepage vs actual video
                    on_homepage   = not clean_title or clean_title.lower() in (
                        "youtube", "new tab", "untitled"
                    )
                    display_title = clean_title if not on_homepage else ""

                    if on_homepage:
                        reason      = "Browsing YouTube feed"
                        reason_long = "You're on the YouTube homepage. Open a video for specific suggestions."
                        confidence  = 0.6
                        suggestions = [
                            {"label": "Recommend Video", "hint": "Suggest something interesting to watch"},
                            {"label": "Ask Anything",    "hint": "Ask me anything"},
                        ]
                    else:
                        reason      = f"Watching {display_title}"
                        reason_long = (f"You're watching \"{display_title}\" on YouTube. "
                                       "Cora can explain the topic, extract key points, or answer questions.")
                        confidence  = 0.85
                        suggestions = [
                            {"label": "Explain Topic", "hint": f"Explain the topic of {display_title}"},
                            {"label": "Key Points",    "hint": f"Main points of {display_title}?"},
                            {"label": "Related Facts", "hint": f"Interesting facts about {display_title}"},
                            {"label": "Ask Question",  "hint": f"I have a question about {display_title}"},
                        ]

                    app_suggestion = {
                        "type":         "youtube_suggestion",
                        "reason":       reason,
                        "reason_long":  reason_long,
                        "confidence":   confidence,
                        "suggestions":  suggestions,
                        "screen_context": current_ocr,
                        "page_title":   display_title,
                        "site_name":    "YouTube",
                        "window_title": current_window,
                    }

                    sig = f"youtube:{display_title or 'home'}"
                    if sig not in self.dismissed_signatures and sig != self.last_suggestion_sig:
                        self._store_proactive_context(
                            snapshot, mode_primary, current_window,
                            reason, current_ocr,
                            page_title=display_title, site_name="YouTube"
                        )
                        self.last_suggestion_sig  = sig
                        self.last_suggestion_time = time.time()
                        self.observer.signals.suggestion_ready.emit(app_suggestion)
                        suggestion_triggered = True

                # ── P3: App-specific static suggestions ──────────────
                if not suggestion_triggered:
                    app_suggestion = None

                    if is_word:
                        app_suggestion = {
                            "type":        "writing_suggestion",
                            "reason":      "Editing a Word document",
                            "reason_long": "Cora can summarize, improve grammar, or rewrite sections.",
                            "confidence":  0.9,
                            "suggestions": [
                                {"label": "Summarize",       "hint": "Summarize this document section"},
                                {"label": "Improve Grammar", "hint": "Fix grammar and clarity"},
                                {"label": "Rewrite",         "hint": "Rewrite this paragraph more clearly"},
                                {"label": "Key Points",      "hint": "Extract key ideas"},
                            ],
                        }
                    elif is_excel:
                        app_suggestion = {
                            "type":        "spreadsheet_suggestion",
                            "reason":      "Working with a spreadsheet",
                            "reason_long": "Cora can analyze data patterns or explain formulas.",
                            "confidence":  0.9,
                            "suggestions": [
                                {"label": "Explain Formula", "hint": "Explain spreadsheet formulas"},
                                {"label": "Analyze Data",    "hint": "Find patterns in this data"},
                                {"label": "Summary",         "hint": "Summarize the spreadsheet content"},
                            ],
                        }
                    elif is_pdf:
                        app_suggestion = {
                            "type":        "pdf_suggestion",
                            "reason":      "Reading a PDF document",
                            "reason_long": "Cora can summarize pages or explain concepts.",
                            "confidence":  0.9,
                            "suggestions": [
                                {"label": "Summarize Page",   "hint": "Summarize the visible page"},
                                {"label": "Explain Concepts", "hint": "Explain difficult parts"},
                                {"label": "Key Points",       "hint": "Extract important ideas"},
                            ],
                        }
                    elif is_browser and site_name:
                        # Known site in browser — use site name
                        app_suggestion = {
                            "type":        "browser_suggestion",
                            "reason":      f"Browsing {site_name}",
                            "reason_long": f"You're on {site_name}. Cora can summarize or explain page content.",
                            "confidence":  0.75,
                            "suggestions": [
                                {"label": "Summarize Page", "hint": f"Summarize the {site_name} page"},
                                {"label": "Key Ideas",      "hint": "Extract key ideas from this page"},
                                {"label": "Explain",        "hint": "Explain the page content"},
                            ],
                        }
                    elif is_browser:
                        # Unknown site — generic browser
                        app_suggestion = {
                            "type":        "browser_suggestion",
                            "reason":      "Browsing the web",
                            "reason_long": "Cora can summarize or explain the current page.",
                            "confidence":  0.65,
                            "suggestions": [
                                {"label": "Summarize Page", "hint": "Summarize this web page"},
                                {"label": "Key Ideas",      "hint": "Extract key ideas"},
                            ],
                        }

                    if app_suggestion:
                        sig = f"{app_suggestion['reason']}:{current_window}"
                        if sig not in self.dismissed_signatures and sig != self.last_suggestion_sig:
                            app_suggestion["screen_context"] = current_ocr
                            app_suggestion["window_title"]   = current_window
                            app_suggestion["page_title"]     = page_title
                            app_suggestion["site_name"]      = site_name
                            self._store_proactive_context(
                                snapshot, mode_primary, current_window,
                                app_suggestion['reason'], current_ocr,
                                page_title=page_title, site_name=site_name
                            )
                            self.last_suggestion_sig  = sig
                            self.last_suggestion_time = time.time()
                            self.observer.signals.suggestion_ready.emit(app_suggestion)
                            suggestion_triggered = True

                # ── P4: Writing / document LLM analysis ──────────────
                # Skip if a static Word suggestion was already shown this window
                if (not suggestion_triggered and mode_primary in ('writing', 'document')
                        and not is_word and not is_excel and not is_pdf):
                    if time.time() - self.last_writing_check_time > 3.0:
                        self.handle_writing_assistance(snapshot)
                        self.last_writing_check_time = time.time()
                        suggestion_triggered = True

                # ── P5: Visual fallback (general / reading) ───────────
                if not suggestion_triggered and time_since_switch > 1.0:
                    now = time.time()
                    if now >= self.analysis_cooldown:
                        ocr_hash = (hashlib.md5(current_ocr.encode()).hexdigest()
                                    if current_ocr else "empty")
                        if ocr_hash != getattr(self, 'last_screen_hash', None):
                            self.last_screen_hash   = ocr_hash
                            self.analysis_cooldown  = now + 1.0
                            if mode_primary not in ('developer', 'internal', 'video', 'youtube'):
                                self.handle_visual_fallback(snapshot)
                                suggestion_triggered = True

                # ── P6: Presence message ──────────────────────────────
                if (not suggestion_triggered
                        and time_since_last_suggestion > 30.0
                        and mode_primary not in ('video', 'youtube', 'browser')):
                    if not self.presence_message_shown:
                        if self.overlay.opacity_effect.opacity() < 0.1:
                            self.overlay.show_message(
                                "Cora Assistant",
                                "I'm observing your activity. Ask me anything!"
                            )
                            self.presence_message_shown = True
                            self.last_suggestion_time   = time.time()

                # ── Clear developer error state if resolved ───────────
                if (not snapshot.get("error")
                        and mode_primary == 'developer'
                        and self.last_error_signature):
                    self.handle_resolution()
                    self.last_error_signature = None

                # ── Dynamic sleep ─────────────────────────────────────
                freq_map = {
                    "developer": 0.15,
                    "writing":   0.4,
                    "reading":   0.6,
                    "general":   0.8,
                    "chat":      1.0,
                    "internal":  0.2,
                }
                time.sleep(freq_map.get(mode_primary, 1.0))
                self.loop_count += 1

            except Exception as e:
                print(f"Copilot Loop Exception: {e}")
                time.sleep(1)

    # ------------------------------------------------------------------
    # Helper: store proactive context with all fields overlay expects
    # ------------------------------------------------------------------

    def _store_proactive_context(self, snapshot, mode_primary, window_title,
                                  reason, ocr_text,
                                  page_title="", site_name=""):
        self.last_proactive_context = {
            'mode_primary':  mode_primary,
            'window_title':  window_title,
            'page_title':    page_title,
            'site_name':     site_name,
            'reason':        reason,
            'screen_context': ocr_text,   # full OCR — not truncated
            'ocr_text':      ocr_text,
            'screenshot':    getattr(self.observer, 'last_proactive_screenshot', None),
            'error_file':    snapshot.get('error', {}).get('file',    '') if snapshot.get('error') else '',
            'error_line':    snapshot.get('error', {}).get('line',    '') if snapshot.get('error') else '',
            'error_message': snapshot.get('error', {}).get('message', '') if snapshot.get('error') else '',
            'error_context': snapshot.get('error', {}).get('context', '') if snapshot.get('error') else '',
        }

    # ------------------------------------------------------------------
    # Error payload builder
    # ------------------------------------------------------------------

    def _build_error_payload(self, error, reason="", code="", payload_type="syntax_error"):
        return {
            "type":          payload_type,
            "reason":        reason or f"Error: {error.get('message', 'Unknown')}",
            "code":          code,
            "suggestions":   [{"label": "Fix Error", "hint": "Show corrected code"}],
            "confidence":    1.0,
            "screen_context": "",
            "error_file":    error.get('file', ''),
            "error_line":    error.get('line', ''),
            "error_message": error.get('message', ''),
            "error_context": error.get('context', ''),
        }

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------

    def handle_new_error(self, snapshot):
        if not snapshot.get("error"):
            return

        error = snapshot['error']
        print(f"Copilot: 🚨 New Error Detected: {error['message']}")

        temp_payload = self._build_error_payload(
            error,
            reason=f"Analyzing: {error['message']}...",
            code="# Fetching fix..."
        )
        self.observer.signals.suggestion_ready.emit(temp_payload)

        self._store_proactive_context(
            snapshot,
            mode_primary  = snapshot.get('mode_primary', 'general'),
            window_title  = snapshot.get('window_title', ''),
            reason        = f"Error: {error.get('message', '')}",
            ocr_text      = self.observer.last_ocr_text,
        )
        # Add error-specific fields
        self.last_proactive_context.update({
            'error_file':    error.get('file', ''),
            'error_line':    error.get('line', ''),
            'error_message': error.get('message', ''),
            'error_context': error.get('context', ''),
            'file_content':  snapshot.get('file_content', ''),
        })

        error_prompt = (
            f"You are a strict debugging assistant.\n\n"
            f"LANGUAGE: Python\n\n"
            f"ERROR:\n"
            f"File: {error['file']}\n"
            f"Line: {error['line']}\n"
            f"Message: {error['message']}\n\n"
            f"CODE:\n{error.get('context', '')}\n\n"
            f"OUTPUT JSON ONLY:\n"
            f'{{"reason": "short explanation", "code": "corrected code"}}'
        )

        print("--- DEBUG PROMPT START ---")
        print(f"Proactive Suggestion: Analyzing: {error['message']}...")
        print(f"Error Context: {error.get('context', '')}")
        print("--- DEBUG PROMPT END ---")

        try:
            import ollama
            now = time.time()
            if now - self.last_llm_call_time < 1.5:
                print("Copilot: Rate limit hit. Skipping LLM call.")
                return
            self.last_llm_call_time = now

            print("Copilot: Asking LLM for error fix...")
            response = ollama.chat(
                model=self.observer.model,
                messages=[
                    {'role': 'system', 'content': config.DEV_SYSTEM_PROMPT},
                    {'role': 'user',   'content': error_prompt},
                ]
            )
            text = response['message']['content'].strip()
            print(f"Copilot: LLM Response (Raw): {text[:80]}...")

            payload = self._clean_json(text)
            if payload:
                final = self._build_error_payload(
                    error,
                    reason=payload.get('reason', error['message']),
                    code=payload.get('code', '')
                )
                print("Copilot: Payload created (JSON parsed)")
            else:
                print("Copilot: JSON parse failed. Using fallback payload.")
                final = self._build_error_payload(
                    error,
                    reason=f"Fix for: {error['message']}",
                    code=text
                )

            self.observer.signals.suggestion_ready.emit(final)
            print("Copilot: Signal emitted: suggestion_ready")

        except Exception as e:
            print(f"Copilot LLM Error: {e}")
            fallback = self._build_error_payload(
                error,
                reason=f"Error detected: {error['message']}",
                code=f"# LLM call failed: {e}"
            )
            self.observer.signals.suggestion_ready.emit(fallback)

    def handle_resolution(self):
        print("Copilot: Resolving error state via Signal.")
        self.observer.signals.error_resolved.emit()

    # ------------------------------------------------------------------
    # Visual fallback
    # ------------------------------------------------------------------

    def handle_visual_fallback(self, snapshot):
        mode_primary   = snapshot.get('mode_primary', 'general')
        mode_secondary = snapshot.get('mode_secondary', 'unknown')

        should_check = mode_secondary in ('terminal', 'browser', 'unknown') or \
                       mode_primary in ('general', 'reading')

        if not should_check:
            return

        now = time.time()
        if now - self.last_llm_call_time < 2.0:
            return

        ocr_text = getattr(self.observer, 'last_ocr_text', '')
        if ocr_text and ocr_text == self.last_ocr_text_cache:
            return

        img = self.observer.capture_screen()
        if img is None:
            return

        win_title = snapshot.get('window_title', 'Unknown').lower()
        if any(kw in win_title for kw in ["cora ai", "cora suggestion"]):
            return

        payload = self.observer.analyze(img, context_text=f"Active Window: {win_title}")
        if payload and isinstance(payload, dict):
            reason = payload.get('reason', '')
            sig    = f"{reason}:{win_title}"
            if sig == self.last_suggestion_sig:
                return

            self._store_proactive_context(
                snapshot, mode_primary, win_title, reason, ocr_text
            )
            self.last_suggestion_sig  = sig
            self.last_suggestion_time = time.time()
            self.observer.signals.suggestion_ready.emit(payload)

    # ------------------------------------------------------------------
    # Writing handler
    # ------------------------------------------------------------------

    def handle_writing_assistance(self, snapshot):
        print("Copilot: ✍️ Writing Pause Detected. Analyzing...")
        try:
            now = time.time()
            if now - self.last_llm_call_time < 0.6:
                return
            self.last_llm_call_time = now

            win_title  = snapshot.get('window_title', 'Unknown Application')
            ocr_text   = getattr(self.observer, 'last_ocr_text', '')
            current_ocr = ocr_text

            # ── Emit instant suggestion immediately ───────────────────
            instant_sig = f"writing_instant:{win_title}"
            if instant_sig != self.last_suggestion_sig and instant_sig not in self.dismissed_signatures:
                instant = {
                    "type":        "writing_suggestion",
                    "reason":      "Editing document",
                    "reason_long": f"Working in {win_title}",
                    "confidence":  0.75,
                    "suggestions": [
                        {"label": "Improve Grammar", "hint": "Fix grammar and clarity in visible text"},
                        {"label": "Summarize",       "hint": "Summarize the visible section"},
                        {"label": "Rewrite",         "hint": "Rewrite this paragraph more clearly"},
                    ],
                    "screen_context": current_ocr,
                    "window_title":   win_title,
                }
                self._store_proactive_context(
                    snapshot, 'writing', win_title, instant['reason'], current_ocr
                )
                self.last_suggestion_sig  = instant_sig
                self.last_suggestion_time = time.time()
                self.observer.signals.suggestion_ready.emit(instant)

            # ── LLM enrichment in background ──────────────────────────
            import threading
            def _enrich():
                try:
                    img     = self.observer.capture_screen()
                    payload = self.observer.analyze(
                        img, context_text=f"User is writing in {win_title}"
                    )
                    if not payload:
                        return
                    confidence = payload.get('confidence', 0.0)
                    if confidence <= config.WRITING_THRESHOLD:
                        return
                    payload['type'] = 'writing_suggestion'
                    if not payload.get('suggestions'):
                        payload['suggestions'] = [
                            {"label": "Explain",   "hint": "Explain this content"},
                            {"label": "Summarize", "hint": "Summarize this content"},
                        ]
                    reason = payload.get('reason', '')
                    sig    = f"{reason}:{win_title}"
                    if sig != self.last_visual_sig and sig not in self.dismissed_signatures:
                        self._store_proactive_context(
                            snapshot, 'writing', win_title, reason,
                            getattr(self.observer, 'last_ocr_text', '')
                        )
                        self.last_visual_sig      = sig
                        self.last_suggestion_sig  = sig
                        self.last_suggestion_time = time.time()
                        print(f"✨ Writing Enriched: {reason}")
                        self.observer.signals.suggestion_ready.emit(payload)
                except Exception as e:
                    print(f"Writing enrich error: {e}")

            threading.Thread(target=_enrich, daemon=True).start()

        except Exception as e:
            print(f"Copilot Writing Handler Error: {e}")

    # ------------------------------------------------------------------
    # Reading handler
    # ------------------------------------------------------------------

    def handle_reading_assistance(self, snapshot):
        print("Copilot: 📖 Reading Pause Detected. Analyzing...")
        try:
            now = time.time()
            if now - self.last_llm_call_time < 0.6:
                return
            self.last_llm_call_time = now

            img       = self.observer.capture_screen()
            win_title = snapshot.get('window_title', 'Unknown Document')
            print(f"Copilot: Analyzing Reading Context in '{win_title}'...")
            payload   = self.observer.analyze(img, context_text=f"User is reading: {win_title}")

            if payload:
                confidence = payload.get('confidence', 0.0)
                if confidence > 0.35:
                    payload['type'] = 'reading_suggestion'
                    if not payload.get('suggestions'):
                        payload['suggestions'] = [
                            {"label": "Summarize Page",   "hint": "Summarize this visible page"},
                            {"label": "Explain Concepts", "hint": "Explain key concepts"},
                            {"label": "Key Points",       "hint": "Extract bullet points"},
                        ]

                    reason = payload.get('reason', '')
                    sig    = f"{reason}:{win_title}"

                    if sig != self.last_visual_sig and sig not in self.dismissed_signatures:
                        self._store_proactive_context(
                            snapshot, 'reading', win_title, reason,
                            getattr(self.observer, 'last_ocr_text', '')
                        )
                        self.last_visual_sig      = sig
                        self.last_suggestion_sig  = sig
                        self.last_suggestion_time = time.time()
                        print(f"✨ Reading Suggestion: {reason}")
                        self.observer.signals.suggestion_ready.emit(payload)

        except Exception as e:
            print(f"Copilot Reading Handler Error: {e}")

    # ------------------------------------------------------------------
    # Document handler
    # ------------------------------------------------------------------

    def handle_document_assistance(self, snapshot):
        print("Copilot: 📄 Analyzing Document...")
        try:
            img      = self.observer.capture_screen()
            ocr_text = self.observer.extract_text_from_screen(img)
            if len(ocr_text) < 40:
                return

            ocr_hash = hashlib.md5(ocr_text.encode()).hexdigest()
            if ocr_hash == self.last_ocr_text_cache:
                return
            self.last_ocr_text_cache = ocr_hash

            win_title = snapshot.get('window_title', 'Document')
            payload   = self.observer.analyze(
                img,
                context_text=f"User is writing in {win_title}. Visible Text: {ocr_text[:500]}..."
            )

            if payload and isinstance(payload, dict):
                reason = payload.get('reason', '')
                sig    = f"{reason}:{win_title}"
                if sig == self.last_suggestion_sig:
                    return
                self._store_proactive_context(
                    snapshot, snapshot.get('mode_primary', 'document'),
                    win_title, reason, ocr_text
                )
                self.last_suggestion_sig  = sig
                self.last_suggestion_time = time.time()
                self.observer.signals.suggestion_ready.emit(payload)

        except Exception as e:
            print(f"Copilot Document Handler Error: {e}")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def stop(self):
        self.running = False
        self.wait()

    def _clean_json(self, text):
        try:
            return json.loads(text)
        except Exception:
            pass
        try:
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                block = text.split("```")[1]
                if block and block.split('\n')[0].strip().isalpha():
                    block = '\n'.join(block.split('\n')[1:])
                text = block.split("```")[0].strip()
            start = text.find('{')
            end   = text.rfind('}')
            if start != -1 and end != -1 and end > start:
                text = text[start:end + 1]
            return json.loads(text)
        except Exception:
            return None