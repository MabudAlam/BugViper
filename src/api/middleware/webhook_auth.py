import hashlib
import hmac
import logging
import os

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


def _verify_signature(raw_body: bytes, secret: str, signature_header: str) -> None:
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(
            status_code=403,
            detail="Missing or malformed X-Hub-Signature-256 header",
        )
    local_hash = hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(local_hash, signature_header.removeprefix("sha256=")):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")


async def verify_github_webhook_signature(request: Request) -> None:
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        logger.error("GITHUB_WEBHOOK_SECRET is not set")
        raise HTTPException(status_code=500, detail="Webhook secret not configured")
    _verify_signature(
        await request.body(),
        secret,
        request.headers.get("X-Hub-Signature-256", ""),
    )


async def verify_marketplace_webhook_signature(request: Request) -> None:
    secret = os.environ.get("GITHUB_MARKETPLACE_WEBHOOK_SECRET")
    if not secret:
        logger.warning(
            "GITHUB_MARKETPLACE_WEBHOOK_SECRET is not set — falling back to GITHUB_WEBHOOK_SECRET"
        )
        secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        logger.error("Neither GITHUB_MARKETPLACE_WEBHOOK_SECRET nor GITHUB_WEBHOOK_SECRET is set")
        raise HTTPException(status_code=500, detail="Webhook secret not configured")
    _verify_signature(
        await request.body(),
        secret,
        request.headers.get("X-Hub-Signature-256", ""),
    )
