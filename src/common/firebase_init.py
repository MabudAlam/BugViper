"""Firebase Admin SDK initialization (shared by main API and ingestion service)."""

import json
import logging
import os

import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)


def _get_firebase_credentials():
    """Parse SERVICE_FILE_LOC as a JSON string or file path."""
    cert_value = os.environ.get("SERVICE_FILE_LOC", "")
    if cert_value.strip().startswith("{"):
        return credentials.Certificate(json.loads(cert_value))
    return credentials.Certificate(cert_value)


def _initialize_firebase():
    """Initialize the Firebase Admin SDK (idempotent). Returns a Firestore client."""
    if firebase_admin._apps:
        return firestore.client()

    cert_value = os.environ.get("SERVICE_FILE_LOC", "")
    if cert_value:
        cred = _get_firebase_credentials()
        firebase_admin.initialize_app(cred)
        logger.info("Firebase initialized with explicit credentials")
    else:
        firebase_admin.initialize_app()
        logger.info("Firebase initialized with default credentials (Cloud Run)")

    return firestore.client()
