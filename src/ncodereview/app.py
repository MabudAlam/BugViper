# ruff: noqa: E402
from dotenv import load_dotenv

load_dotenv(override=True)

import logging

from fastapi import FastAPI

from common.job_models import PRReviewPayload

logger = logging.getLogger(__name__)

app = FastAPI(title="BugViper Code Review Worker", version="0.1.0")


@app.post("/tasks/review")
async def handle_review(payload: PRReviewPayload):
    """Run a sandboxed DeepAgent review, called by Cloud Tasks."""
    logger.info(
        "Review task received: %s/%s#%d type=%s",
        payload.owner,
        payload.repo,
        payload.pr_number,
        payload.review_type,
    )
    from ncodereview import run_review_pipeline

    await run_review_pipeline(
        owner=payload.owner,
        repo=payload.repo,
        pr_number=payload.pr_number,
        review_type=payload.review_type,
        comment_id=payload.comment_id,
    )
    return {"status": "ok"}
