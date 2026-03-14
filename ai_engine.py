"""
Layer 4: AI ENGINE
Only component that calls the LLM.
Receives Context, builds prompt, returns response.
No UI logic. No window detection.
"""
import os
import threading
import json
from PyQt6.QtCore import QObject, pyqtSignal
from context_extractor import Context

class AIEngine(QObject):
    suggestion_ready = pyqtSignal(dict)   # proactive suggestion payload
    stream_chunk     = pyqtSignal(str)    # streaming chat token
    stream_done      = pyqtSignal()
    error_occurred   = pyqtSignal(str)

    def __init__(self, model_name: str = "models/gemini-2.5-flash"):
        super().__init__()
        self._model          = model_name
        self._lock           = threading.Lock()
        self._generating     = False
        self._last_call_time = 0
        self._min_call_interval = 15.0
        self._retry_after    = 0
        self._stop_requested = False

        # Use new google-genai SDK
        try:
            from google import genai
            from google.genai import types
            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                print("WARNING: GEMINI_API_KEY not set")
            self._client = genai.Client(api_key=api_key)
            self._types  = types
            self._sdk    = "new"
            print(f"AIEngine: Gemini Client (v2) ready ({model_name})")
        except ImportError:
            try:
                import google.generativeai as genai
                api_key = os.getenv("GEMINI_API_KEY", "")
                genai.configure(api_key=api_key)
                self._client = genai.GenerativeModel(model_name)
                self._sdk    = "old"
                print(f"AIEngine: Gemini Client (v1) ready ({model_name})")
            except Exception as e:
                print(f"AIEngine: Gemini init failed: {e}")
                self._client = None
                self._sdk    = None

    # ── Proactive suggestion ──────────────────────────────────────────────
    def generate_suggestion_async(self, ctx: Context) -> None:
        """Generate a proactive suggestion for the current context."""
        if self._generating:
            return
        threading.Thread(
            target=self._generate_suggestion,
            args=(ctx,),
            daemon=True,
        ).start()

    def _generate_suggestion(self, ctx: Context) -> None:
        import time
        now = time.time()

        # Respect retry-after from quota errors
        if now < self._retry_after:
            wait = int(self._retry_after - now)
            print(f"AIEngine: Rate limited — waiting {wait}s")
            return

        # Enforce minimum interval between calls
        if (now - self._last_call_time) < self._min_call_interval:
            return

        with self._lock:
            self._generating = True
        try:
            self._last_call_time = now
            prompt   = self._build_suggestion_prompt(ctx)
            response = self._call_llm(prompt, ctx.image)
            payload  = self._parse_suggestion_response(response, ctx)
            self.suggestion_ready.emit(payload)
        except Exception as e:
            print(f"AIEngine suggestion error: {e}")
            # Parse retry delay from 429 errors
            err_str = str(e)
            if '429' in err_str or 'quota' in err_str.lower():
                import re
                match = re.search(r'retry_delay\s*\{\s*seconds:\s*(\d+)', err_str)
                delay = int(match.group(1)) if match else 60
                self._retry_after = time.time() + delay
                print(f"AIEngine: Quota hit — backing off {delay}s")
            self.error_occurred.emit(str(e))
        finally:
            with self._lock:
                self._generating = False

    def _build_suggestion_prompt(self, ctx) -> str:
        best  = ctx.best_text()[:3000] if ctx.best_text() else ''
        app   = ctx.app          or 'general'
        win   = ctx.window_title or ''
        url   = getattr(ctx, 'url',   '') or ''
        extra = getattr(ctx, 'extra', {}) or {}

        # Rules for what chips AI CAN do
        can_do_rules = """STRICT CHIP RULES — only suggest things an AI can answer in text:
✅ ALLOWED chips: Explain, Summarize, Fix, Improve, Define, Analyze, Compare, Translate, Review, Find bugs, Optimize, Rewrite, Key points, Pros and cons, Describe
❌ FORBIDDEN chips: Open tab, Search web, Click, Navigate, Download, Install, Run, Execute, Continue chat, Start new chat, Find videos, Open browser, Go to, Visit

Each chip must be something I can answer RIGHT NOW with text only."""

        if app == 'youtube' and extra:
            yt_title = extra.get('title', '')
            yt_desc  = extra.get('description', '')[:500]
            yt_chan   = extra.get('channel', '')
            yt_dur   = extra.get('duration', 0)
            return (
                f"{can_do_rules}\n\n"
                f"YouTube Video:\n"
                f"Title: {yt_title}\n"
                f"Channel: {yt_chan}\n"
                f"Duration: {yt_dur//60}m\n"
                f"Description: {yt_desc}\n\n"
                f"Give ONE insight title and 3 chips.\n"
                f"Format:\n"
                f"TITLE: <specific insight about this video>\n"
                f"CHIP1: Summarize Video\n"
                f"CHIP2: About {yt_chan}\n"
                f"CHIP3: Key Topics\n"
                f"HINT1: Summarize this YouTube video in detail: Title={yt_title}, Channel={yt_chan}, Description={yt_desc}\n"
                f"HINT2: Tell me about {yt_chan} — their content style, popular videos, background\n"
                f"HINT3: What are the key topics covered in: {yt_title}? Description: {yt_desc}"
            )

        elif app == 'browser':
            return (
                f"{can_do_rules}\n\n"
                f"Browser page: {win}\nURL: {url}\n"
                f"Content: {best[:500]}\n\n"
                f"Give ONE insight and 3 chips the AI can answer from page content.\n"
                f"TITLE: <specific insight>\n"
                f"CHIP1: <label>\nCHIP2: <label>\nCHIP3: <label>\n"
                f"HINT1: <full question>\nHINT2: <full question>\nHINT3: <full question>"
            )

        elif app == 'word':
            return (
                f"{can_do_rules}\n\n"
                f"Word document: {win}\n\n"
                f"DOCUMENT TEXT:\n{best}\n\n"
                f"Give ONE writing-specific insight and 3 chips.\n"
                f"TITLE: <specific insight about the writing>\n"
                f"CHIP1: <label>\nCHIP2: <label>\nCHIP3: <label>\n"
                f"HINT1: Fix all grammar in this text:\\n{best[:1500]}\n"
                f"HINT2: Improve clarity of:\\n{best[:1500]}\n"
                f"HINT3: Summarize in 3 bullets:\\n{best[:1500]}"
            )

        elif app in ('editor', 'general') and best:
            error_kw = ['error', 'exception', 'traceback', 'syntaxerror',
                        'nameerror', 'invalid syntax', 'unexpected']
            has_error = any(k in best.lower() for k in error_kw)
            if has_error:
                return (
                    f"{can_do_rules}\n\n"
                    f"Code with error:\n{best}\n\n"
                    f"TITLE: Error detected — fix available\n"
                    f"CHIP1: Fix Error\nCHIP2: Explain Error\nCHIP3: Show Corrected Code\n"
                    f"HINT1: Fix this error and show corrected code:\\n{best[:2000]}\n"
                    f"HINT2: Explain what caused this error:\\n{best[:2000]}\n"
                    f"HINT3: Show me the corrected version of:\\n{best[:2000]}"
                )
            return (
                f"{can_do_rules}\n\n"
                f"Code: {win}\n\n{best}\n\n"
                f"TITLE: <specific insight about this code>\n"
                f"CHIP1: Explain Code\nCHIP2: Find Bugs\nCHIP3: Optimize\n"
                f"HINT1: Explain what this code does:\\n{best[:2000]}\n"
                f"HINT2: Find bugs in:\\n{best[:2000]}\n"
                f"HINT3: Optimize this code:\\n{best[:2000]}"
            )

        elif app == 'pdf':
            return (
                f"{can_do_rules}\n\n"
                f"PDF: {win}\n\nContent:\n{best}\n\n"
                f"TITLE: <specific insight>\n"
                f"CHIP1: Summarize PDF\nCHIP2: Key Points\nCHIP3: Explain Section\n"
                f"HINT1: Summarize this PDF:\\n{best[:2000]}\n"
                f"HINT2: Extract key points from:\\n{best[:2000]}\n"
                f"HINT3: Explain this section:\\n{best[:2000]}"
            )

        else:
            if not best:
                return (
                    f"{can_do_rules}\n\n"
                    f"App: {app} | Window: {win}\n\n"
                    f"Suggest 3 things I can explain or answer for this app type.\n"
                    f"TITLE: Ready to help\n"
                    f"CHIP1: What's on screen?\nCHIP2: Summarize\nCHIP3: Ask me anything\n"
                    f"HINT1: Look at this screen and tell me what's visible and what would be most helpful\n"
                    f"HINT2: Summarize what's on screen\n"
                    f"HINT3: What can you help me with right now?"
                )
            return (
                f"{can_do_rules}\n\n"
                f"Window: {win}\nContent:\n{best}\n\n"
                f"TITLE: <specific insight>\n"
                f"CHIP1: <label>\nCHIP2: <label>\nCHIP3: <label>\n"
                f"HINT1: <question with content>\n"
                f"HINT2: <question with content>\n"
                f"HINT3: <question with content>"
            )



    def _parse_suggestion_response(self, text: str, ctx) -> dict:
        lines  = {}
        for line in text.strip().splitlines():
            if ':' in line:
                key, _, val = line.partition(':')
                lines[key.strip().upper()] = val.strip()

        title = lines.get('TITLE', 'Suggestion ready')
        chips = []
        for i in range(1, 4):
            label = lines.get(f'CHIP{i}', '')
            hint  = lines.get(f'HINT{i}', label)
            if label:
                chips.append({"label": label, "hint": hint})

        if not chips:
            # Fallback parse — treat whole response as title
            chips = [{"label": "Ask Cora", "hint": text[:200]}]

        return {
            "type":        ctx.app or "general",
            "reason":      title,
            "reason_long": ctx.best_text()[:200] if ctx.best_text() else "",
            "confidence":  0.9,
            "suggestions": chips,
        }

    # ── Chat response ─────────────────────────────────────────────────────
    def stop_stream(self):
        """Stop current streaming generation."""
        self._stop_requested = True
        print('[AI] Stop requested')

    def stream_chat_async(self, message: str, ctx, history: list = None):
        import threading
        self._stop_requested = False  # Reset on new stream
        print(f'[AI] stream_chat_async: message={len(message)}ch')
        prompt = self._build_chat_prompt(message, ctx, history or [])
        print(f'[AI] Built prompt: {len(prompt)}ch')
        t = threading.Thread(
            target = self._stream_llm,
            args   = (prompt,),
            daemon = True,
        )
        t.start()


    def _build_chat_prompt(self, message: str, ctx, history: list = None) -> str:
        if history is None:
            history = []

        best  = ctx.best_text()[:4000] if ctx and ctx.best_text() else ''
        app   = ctx.app          if ctx else 'general'
        win   = ctx.window_title if ctx else ''
        url   = getattr(ctx, 'url',   '') if ctx else ''
        extra = getattr(ctx, 'extra', {}) if ctx else {}

        msg_lower = message.lower()

        # ── Detect intent for length control ──────────────────
        is_error_fix   = any(k in msg_lower for k in [
            'fix error', 'fix bug', 'show fix', '🔴', 'fix this',
            'correct', 'debug', 'solve error',
        ])
        is_code_task   = any(k in msg_lower for k in [
            'explain code', 'optimize', 'refactor', 'add comments',
            'find bugs', 'review code', 'rewrite',
        ])
        is_summary     = any(k in msg_lower for k in [
            'summarize', 'summary', 'key points', 'tldr',
            'brief', 'overview', 'what is this',
        ])
        is_definition  = any(k in msg_lower for k in [
            'define', 'what is', 'what are', 'meaning of',
            'explain what', 'tell me about',
        ])
        is_writing     = any(k in msg_lower for k in [
            'fix grammar', 'improve', 'rewrite', 'make formal',
            'rephrase', 'paraphrase', 'proofread',
        ])
        is_quick       = any(k in msg_lower for k in [
            'synonyms', 'translate', 'how many', 'when was',
            'who is', 'what does', 'spell',
        ])

        # ── Length instruction per intent ─────────────────────
        if is_error_fix:
            length_rule = (
                "FORMAT FOR ERROR FIX:\n"
                "1. One sentence: what the error is\n"
                "2. Show ORIGINAL broken code in ```python block\n"
                "3. Show FIXED code in ```python block with comment on changed line\n"
                "4. One sentence: why this fixes it\n"
                "Keep total response under 20 lines."
            )
        elif is_code_task:
            length_rule = (
                "FORMAT FOR CODE TASK:\n"
                "- Brief explanation (2-3 sentences)\n"
                "- Show code in ```language block\n"
                "- Keep explanation concise — let code speak\n"
                "- Max 30 lines total"
            )
        elif is_summary:
            length_rule = (
                "FORMAT FOR SUMMARY:\n"
                "- 3-5 bullet points maximum\n"
                "- Each bullet: one clear sentence\n"
                "- No preamble, no conclusion paragraph\n"
                "- Start directly with first bullet"
            )
        elif is_writing:
            length_rule = (
                "FORMAT FOR WRITING FIX:\n"
                "- Show corrected/improved text directly\n"
                "- After text: 1-2 sentences on what was changed\n"
                "- No lengthy explanations"
            )
        elif is_definition:
            length_rule = (
                "FORMAT FOR EXPLANATION:\n"
                "- 2-4 sentences for simple questions\n"
                "- Use bullet points only if listing multiple items\n"
                "- Include one relevant example if helpful\n"
                "- Max 10 lines"
            )
        elif is_quick:
            length_rule = (
                "FORMAT: Answer in 1-3 lines maximum. Be direct."
            )
        else:
            length_rule = (
                "FORMAT: Match response length to question complexity.\n"
                "- Simple question → 2-4 sentences\n"
                "- Complex question → use bullets, max 10 lines\n"
                "- Always lead with the answer, not preamble"
            )

        system = f"""You are Cora, an intelligent desktop AI assistant.

RULES:
1. Answer DIRECTLY — no "Sure!", "Great question!", "Certainly!"
2. NEVER describe UI elements, toolbars, or say "click Problems tab"
3. NEVER say "I cannot see" or "please provide" — use what's given
4. For code — always use ```language blocks so user can copy
5. {length_rule}"""

        # Add after main system rules
        if app == 'ai_chat' or 'prompt' in msg_lower:
            system += (
                "\n\nPROMPT ENGINEERING MODE:\n"
                "- Format all generated prompts in ```text blocks so user can copy\n"
                "- Include role, context, task, format instructions in each prompt\n"
                "- Make prompts specific and detailed\n"
                "- After giving prompt, explain in 1 sentence why it works well"
            )

        parts = [system, '']

        # Context per app
        if app == 'ai_chat':
            win_lower = win.lower()
            ai_name   = 'Claude' if 'claude' in win_lower else \
                         'ChatGPT' if 'chatgpt' in win_lower else \
                         'Gemini' if 'gemini' in win_lower else 'AI assistant'
            parts.append(
                f"Context: User is currently using {ai_name}.\n"
                f"Your role: Act as a prompt engineering expert.\n"
                f"Help the user craft better prompts for {ai_name}.\n"
                f"When writing prompts — format them clearly so user can copy-paste directly.\n"
            )
        elif app == 'youtube' and extra:
            yt_title   = extra.get('title', '')
            yt_desc    = extra.get('description', '')[:800]
            yt_channel = extra.get('channel', '')
            yt_dur     = extra.get('duration', 0)
            parts.append(
                f"YouTube: {yt_title}\n"
                f"Channel: {yt_channel} | Duration: {yt_dur//60}m\n"
                f"Description: {yt_desc}\n"
            )
        elif app in ('editor', 'code') and best:
            parts.append(f"CODE ({win}):\n```\n{best}\n```\n")
        elif app == 'word' and best:
            parts.append(f"DOCUMENT:\n{best}\n")
        elif app == 'pdf' and best:
            parts.append(f"PDF CONTENT:\n{best}\n")
        elif app == 'browser' and best:
            parts.append(f"PAGE ({url or win}):\n{best}\n")
        elif best:
            parts.append(f"SCREEN ({win}):\n{best}\n")

        if history:
            parts.append("HISTORY:")
            for h in history[-4:]:
                role = "User" if h.get("role") == "user" else "Assistant"
                parts.append(f"{role}: {h.get('content','')[:300]}")
            parts.append('')

        parts.append(f"User: {message}")
        parts.append("Cora:")

        return '\n'.join(parts)

    def _build_message_history(self, history: list, prompt: str) -> list:
        messages = []
        for turn in history[-6:]:  # last 6 turns for context
            messages.append({'role': turn['role'], 'content': turn['content']})
        messages.append({'role': 'user', 'content': prompt})
        return messages

    # ── LLM calls ────────────────────────────────────────────────────────
    def _call_llm(self, prompt: str, image: bytes = None) -> str:
        if not self._client:
            return ""
        try:
            if self._sdk == "new":
                response = self._client.models.generate_content(
                    model    = self._model,
                    contents = [prompt],
                )
                return response.text.strip()
            else:
                response = self._client.generate_content(prompt)
                return response.text.strip()
        except Exception as e:
            print(f"Gemini call error: {e}")
            return ""

    def _stream_llm(self, full_prompt: str):
        print(f'[AI] Starting stream, prompt={len(full_prompt)}ch')
        try:
            if self._sdk == 'new':
                from google.genai import types
                for chunk in self._client.models.generate_content_stream(
                    model    = self._model,
                    contents = [full_prompt],
                    config   = types.GenerateContentConfig(
                        max_output_tokens = 4096,
                        temperature       = 0.7,
                    )
                ):
                    if self._stop_requested:
                        print('[AI] Stream stopped by user')
                        break
                    try:
                        text = chunk.text
                        if text:
                            print(f'[AI] chunk: {len(text)}ch')
                            self.stream_chunk.emit(text)
                    except Exception as ce:
                        print(f'[AI] chunk error: {ce}')
                        continue
            else:
                # legacy SDK
                import google.generativeai as genai
                model = genai.GenerativeModel(self._model)
                response = model.generate_content(
                    full_prompt,
                    stream=True,
                )
                for chunk in response:
                    if self._stop_requested:
                        print('[AI] Stream stopped by user')
                        break
                    try:
                        if chunk.text:
                            self.stream_chunk.emit(chunk.text)
                    except Exception:
                        continue

            print('[AI] Stream complete')
            self.stream_done.emit()

        except Exception as e:
            print(f'[AI] Stream error: {e}')
            import traceback
            traceback.print_exc()
            
            err = str(e)
            if '429' in err or 'quota' in err.lower():
                import time, re
                match = re.search(r'retry_delay.*?seconds.*?(\d+)', err)
                delay = int(match.group(1)) if match else 60
                self._retry_after = time.time() + delay
                self.stream_chunk.emit(f"\n\n⏳ Rate limited — wait {delay}s and try again.")
            else:
                self.error_occurred.emit(str(e))
            
            self.stream_done.emit()
