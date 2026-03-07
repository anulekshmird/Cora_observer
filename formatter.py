import re
import base64

class ResponseFormatter:
    """
    Parses Cora's structured LLM output and converts it into styled HTML for the Chat UI.
    Supports: ⚠ Error, Cause, Fix, Commands.
    """
    
    @staticmethod
    def format(text):
        if not text:
            return ""
            
        # Filter internal JSON blocks that Cora sometimes outputs
        if text.strip().startswith("{") and text.strip().endswith("}"):
            return ""
        
        # Remove any markdown JSON blocks present in the output
        text = re.sub(r"```json\s*{.*?}\s*```", "", text, flags=re.DOTALL)
        text = text.strip()
            
        # 1. Handle Code Blocks first (preserve them)
        code_blocks = []
        def save_code(match):
            placeholder = f"__CODE_BLOCK_{len(code_blocks)}__"
            code_blocks.append(match.group(0))
            return placeholder
        
        text = re.sub(r"```.*?```", save_code, text, flags=re.DOTALL)
        
        # 2. Define Section Styles
        # Using dark theme colors matching Cora's UI
        
        # ⚠ Error Section
        text = re.sub(
            r"⚠ Error\n?(.*)", 
            r'<div style="background-color: #451a1a; border-left: 4px solid #ef4444; padding: 10px; margin-bottom: 10px; border-radius: 4px;">'
            r'<b style="color: #f87171; font-size: 16px;">⚠ Error</b><br><span style="color: #fca5a5;">\1</span></div>', 
            text
        )
        
        # Cause Section
        text = re.sub(
            r"(?i)Cause\n?(.*)", 
            r'<div style="margin-top: 10px; margin-bottom: 5px;"><b style="color: #60a5fa;">🔍 Cause</b></div>'
            r'<div style="color: #93c5fd; margin-left: 10px; margin-bottom: 10px;">\1</div>', 
            text
        )
        
        # Fix Section
        text = re.sub(
            r"(?i)Fix\n?", 
            r'<div style="margin-top: 10px; margin-bottom: 5px;"><b style="color: #34d399;">🛠 Fix</b></div>', 
            text
        )
        
        # Commands Section
        text = re.sub(
            r"(?i)Commands\n?", 
            r'<div style="margin-top: 10px; margin-bottom: 5px;"><b style="color: #fbbf24;">⌨ Commands</b></div>', 
            text
        )
        
        # 3. Restore Code Blocks with Styling
        for i, code in enumerate(code_blocks):
            # Extract content from ```lang content ```
            match = re.search(r"```(?P<lang>\w+)?\n?(?P<code>.*?)```", code, re.DOTALL)
            if match:
                lang = match.group('lang') or "code"
                code_content = match.group('code').strip()
                
                # Encode for copy link
                b64_code = base64.b64encode(code_content.encode()).decode()
                
                styled_code = (
                    f'<div style="background-color: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 10px; font-family: \'Consolas\', \'Courier New\', monospace; margin-top: 5px; margin-bottom: 10px;">'
                    f'<table width="100%" style="margin-bottom: 5px;">'
                    f'<tr>'
                    f'<td style="color: #64748b; font-size: 11px; text-transform: uppercase;">{lang}</td>'
                    f'<td align="right"><a href="copy:{b64_code}" style="color: #3b82f6; font-size: 10px; text-decoration: none; font-weight: bold; background-color: #1e293b; padding: 2px 6px; border-radius: 4px;">COPY</a></td>'
                    f'</tr>'
                    f'</table>'
                    f'<pre style="color: #e2e8f0; margin: 0; white-space: pre-wrap;">{code_content}</pre>'
                    f'</div>'
                )
                text = text.replace(f"__CODE_BLOCK_{i}__", styled_code)
            else:
                text = text.replace(f"__CODE_BLOCK_{i}__", f"<pre>{code}</pre>")

        # 4. Clean up lists and basic markdown
        # Convert - list item to bullet
        text = re.sub(r"^\s*-\s+(.*)", r"• \1", text, flags=re.MULTILINE)
        # Convert 1. list item
        text = re.sub(r"^\s*(\d+)\.\s+(.*)", r"<b>\1.</b> \2", text, flags=re.MULTILINE)
        
        # Newlines to <br> (but not inside the divs we already made)
        # We'll just replace \n with <br> if it's not at the end of a tag
        text = text.replace("\n", "<br>")
        
        return text
