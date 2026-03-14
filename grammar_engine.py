"""
CORA Grammar Engine — Grammarly-like writing analysis using Gemini.
Works with Word COM, OCR text fields, and WhatsApp.
"""

import threading
import time
from typing import Callable, Optional


class GrammarEngine:
    """Real-time grammar analysis engine."""

    def __init__(self, ai_engine):
        self._ai      = ai_engine
        self._last_text    = ""
        self._last_check   = 0
        self._check_interval = 15  # seconds
        self._callback    = None
        self._lock        = threading.Lock()
        print("[GRAMMAR] Engine initialized")

    def set_callback(self, cb: Callable):
        """Called when grammar issues are found."""
        self._callback = cb

    def check_text(self, text: str, source: str = 'ocr', force: bool = False):
        """
        Analyze text for grammar issues.
        source: 'word', 'ocr', 'whatsapp'
        force: skip cooldown check
        """
        if not text or len(text.strip()) < 20:
            return

        now = time.time()
        with self._lock:
            # Skip if same text or too soon
            if not force:
                if text.strip() == self._last_text.strip():
                    return
                if now - self._last_check < self._check_interval:
                    return

            self._last_text  = text
            self._last_check = now

        print(f"[GRAMMAR] Checking {len(text)}ch from {source}")
        threading.Thread(
            target  = self._analyze,
            args    = (text, source),
            daemon  = True,
        ).start()

    def _analyze(self, text: str, source: str):
        """Run grammar analysis via Gemini."""
        try:
            prompt = self._build_grammar_prompt(text, source)
            result = self._ai._call_llm(prompt)
            parsed = self._parse_result(result, text)

            if parsed and self._callback:
                print(f"[GRAMMAR] Issues found: {parsed.get('issue_count', 0)}")
                self._callback(parsed)

        except Exception as e:
            print(f"[GRAMMAR] Analysis error: {e}")

    def _build_grammar_prompt(self, text: str, source: str) -> str:
        context = {
            'word':      'Microsoft Word document',
            'ocr':       'text on screen',
            'whatsapp':  'WhatsApp message',
            'general':   'text field',
        }.get(source, 'text')

        return f"""You are a grammar and writing assistant analyzing {context}.

TEXT TO ANALYZE:
{text[:2000]}

Analyze for:
1. Grammar and spelling errors
2. Word choice improvements  
3. Sentences that can be simplified
4. Unclear or awkward phrasing

Respond in this EXACT format:
ISSUE_COUNT: <number, 0 if none>
SCORE: <1-10 writing quality score>
TONE: <formal/informal/neutral>

If ISSUE_COUNT is 0, just write:
ISSUE_COUNT: 0
SCORE: <score>
TONE: <tone>
SUMMARY: No issues found.

If issues exist:
ISSUE_COUNT: <n>
SCORE: <score>
TONE: <tone>
SUMMARY: <one sentence describing main issues>
ISSUE1: <exact problematic phrase from text>
FIX1: <corrected version>
REASON1: <why in 5 words>
ISSUE2: <exact phrase>
FIX2: <corrected version>
REASON2: <why>
ISSUE3: <exact phrase>
FIX3: <corrected version>
REASON3: <why>
FULL_CORRECTION: <complete corrected version of entire text>"""

    def _parse_result(self, result: str, original: str) -> Optional[dict]:
        """Parse grammar analysis result."""
        try:
            lines = {}
            for line in result.strip().splitlines():
                if ':' in line:
                    key, _, val = line.partition(':')
                    lines[key.strip().upper()] = val.strip()

            issue_count = int(lines.get('ISSUE_COUNT', '0'))
            score       = lines.get('SCORE', '8')
            tone        = lines.get('TONE', 'neutral')
            summary     = lines.get('SUMMARY', '')
            full_fix    = lines.get('FULL_CORRECTION', '')

            issues = []
            for i in range(1, issue_count + 1):
                issue  = lines.get(f'ISSUE{i}', '')
                fix    = lines.get(f'FIX{i}', '')
                reason = lines.get(f'REASON{i}', '')
                if issue and fix:
                    issues.append({
                        'issue':  issue,
                        'fix':    fix,
                        'reason': reason,
                    })

            return {
                'issue_count':    issue_count,
                'score':          score,
                'tone':           tone,
                'summary':        summary,
                'issues':         issues,
                'full_correction': full_fix or original,
                'original':       original,
            }

        except Exception as e:
            print(f"[GRAMMAR] Parse error: {e}")
            return None

    def check_on_demand(self, text: str, source: str = 'ocr'):
        """Force immediate check bypassing cooldown."""
        self.check_text(text, source, force=True)
