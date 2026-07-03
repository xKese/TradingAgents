from http.server import BaseHTTPRequestHandler
from datetime import datetime
import json


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle GET requests"""
        response = {
            "status": "healthy",
            "service": "TradingAgents",
            "version": "0.2.5",
            "timestamp": datetime.utcnow().isoformat()
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def do_HEAD(self):
        """Handle HEAD requests"""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
