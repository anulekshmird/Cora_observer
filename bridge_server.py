
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Callback storage
_diagnostics_callback = None

def set_diagnostics_callback(cb):
    global _diagnostics_callback
    _diagnostics_callback = cb

class BridgeHandler(BaseHTTPRequestHandler):
    context_engine = None # Class variable or set via server

    def do_POST(self):
        if self.path == '/update_buffer':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                file_path = data.get('file_path')
                content = data.get('buffer_content')
                
                if file_path and content is not None:
                    # Update Context Engine
                    if BridgeHandler.context_engine:
                        BridgeHandler.context_engine.update_buffer(file_path, content)
                    
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"status": "ok"}')
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"status": "bad_request"}')
                    
            except Exception as e:
                print(f"Bridge Server Error: {e}")
                self.send_response(500)
                self.end_headers()
        elif self.path == '/diagnostics':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                errors = data.get('errors', [])
                error_count = data.get('error_count', 0)
                
                print(f'[BRIDGE] Diagnostics: {error_count} error(s)')
                
                if error_count > 0:
                    # Build rich error context
                    error_lines = []
                    for e in errors[:5]:  # max 5 errors
                        error_lines.append(
                            f"• {e['file']} line {e['line']}: {e['message']}"
                        )
                    error_text = '\n'.join(error_lines)
                    print(f'[BRIDGE] Errors:\n{error_text}')

                    # Notify main app via callback
                    if _diagnostics_callback:
                        _diagnostics_callback(errors, error_count, error_text)
                
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            except Exception as e:
                print(f'[BRIDGE] Diagnostics error: {e}')
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'{"status": "error"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass # Suppress logging

class BridgeServer(threading.Thread):
    def __init__(self, context_engine, port=54321):
        super().__init__()
        self.context_engine = context_engine
        self.port = port
        self.server = None
        self.daemon = True # Auto-kill on exit

    def run(self):
        # Set shared context
        BridgeHandler.context_engine = self.context_engine
        
        self.server = ThreadingHTTPServer(('127.0.0.1', self.port), BridgeHandler)
        print(f"Bridge Server running on http://127.0.0.1:{self.port}")
        self.server.serve_forever()

    def stop(self):
        if self.server:
            self.server.shutdown()
