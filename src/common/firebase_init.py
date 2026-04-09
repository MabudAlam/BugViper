"""Firebase Admin SDK initialization (shared by main API and ingestion service)."""

import json
import logging
import os

import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)

isLocal = True


def _get_firebase_credentials():
    cert_value = os.environ.get("SERVICE_FILE_LOC", "")
    if not cert_value or not cert_value.strip():
        return None
    if cert_value.strip().startswith("{"):
        return credentials.Certificate(json.loads(cert_value))
    return credentials.Certificate(cert_value)


def _initialize_firebase():
    if firebase_admin._apps:
        return firestore.client()

    if isLocal:
        cred = _get_firebase_credentials()
        if not cred:
            raise ValueError("FIREBASE_LOCAL=true but SERVICE_FILE_LOC is not set")
        firebase_admin.initialize_app(cred)
        logger.info("Firebase initialized with explicit credentials (local)")
    else:
        firebase_admin.initialize_app()
        logger.info("Firebase initialized with default credentials (Cloud Run)")

    return firestore.client()
