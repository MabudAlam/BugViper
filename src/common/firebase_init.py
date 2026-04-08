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
    if not cert_value or not cert_value.strip():
        return None
    if cert_value.strip().startswith("{"):
        return credentials.Certificate(json.loads(cert_value))
    return credentials.Certificate(cert_value)


def initialize_firebase_local():
    """Initialize Firebase using SERVICE_FILE_LOC (local development)."""
    if firebase_admin._apps:
        return firestore.client()

    cred = _get_firebase_credentials()
    if not cred:
        raise ValueError("SERVICE_FILE_LOC is required for local Firebase initialization")
    firebase_admin.initialize_app(cred)
    logger.info("Firebase initialized with explicit credentials (local)")
    return firestore.client()


def initialize_firebase_server():
    """Initialize Firebase using default credentials (Cloud Run / production)."""
    if firebase_admin._apps:
        return firestore.client()

    firebase_admin.initialize_app()
    logger.info("Firebase initialized with default credentials (Cloud Run)")
    return firestore.client()


def _initialize_firebase():
    """Default initialization — delegates to server mode."""
    return initialize_firebase_server()
