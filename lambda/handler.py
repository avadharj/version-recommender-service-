"""Lambda entry point for the Version Recommender Service."""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def lambda_handler(event: dict, context: object) -> dict:
    """Placeholder — implemented in Story 5.1."""
    return {
        "statusCode": 501,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"code": "NotImplemented", "message": "stub"}),
    }
