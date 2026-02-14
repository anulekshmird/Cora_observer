import time
import mss
import ollama
from PIL import Image
import io
import os
import config
import json
import re
from PyQt6.QtCore import QObject, pyqtSignal

class ObserverSignal(QObject):
    suggestion_ready = pyqtSignal(object) # json payload
    prepare_capture = pyqtSignal()
    finished_capture = pyqtSignal()

class Observer:
    def __init__(self):
        self.running = False
        self.paused = False
        self.stop_flag = False
        self.signals = ObserverSignal()
        self.model = config.OLLAMA_MODEL 
        self.chat_history = [] # For maintaining context
        
    def stop_chat(self):
        self.stop_flag = True
        print("Stopping generation...")

    def clear_history(self):
        self.chat_history = []
        print("Chat history cleared.")

    # ... (capture_screen, _image_to_bytes, pause, resume, analyze, read_file_content unused changes omitted)

    def stream_chat_with_screen(self, user_query, attachment=None):
        self.stop_flag = False
        try:
            image_bytes = None
            
            # 1. Prepare User Message for this turn
            user_content = user_query
            current_images = []
            
            # 2. Handle Attachment (Priority Context)
            if attachment:
                print(f"Reading attachment: {attachment}")
                content = self.read_file_content(attachment)
                # Inject file content into the prompt context for this turn
                file_context = f"\n\n[PRIORITY CONTEXT - ATTACHED FILE: {os.path.basename(attachment)}]:\n{content}\n[END FILE]\n"
                user_content = file_context + "\n" + user_content
                print("Attachment processed. Skipping screen capture for this turn.")
                
            else:
                # 3. Handle Screen Context (Only if no file attached this turn)
                # We interpret "responding based on screen" issues as:
                # If the user is just chatting (continuation), we might NOT want to capture screen every single time if it distracts?
                # But generally, we should capture screen unless they are focused on a file.
                # Since we are now maintaining history, the previous file context is preserved.
                
                print("Capturing screen for chat context...")
                img = self.capture_screen()
                image_bytes = self._image_to_bytes(img)
                if image_bytes:
                    current_images.append(image_bytes)
                else:
                    yield "Error: Could not capture screen."
                    return

            print(f"Streaming from Ollama ({self.model})... History len: {len(self.chat_history)}")
            
            # 4. Construct Message Object
            new_message = {
                'role': 'user',
                'content': user_content
            }
            if current_images:
                new_message['images'] = current_images
                
            # 5. Append to History
            self.chat_history.append(new_message)
            
            # 6. Prepare Payload (System Prompt + History)
            # We inject the System Prompt as the first message
            messages_payload = [{'role': 'system', 'content': config.CHAT_SYSTEM_PROMPT}] + self.chat_history
            
            stream = ollama.chat(model=self.model, messages=messages_payload, stream=True)

            full_response = ""
            for chunk in stream:
                if self.stop_flag:
                    print("Stream stopped by user.")
                    break
                token = chunk['message']['content']
                full_response += token
                yield token
            
            # 7. Append Assistant Response to History
            self.chat_history.append({'role': 'assistant', 'content': full_response})

        except Exception as e:
            print(f"Stream Error: {e}")
            yield f"[Error: {e}]"

    def capture_screen(self):
        try:
            # 1. Hide UI (Prevent recursion)
            self.signals.prepare_capture.emit()
            time.sleep(0.3) # Give UI time to vanish
            
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                # Downscale for performance, but keep readable (2048px for text)
                img.thumbnail((2048, 2048)) 
                
            # 2. Restore UI
            self.signals.finished_capture.emit()
            return img
            
        except Exception as e:
            print(f"Screen Capture Error: {e}")
            self.signals.finished_capture.emit() # Always restore
            return None

    def _image_to_bytes(self, image):
        if not image: return None
        with io.BytesIO() as output:
            image.save(output, format='JPEG', quality=80)
            return output.getvalue()

    def pause(self):
        self.paused = True
        print("Observer Paused for Chat.")

    def resume(self):
        self.paused = False
        print("Observer Resumed.")

    def analyze(self, image):
        try:
            image_bytes = self._image_to_bytes(image)
            # print("Silent Analysis...") 
            
            response = ollama.chat(model=self.model, messages=[
                {
                    'role': 'user',
                    'content': config.SYSTEM_PROMPT,
                    'images': [image_bytes]
                }
            ])
            text = response['message']['content'].strip()
            
            # Clean JSON (sometimes LLMs add markdown)
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
                
            data = json.loads(text)
            
            # Confidence Check
            confidence = data.get("confidence", 0.0)
            if confidence >= config.PROACTIVE_THRESHOLD:
                return data
            else:
                # print(f"Low Confidence ({confidence}): {data.get('reason')}")
                return None
                
        except json.JSONDecodeError:
            print("Observer: Failed to parse JSON from AI.")
            # print(text) 
            return None
        except Exception as e:
            print(f"Ollama Silent Error: {e}")
            return None

    def read_file_content(self, path):
        try:
            if not path: return None
            _, ext = os.path.splitext(path)
            ext = ext.lower()
            
            # 1. Try PDF
            if ext == '.pdf':
                try:
                    import pypdf
                    reader = pypdf.PdfReader(path)
                    text = ""
                    for page in reader.pages[:10]: # Increased page limit to 10
                         extract = page.extract_text()
                         if extract:
                             text += extract + "\n"
                    
                    if len(text.strip()) < 50:
                        return f"[WARNING: Extracted text from PDF is very short ({len(text)} chars). The PDF might be scanned images, which I cannot read directly. Please open the PDF on your screen so I can 'see' it instead.]"
                        
                    print(f"PDF Parsing Success: {len(text)} chars extracted.")
                    return text
                except ImportError:
                    return f"[PDF detected at {path}. Install 'pypdf' to read content: `pip install pypdf`]"
                except Exception as e:
                    return f"[Error reading PDF: {e}]"

            # 2. Text/Code
            valid_exts = ['.txt', '.py', '.md', '.json', '.html', '.css', '.js', '.csv', '.bat', '.sh', '.xml', '.yaml', '.yml', '.ini', '.log']
            if ext not in valid_exts:
                return f"[File type '{ext}' not currently supported for deep analysis, but path is: {path}]"
            
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(50000) # Increased char limit
                return content
        except Exception as e:
            return f"[Error reading file: {e}]"

    def stream_chat_with_screen(self, user_query, attachment=None):
        self.stop_flag = False
        try:
            image_bytes = None
            file_context = ""
            
            # 1. Handle Attachment (Priority)
            if attachment:
                print(f"Reading attachment: {attachment}")
                content = self.read_file_content(attachment)
                file_context = f"\n\n[PRIORITY CONTEXT - ATTACHED FILE]:\n{content}\n[END FILE]\n"
                
                # CRITICAL: If a file is attached, DO NOT capture the screen.
                # This forces the model to focus 100% on the file content.
                # The user explicitly requested: "if im asking about file it needs to say about file"
                # and "not to read its on screen only the other screen" (which implies ignoring screen context if file is present).
                print("Skipping screen capture to focus on attachment.")
            
            else:
                # 2. Handle Screen Context (Only if no file)
                print("Capturing screen for chat context...")
                img = self.capture_screen()
                image_bytes = self._image_to_bytes(img)
                if not image_bytes:
                    yield "Error: Could not capture screen."
                    return

            print(f"Streaming from Ollama ({self.model})...")
            
            full_prompt = f"{config.CHAT_SYSTEM_PROMPT}\n{file_context}\nUSER: {user_query}\nCORA:"
            
            payload = {
                'role': 'user',
                'content': full_prompt
            }
            
            # Only add image if we captured it
            if image_bytes:
                payload['images'] = [image_bytes]

            stream = ollama.chat(model=self.model, messages=[payload], stream=True)

            for chunk in stream:
                if self.stop_flag:
                    print("Stream stopped by user.")
                    break
                token = chunk['message']['content']
                yield token

        except Exception as e:
            print(f"Stream Error: {e}")
            yield f"[Error: {e}]"

    def loop(self):
        print("Observer started (Silent Mode)...")
        self.running = True
        while self.running:
            if self.paused:
                time.sleep(1)
                continue

            try:
                # 1. Capture
                img = self.capture_screen()
                
                # 2. Analyze (Silent Mode)
                payload = self.analyze(img)
                
                if payload:
                    reason = payload.get('reason', 'Unknown Reason')
                    confidence = payload.get('confidence', 0.0)
                    print(f"✨ PROACTIVE ({confidence}): {reason}")
                    self.signals.suggestion_ready.emit(payload)
                
            except Exception as e:
                print(f"Observer Loop Error: {e}")
            
            # Wait for next cycle
            time.sleep(config.CHECK_INTERVAL)

    def stop(self):
        self.running = False
