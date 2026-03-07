
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
        self.observer = observer
        self.overlay = overlay
        self.running = False
        self.last_error_signature = None
        self.last_visual_sig = None
        self.loop_count = 0
        self.last_llm_call_time = 0
        
        # Intelligent Dismiss State
        self.dismissed_signatures = set()
        self.snoozed_until = 0.0

        # Connect UI Signals
        self.overlay.dismissed.connect(self.on_user_dismissed)
        self.overlay.snoozed.connect(self.on_user_snoozed)
        
        # State Tracking
        self.last_active_window = None
        self.last_writing_check_time = 0
        self.last_doc_check_time = 0
        
        self.last_proactive_context = None
        self.last_ocr_text_cache = "" # For dismissal reset tracking
        self.last_screen_hash = None # For responsiveness: skip redundant frames
        
        self.last_switch_time = time.time()
        self.window_focus_start = time.time() # Track focus duration
        self.presence_message_shown = False
        self.last_error_time = 0.0
        
        # Performance & Throttling
        self.last_suggestion_time = time.time()
        self.last_presence_time = 0.0
        self.last_suggestion_sig = None # reason + window
        self.analysis_cooldown = 0


    def on_user_dismissed(self):
        # Add current error/visual sig to dismissed
        if self.last_error_signature:
            self.dismissed_signatures.add(self.last_error_signature)
            print(f"Copilot: Dismissed error signature: {self.last_error_signature}")
        
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

    def start_proactive_loop(self):
        self.running = True
        self.paused = False
        print("Copilot Controller: Proactive Loop Started.")
        
        while self.running:
            try:
                # 0. Check Pause and Snooze
                if self.paused:
                    time.sleep(0.5)
                    continue

                if time.time() < self.snoozed_until:
                    time.sleep(2)
                    continue

                # 1. Get OS/Context Snapshot (Primary heartbeat)
                snapshot = self.context_engine.get_context_snapshot()
                current_window = snapshot.get('window_title', '')
                current_mode = snapshot.get('mode', 'unknown')
                mode_primary = snapshot.get('mode_primary', current_mode)
                mode_secondary = snapshot.get('mode_secondary', 'unknown')
                idle_time = self.context_engine.get_idle_time()

                # DEBUG: Pulse Check (Every 5 loops ~ 5s)
                if self.loop_count % 5 == 0:
                    print(f"Copilot Pulse: Mode=[{mode_primary}/{mode_secondary}] Idle=[{idle_time:.1f}s] Window=[{current_window}]")

                # 2. Early Exits for internal/system modes
                if mode_primary == "internal":
                    time.sleep(0.5)
                    continue

                cw_lower = (current_window or "").lower()
                if any(kw in cw_lower for kw in ["cora suggestion", "cora ai"]):
                    time.sleep(1.0)
                    continue

                # 3. Section 1: Clear Stale Error State
                if not snapshot.get("error") and self.last_error_signature:
                    print("Copilot: Error resolved, clearing state.")
                    self.last_error_signature = None
                    self.last_visual_sig = None
                    self.dismissed_signatures.clear()

                # ---------------------------------------------------------
                # A. APP SWITCH PRESENCE MODE
                # ---------------------------------------------------------
                if current_window != self.last_active_window:
                    self.last_active_window = current_window
                    time.sleep(1)
                    continue

                # FIX 2: Reset dismissal history on significant OCR change (Slide Change)
                current_ocr = getattr(self.observer, 'last_ocr_text', '')
                current_hash = hashlib.md5(current_ocr.encode()).hexdigest() if current_ocr else None

                if current_hash != self.last_ocr_text_cache:
                    # If OCR changed significantly, reset dismissal history
                    # This allows the orb to reappear when moving to a new slide
                    print("Copilot: Context change detected.")
                    self.dismissed_signatures.clear()
                    self.last_ocr_text_cache = current_hash

                # ---------------------------------------------------------
                # Section 2.2: Suggestion Priority Check (ISSUE 2)
                # ---------------------------------------------------------
                time_since_switch = time.time() - self.last_switch_time
                time_since_last_suggestion = time.time() - self.last_suggestion_time
                suggestion_triggered = False
                
                # PART 5: Idle Threshold (Issue 12) - Wait for 0.8s pause
                if idle_time < 0.8:
                    time.sleep(0.2)
                    continue

                # ISSUE 14: Application-Aware Detection
                win_lower = current_window.lower()
                is_word = "word" in win_lower
                is_excel = "excel" in win_lower
                is_pdf = "pdf" in win_lower or "acrobat" in win_lower
                is_browser = any(x in win_lower for x in ["chrome", "edge", "firefox"])
                is_youtube = "youtube" in win_lower

                # 1. Error suggestions (Priority 1)
                if snapshot.get("error"):
                    err_sig = snapshot.get("error_signature")
                    if err_sig != self.last_error_signature and err_sig not in self.dismissed_signatures:
                        # Error Cooldown (2s - ISSUE 5)
                        if time.time() - self.last_error_time > 2.0:
                            self.last_error_time = time.time()
                            self.handle_new_error(snapshot)
                            suggestion_triggered = True

                # ISSUE 14: Application-Specific Suggestions (Priority 1.5)
                if not suggestion_triggered:
                    app_suggestion = None
                    if is_word:
                        app_suggestion = {
                            "type": "writing_suggestion",
                            "reason": "You are editing a Word document.",
                            "reason_long": "CORA can help summarize, improve grammar, or rewrite sections of your document.",
                            "confidence": 0.9,
                            "suggestions": [
                                {"label":"Summarize","hint":"Summarize this document section"},
                                {"label":"Improve Grammar","hint":"Fix grammar and clarity"},
                                {"label":"Rewrite","hint":"Rewrite this paragraph more clearly"},
                                {"label":"Key Points","hint":"Extract key ideas"}
                            ]
                        }
                    elif is_excel:
                        app_suggestion = {
                            "type": "general",
                            "reason": "Working with a spreadsheet.",
                            "reason_long": "CORA can help analyze data patterns or explain formulas.",
                            "confidence": 0.9,
                            "suggestions": [
                                {"label":"Explain Formula","hint":"Explain spreadsheet formulas"},
                                {"label":"Analyze Data","hint":"Find patterns in this data"},
                                {"label":"Summary","hint":"Summarize the spreadsheet content"}
                            ]
                        }
                    elif is_pdf:
                        app_suggestion = {
                            "type": "reading_suggestion",
                            "reason": "You are reading a PDF document.",
                            "reason_long": "CORA can summarize pages or explain concepts from the document.",
                            "confidence": 0.9,
                            "suggestions": [
                                {"label":"Summarize Page","hint":"Summarize the visible page"},
                                {"label":"Explain Concepts","hint":"Explain difficult parts"},
                                {"label":"Key Points","hint":"Extract important ideas"}
                            ]
                        }
                    elif is_browser:
                        app_suggestion = {
                            "type": "general",
                            "reason": "Viewing content in a browser.",
                            "reason_long": "CORA can explain the page content or summarize information.",
                            "confidence": 0.8,
                            "suggestions": [
                                {"label":"Explain Page","hint":"Explain this webpage"},
                                {"label":"Summarize","hint":"Summarize the page"},
                                {"label":"Key Ideas","hint":"Extract key ideas"}
                            ]
                        }
                    
                    if is_youtube and not app_suggestion:
                        title_text = getattr(self.observer, "last_ocr_text", "")
                        app_suggestion = {
                            "type": "video_suggestion",
                            "reason": "You're watching a YouTube video.",
                            "reason_long": "CORA can help explain the topic of this video or answer questions about it.",
                            "confidence": 0.9,
                            "suggestions": [
                                {"label": "Explain Topic", "hint": "Explain the topic of this video"},
                                {"label": "Summarize Subtitles", "hint": "Summarize visible subtitles"},
                                {"label": "Related Topics", "hint": "Suggest related learning topics"},
                                {"label": "Ask Question", "hint": "Ask something about this video"}
                            ]
                        }

                    if app_suggestion:
                        # For YouTube, we use the title for better uniqueness
                        if is_youtube:
                            title_text = getattr(self.observer, "last_ocr_text", "")
                            sig = f"youtube:{title_text[:50]}" # Limit length
                        else:
                            sig = f"{app_suggestion['reason']}:{current_window}"
                            
                        if sig not in self.dismissed_signatures and sig != self.last_suggestion_sig:
                            # Standardized context storage
                            self.last_proactive_context = {
                                'mode_primary': mode_primary,
                                'window_title': current_window,
                                'reason': app_suggestion.get('reason', ''),
                                'ocr_text': getattr(self.observer, 'last_ocr_text', ''),
                                'screenshot': getattr(self.observer, 'last_proactive_screenshot', None)
                            }
                            self.last_suggestion_sig = sig
                            self.last_suggestion_time = time.time()
                            self.observer.signals.suggestion_ready.emit(app_suggestion)
                            suggestion_triggered = True
                            continue # Skip fallback for specific apps
                
                # 2. Writing / Document suggestions (Priority 2)
                if not suggestion_triggered and mode_primary in ['writing', 'document']:
                    if time.time() - self.last_writing_check_time > 3.0:
                        self.handle_writing_assistance(snapshot)
                        self.last_writing_check_time = time.time()
                        suggestion_triggered = True

                # 3. Visual / Screen Suggestions (Priority 3)
                if not suggestion_triggered and time_since_switch > 1.0:
                    # ISSUE 13: Analysis Cooldown & Hashing
                    now = time.time()
                    if now >= self.analysis_cooldown:
                        current_ocr = getattr(self.observer, 'last_ocr_text', '')
                        screen_hash = hashlib.md5(current_ocr.encode()).hexdigest() if current_ocr else "empty"
                        
                        if screen_hash != getattr(self, "last_screen_hash", None):
                            self.last_screen_hash = screen_hash
                            self.analysis_cooldown = now + 1.0 # Throttle analysis to 1/s
                            
                            if mode_primary == 'video':
                                 self.handle_video_assistance(snapshot)
                                 suggestion_triggered = True
                            else:
                                 # General analysis (visual fallback)
                                 self.handle_visual_fallback(snapshot)
                                 suggestion_triggered = True

                # 4. Idle Assistance (Priority 4) - ISSUE 2.3
                if not suggestion_triggered and idle_time > 8.0:
                    # ANALYSIS COOLDOWN for idle as well
                    now = time.time()
                    if now >= self.analysis_cooldown:
                        print(f"Copilot: Idle Assistance Triggered ({idle_time:.1f}s)")
                        self.analysis_cooldown = now + 1.0
                        self.handle_visual_fallback(snapshot)

                # 5. Presence message (Priority 5) - ISSUE 6
                if not suggestion_triggered and time_since_last_suggestion > 30.0:
                    if not self.presence_message_shown:
                        # Only show if overlay is idle
                        if self.overlay.opacity_effect.opacity() < 0.1:
                            print("Copilot: Showing Presence Message (30s quiet period)")
                            self.overlay.show_message("Cora Assistant", "I'm observing your activity. Ask me anything about your current work!")
                            self.presence_message_shown = True
                            self.last_suggestion_time = time.time()

                # Clear error state if no error present and in developer mode
                if not snapshot.get("error") and mode_primary == 'developer' and self.last_error_signature:
                    self.handle_resolution()
                    self.last_error_signature = None

                # ---------------------------------------------------------
                # FIX 5: Mode-Based Loop Frequency (Dynamic Sleep)
                # Moved to end of loop cycle
                # ---------------------------------------------------------
                freq_map = {
                    "developer": 0.15,
                    "writing": 0.4,
                    "reading": 0.6,
                    "general": 0.8,
                    "chat": 1.0,
                    "internal": 0.2
                }
                sleep_time = freq_map.get(mode_primary, 1.0)
                time.sleep(sleep_time)
                self.loop_count += 1
                
            except Exception as e:
                print(f"Copilot Loop Exception: {e}")
                time.sleep(1) # Prevent busy loop on crash

    def stop(self):
        self.running = False
        self.wait()

    def _build_error_payload(self, error, reason="", code="", payload_type="syntax_error"):
        """Build a guaranteed-valid error payload with all required fields."""
        return {
            "type": payload_type,
            "reason": reason or f"Error: {error.get('message', 'Unknown')}",
            "code": code,
            "suggestions": [{"label": "Fix Error", "hint": "Show corrected code"}],
            "confidence": 1.0,
            "screen_context": "",
            "error_file": error.get('file', ''),
            "error_line": error.get('line', ''),
            "error_message": error.get('message', ''),
            "error_context": error.get('context', '')
        }

    def handle_new_error(self, snapshot):
        # Section 3: Verify error still exists
        if not snapshot.get("error"):
            return
            
        error = snapshot['error']
        print(f"Copilot: 🚨 New Error Detected: {error['message']}")
        
        # PHASE 1: Immediate Visual Feedback (includes full error context)
        temp_payload = self._build_error_payload(
            error, 
            reason=f"Analyzing: {error['message']}...",
            code="# Fetching fix..."
        )
        self.observer.signals.suggestion_ready.emit(temp_payload)
        
        # Store proactive context for grounded suggestion execution
        self.last_proactive_context = {
            'mode_primary': snapshot.get('mode_primary', snapshot.get('mode', 'general')),
            'window_title': snapshot.get('window_title', ''),
            'reason': f"Error: {error.get('message', '')}",
            'ocr_text': self.observer.last_ocr_text,
            'screenshot': self.observer.last_proactive_screenshot,
            'error_file': error.get('file', ''),
            'error_line': error.get('line', ''),
            'error_message': error.get('message', ''),
            'error_context': error.get('context', ''),
            'file_content': snapshot.get('file_content', ''),
        }
        
        # Construct Prompt — JSON ONLY, no markdown
        error_prompt = f"""You are a strict debugging assistant.

LANGUAGE: Python

ERROR:
File: {error['file']}
Line: {error['line']}
Message: {error['message']}

CODE:
{error.get('context', '')}

TASK:
1. Identify exact syntax mistake
2. Provide corrected code
3. Keep explanation MAX 1 sentence
4. Do NOT give teaching paragraphs

OUTPUT JSON ONLY:
{{"reason": "short explanation", "code": "corrected code"}}"""

        # DEBUG LOGGING
        print("--- DEBUG PROMPT START ---")
        print(f"Proactive Suggestion: Analyzing: {error['message']}...")
        print(f"Error Context: {error.get('context', '')}")
        print("--- DEBUG PROMPT END ---")

        try:
            import ollama
            
            # Rate Limiting (≥1.5s between calls)
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
                    {'role': 'user', 'content': error_prompt}
                ]
            )
            text = response['message']['content'].strip()
            print(f"Copilot: LLM Response (Raw): {text[:80]}...")
            
            # Parse JSON
            payload = self._clean_json(text)
            if payload:
                # Merge with guaranteed structure
                final = self._build_error_payload(
                    error,
                    reason=payload.get('reason', error['message']),
                    code=payload.get('code', '')
                )
                print(f"Copilot: Payload created (JSON parsed)")
            else:
                # FALLBACK: JSON parsing failed — use raw text
                print("Copilot: JSON parse failed. Using fallback payload.")
                final = self._build_error_payload(
                    error,
                    reason=f"Fix for: {error['message']}",
                    code=text  # Raw LLM output as code
                )
                final['type'] = 'syntax_error'
            
            # Always emit a valid payload
            self.observer.signals.suggestion_ready.emit(final)
            print("Copilot: Signal emitted: suggestion_ready")
                
        except Exception as e:
            print(f"Copilot LLM Error: {e}")
            # RECOVERY: Emit fallback so UI doesn't freeze
            fallback = self._build_error_payload(
                error,
                reason=f"Error detected: {error['message']}",
                code=f"# LLM call failed: {e}"
            )
            self.observer.signals.suggestion_ready.emit(fallback)

    def handle_resolution(self):
        # Emit signal to hide bubble/overlay
        print("Copilot: Resolving error state via Signal.")
        self.observer.signals.error_resolved.emit()

    def handle_visual_fallback(self, snapshot):
        # Visual check logic (migrated from Observer)
        # Check if mode is appropriate
        mode_primary = snapshot.get('mode_primary', 'general')
        mode_secondary = snapshot.get('mode_secondary', 'unknown')
        should_check = False
        
        # Check Strategy based on Secondary Mode
        if mode_secondary in ['terminal', 'browser', 'unknown']:
            should_check = True
        elif mode_primary in ['general', 'video', 'reading']:
            should_check = True
        elif mode_primary in ['developer', 'internal']:
            # STRICT MODE: Disable visual fallback in clear productive or internal modes
            should_check = False
                
        if should_check:
             # ISSUE 5: Throttling (2s between LLM calls)
             now = time.time()
             if now - self.last_llm_call_time < 2.0:
                 return

             # ISSUE 5: Content Hash Check
             ocr_text = getattr(self.observer, 'last_ocr_text', '')
             if ocr_text and ocr_text == self.last_ocr_text_cache:
                 # Minimal text change, skip LLM
                 return

             # Capture via Observer
             img = self.observer.capture_screen()
             if img is None:
                 return
             win_title = snapshot.get('window_title', 'Unknown').lower()
             
             # Double Check: If active window is Cora UI, ABORT
             cora_keywords = ["cora ai", "cora suggestion"]
             if any(kw in win_title for kw in cora_keywords):
                 return

             # ISSUE 7: Video/Browser tailored analysis (Neutralized)
             video_keywords = ["youtube", "video", "tutorial", "course", "netflix"]
             context_text = f"Active Window: {win_title}"
             if any(kw in win_title for kw in video_keywords):
                 context_text += " | User is on a video/browser page. CORA cannot hear audio. Suggest neutral assistance like 'Need help with something on this page?' or 'I can help explain concepts related to this page.' Avoid 'summarize video'."
             
             # Analyze
             payload = self.observer.analyze(img, context_text=context_text)
             if payload and isinstance(payload, dict):
                 # Performance Deduplication (Issue 8)
                 reason = payload.get('reason', '')
                 sig = f"{reason}:{win_title}"
                 if sig == self.last_suggestion_sig:
                     return

                 # Store proactive context (Requirement 4)
                 self.last_proactive_context = {
                     'mode_primary': snapshot.get('mode_primary', 'general'),
                     'window_title': win_title,
                     'reason': reason,
                     'ocr_text': getattr(self.observer, 'last_ocr_text', ''),
                     'screenshot': getattr(self.observer, 'last_proactive_screenshot', None)
                 }

                 self.last_suggestion_sig = sig
                 self.last_suggestion_time = time.time()
                 self.observer.signals.suggestion_ready.emit(payload)

    def handle_writing_assistance(self, snapshot):
        print("Copilot: ✍️ Writing Pause Detected. Analyzing...")
        try:
             # FIX 6: Rate Limiting (shared 1.5s cooldown)
             now = time.time()
             if now - self.last_llm_call_time < 0.6:
                 print("Copilot: Rate limit hit. Skipping writing analyze.")
                 return
             self.last_llm_call_time = now # Set here to lock!

             # 1. Capture Screen (Productivity App)
             img = self.observer.capture_screen()
             win_title = snapshot.get('window_title', 'Unknown Application')
             
             # 2. Re-use Observer.analyze for robust OCR + Vision + JSON
             print(f"Copilot: Analyzing Writing Context in '{win_title}'...")
             payload = self.observer.analyze(img, context_text=f"User is writing in {win_title}")
             
             # 3. Process
             if payload:
                 print(f"WRITING PAYLOAD: {payload}")
                 confidence = payload.get('confidence', 0.0)
                 
                 # 4. Check Thresholds (Lower for writing)
                 if confidence > config.WRITING_THRESHOLD:
                     payload['type'] = 'writing_suggestion'
                     
                     # Enforce Structure
                     if 'suggestions' not in payload or not payload['suggestions']:
                         payload['suggestions'] = [
                             {"label": "Explain", "hint": "Explain this content"},
                             {"label": "Summarize", "hint": "Summarize this content"}
                         ]

                     # Performance Deduplication
                     reason = payload.get('reason', '')
                     sig = f"{reason}:{win_title}"
                     
                     if sig != self.last_visual_sig and sig not in self.dismissed_signatures:
                         # Store proactive context (Requirement 4)
                         self.last_proactive_context = {
                             'mode_primary': 'writing',
                             'window_title': win_title,
                             'reason': reason,
                             'ocr_text': getattr(self.observer, 'last_ocr_text', ''),
                             'screenshot': getattr(self.observer, 'last_proactive_screenshot', None)
                         }
                         
                         self.last_visual_sig = sig
                         self.last_suggestion_sig = sig
                         self.last_suggestion_time = time.time()
                         print(f"✨ Writing Suggestion: {reason}")
                         self.observer.signals.suggestion_ready.emit(payload)
                 else:
                     print(f"Copilot: Low confidence ({confidence}) writing suggestion.")
                     
        except Exception as e:
            print(f"Copilot Writing Handler Error: {e}")

        
    def _clean_json(self, text):
        """Extract JSON from LLM response. Returns dict or None."""
        try:
            # Strategy 1: Direct parse
            return json.loads(text)
        except:
            pass
        
        try:
            # Strategy 2: Extract from markdown code block
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                # Could be ```python or other — try extracting JSON from first block
                block = text.split("```")[1]
                # If block starts with a language tag, skip it
                if block and block.split('\n')[0].strip().isalpha():
                    block = '\n'.join(block.split('\n')[1:])
                text = block.split("```")[0].strip()
            
            # Strategy 3: Find JSON object boundaries
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1 and end > start:
                text = text[start:end+1]
            
            return json.loads(text)
        except:
            return None
    def handle_reading_assistance(self, snapshot):
        print("Copilot: 📖 Reading Pause Detected. Analyzing...")
        try:
             # FIX 6: Rate Limiting (shared 1.5s cooldown)
             now = time.time()
             if now - self.last_llm_call_time < 0.6:
                 print("Copilot: Rate limit hit. Skipping reading analyze.")
                 return
             self.last_llm_call_time = now # Set here to lock!

             # 1. Capture Screen 
             img = self.observer.capture_screen()
             win_title = snapshot.get('window_title', 'Unknown Document')

             # 2. Re-use Observer.analyze for robust OCR + Vision + JSON
             print(f"Copilot: Analyzing Reading Context in '{win_title}'...")
             payload = self.observer.analyze(img, context_text=f"User is reading document: {win_title}")
             
             if payload:
                 print(f"READING PAYLOAD: {payload}")
                 confidence = payload.get('confidence', 0.0)
                 
                 if confidence > 0.35: 
                     payload['type'] = 'reading_suggestion'
                     
                     # Ensure we have robust suggestions list
                     if 'suggestions' not in payload or not payload['suggestions']:
                         payload['suggestions'] = [
                             {"label": "Summarize Page", "hint": "Summarize this visible page"},
                             {"label": "Explain Concepts", "hint": "Explain key concepts on this page"},
                             {"label": "Key Points", "hint": "Extract bullet points"}
                         ]
                     
                     # Deduplication
                     reason = payload.get('reason', '')
                     sig = f"{reason}:{win_title}"
                     
                     if sig != self.last_visual_sig and sig not in self.dismissed_signatures:
                         # Store proactive context (Requirement 4)
                         self.last_proactive_context = {
                             'mode_primary': 'reading',
                             'window_title': win_title,
                             'reason': reason,
                             'ocr_text': getattr(self.observer, 'last_ocr_text', ''),
                             'screenshot': getattr(self.observer, 'last_proactive_screenshot', None)
                         }
                         
                         self.last_visual_sig = sig
                         self.last_suggestion_sig = sig
                         self.last_suggestion_time = time.time()
                         print(f"✨ Reading Suggestion: {reason}")
                         self.observer.signals.suggestion_ready.emit(payload)
                 else:
                     print(f"Copilot: Low confidence ({confidence}) reading suggestion.")
                     
        except Exception as e:
            print(f"Copilot Reading Handler Error: {e}")

    def handle_document_assistance(self, snapshot):
        print("Copilot: 📄 Analyzing Word Document...")
        try:
             # 1. Capture Screen
             img = self.observer.capture_screen()
             
             # 2. Extract OCR Text
             ocr_text = self.observer.extract_text_from_screen(img)
             print(f"Copilot: OCR Extracted {len(ocr_text)} chars: '{ocr_text[:50]}...'")
             
             # 3. Analyze if length passes threshold
             if len(ocr_text) < 40: # Reduced for responsiveness
                 print(f"Copilot: Document content too short, skipping.")
                 return
                 
             # 4. Check for changes via hashing
             import hashlib
             ocr_hash = hashlib.md5(ocr_text.encode()).hexdigest()
             if ocr_hash == self.last_ocr_text_cache:
                 print("Copilot: Hash match - content unchanged.")
                 return
             
             # Update cache for next loop
             self.last_ocr_text_cache = ocr_hash

             # 5. Analyze via LLM
             win_title = snapshot.get('window_title', 'Microsoft Word')
             payload = self.observer.analyze(img, context_text=f"User is writing in {win_title}. Visible Text: {ocr_text[:500]}...")
             
             if payload and isinstance(payload, dict):
                 # Performance Deduplication
                 reason = payload.get('reason', '')
                 sig = f"{reason}:{win_title}"
                 if sig == self.last_suggestion_sig:
                     return

                 # Store proactive context
                 self.last_proactive_context = {
                     'mode_primary': snapshot.get('mode_primary', 'document'),
                     'window_title': win_title,
                     'reason': reason,
                     'ocr_text': getattr(self.observer, 'last_ocr_text', ''),
                     'screenshot': getattr(self.observer, 'last_proactive_screenshot', None)
                 }

                 self.last_suggestion_sig = sig
                 self.last_suggestion_time = time.time()
                 self.observer.signals.suggestion_ready.emit(payload)
                     
        except Exception as e:
            print(f"Copilot Document Handler Error: {e}")

    def handle_video_assistance(self, snapshot):
        """Specifically handles video players with neutral assistance (Issue 9)."""
        print("Copilot: 🎬 Neutralizing Video Context...")
        try:
             # Rate limiting check (shared)
             now = time.time()
             if now - self.last_llm_call_time < 0.6:
                 return

             # 1. Capture Screen
             img = self.observer.capture_screen()
             if not img: return

             # 2. Extract OCR Text (Subtitles/Slide Text)
             ocr_text = self.observer.extract_text_from_screen(img)
             
             # 3. Quick hash check to skip identical frames
             import hashlib
             current_hash = hashlib.md5(ocr_text.encode()).hexdigest()
             if current_hash == self.last_ocr_text_cache:
                 return
             self.last_ocr_text_cache = current_hash

             # 4. Neutral Assistance Payload (Requested by User)
             payload = {
                 "type": "video_suggestion",
                 "reason": "Need help with something on this page? I can help explain concepts related to what you're viewing.",
                 "confidence": 0.85,
                 "suggestions": [
                     {"label": "Ask Anything", "hint": "Ask a question about this page"},
                     {"label": "Explain Concepts", "hint": "Explain visible concepts"},
                     {"label": "Key Points", "hint": "Extract key points"}
                 ]
             }

             if payload and isinstance(payload, dict):
                 win_title = snapshot.get('window_title', 'Video/Browser')
                 # Store proactive context
                 self.last_proactive_context = {
                     'mode_primary': snapshot.get('mode_primary', 'video'),
                     'window_title': win_title,
                     'reason': payload.get('reason', ''),
                     'ocr_text': getattr(self.observer, 'last_ocr_text', ''),
                     'screenshot': getattr(self.observer, 'last_proactive_screenshot', None)
                 }

                 self.last_suggestion_time = time.time()
                 self.observer.signals.suggestion_ready.emit(payload)
             self.last_llm_call_time = time.time()

        except Exception as e:
            print(f"Copilot Video Handler Error: {e}")
