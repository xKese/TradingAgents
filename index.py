"""
Simple WSGI application wrapper for Vercel deployment.
Routes health checks to the serverless function.
"""

from datetime import datetime
import json


def application(environ, start_response):
    """WSGI application entry point for Vercel"""
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")

    # Health check endpoint
    if path == "/api/health" and method in ["GET", "HEAD"]:
        response = {
            "status": "healthy",
            "service": "TradingAgents",
            "version": "0.2.5",
            "timestamp": datetime.utcnow().isoformat()
        }

        status = "200 OK"
        headers = [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(json.dumps(response))))
        ]
        start_response(status, headers)
        return [json.dumps(response).encode("utf-8")]

    # Root endpoint
    elif path == "/" and method in ["GET", "HEAD"]:
        response = {
            "message": "TradingAgents API",
            "endpoints": {
                "health": "/api/health"
            }
        }

        status = "200 OK"
        headers = [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(json.dumps(response))))
        ]
        start_response(status, headers)
        return [json.dumps(response).encode("utf-8")]

    # Not found
    else:
        response = {"error": "Not found"}
        status = "404 Not Found"
        headers = [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(json.dumps(response))))
        ]
        start_response(status, headers)
        return [json.dumps(response).encode("utf-8")]


# For local testing
if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    server = make_server("127.0.0.1", 8000, application)
    print("Serving on http://127.0.0.1:8000")
    server.serve_forever()
