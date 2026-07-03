from datetime import datetime
import json


def handler(request):
    """Serverless function handler for health check endpoint"""
    response = {
        "status": "healthy",
        "service": "TradingAgents",
        "version": "0.2.5",
        "timestamp": datetime.utcnow().isoformat()
    }

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(response),
    }
