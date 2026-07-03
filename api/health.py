from http.server import BaseHTTPRequestHandler
from datetime import datetime
import json


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        response = {
            "status": "healthy",
            "service": "TradingAgents",
            "version": "0.2.5",
            "timestamp": datetime.utcnow().isoformat(),
        }
        body = json.dumps(response).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
