import http.server
import socketserver
import os
import sys

PORT = 8765
Handler = http.server.SimpleHTTPRequestHandler

class DualStackServer(socketserver.TCPServer):
    allow_reuse_address = True

print(f"🚀 Starting Chopify Web Studio on 0.0.0.0:{PORT}...")
try:
    with DualStackServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"✅ Studio Server ACTIVE! Listening on port {PORT}")
        httpd.serve_forever()
except Exception as e:
    print("Server error:", e)
