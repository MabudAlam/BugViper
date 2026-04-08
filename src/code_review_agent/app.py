# ruff: noqa: E402
from dotenv import load_dotenv

# Load env vars BEFORE any imports that read them (Firebase, OpenRouter, etc.)
# override=True makes local dev deterministic even if the shell exported old values.
load_dotenv(override=True)

import logging

from fastapi import FastAPI

from api.services.review_service import review_pipeline
from common.job_models import PRReviewPayload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="BugViper Review Service", docs_url=None, redoc_url=None)


@app.get("/health")
def health():
    return {"status": "ok", "service": "review"}


@app.post("/tasks/review")
async def handle_review_task(payload: PRReviewPayload):
    """
    Receive a PR review task from Cloud Tasks and run the full pipeline.

    Always returns 200 so Cloud Tasks does not retry on review failures —
    errors are logged and posted as GitHub comments by execute_pr_review itself.
    """
    logger.info("Review task received: %s/%s#%s", payload.owner, payload.repo, payload.pr_number)
    try:
        await review_pipeline(payload.owner, payload.repo, payload.pr_number)
    except Exception:
        logger.exception(
            "Unhandled error in review task for %s/%s#%s",
            payload.owner,
            payload.repo,
            payload.pr_number,
        )
    return {"status": "ok"}
