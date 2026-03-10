"""Google Cloud Tasks dispatcher for ingestion jobs."""

import json
import logging
import os
from typing import Optional

from pydantic import BaseModel

from common.job_models import (
    IncrementalPRPayload,
    IncrementalPushPayload,
    IngestionTaskPayload,
    PRReviewPayload,
)

logger = logging.getLogger(__name__)


class CloudTasksService:
    """Dispatch ingestion tasks to a Cloud Run worker via Cloud Tasks.

    When ``INGESTION_SERVICE_URL`` is not set, ``is_enabled`` is ``False``
    and the main API should fall back to in-process execution.
    """

    def __init__(self):
        self._project = os.environ.get("GCP_PROJECT_ID", "")
        self._location = os.environ.get("GCP_LOCATION", "us-central1")
        self._queue = os.environ.get("CLOUD_TASKS_QUEUE", "ingestion-queue")
        self._review_queue = os.environ.get("CLOUD_TASKS_REVIEW_QUEUE", "codeReview")
        self._service_url = os.environ.get("INGESTION_SERVICE_URL", "")
        self._review_url = os.environ.get("REVIEW_SERVICE_URL", "")
        self._sa_email = os.environ.get("CLOUD_TASKS_SA_EMAIL", "")

    @property
    def is_enabled(self) -> bool:
        return bool(self._service_url)

    @property
    def review_is_enabled(self) -> bool:
        return bool(self._review_url)

    def _dispatch(
        self,
        path: str,
        payload: BaseModel,
        service_url: str | None = None,
        queue: str | None = None,
    ) -> Optional[str]:
        """Create a Cloud Task that POSTs to a Cloud Run service at *path*.

        ``service_url`` overrides the default ingestion service URL.
        ``queue`` overrides the default ingestion queue name.

        Returns the Cloud Task resource name, or ``None`` on failure.
        """
        target_url = (service_url or self._service_url).rstrip("/")
        if not target_url:
            logger.warning("Cloud Tasks not enabled — target service URL is unset")
            return None

        # Lazy import so the dependency is only required when Cloud Tasks is active
        from google.cloud import tasks_v2

        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(self._project, self._location, queue or self._queue)

        task_body = json.dumps(payload.model_dump()).encode()
        url = f"{target_url}{path}"

        task: dict = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": url,
                "headers": {"Content-Type": "application/json"},
                "body": task_body,
            },
            "dispatch_deadline": {"seconds": 1800},  # 30 minutes
        }

        # Add OIDC token for authenticated Cloud Run services
        if self._sa_email:
            task["http_request"]["oidc_token"] = {
                "service_account_email": self._sa_email,
                "audience": target_url,
            }

        response = client.create_task(parent=parent, task=task)
        logger.info("Dispatched Cloud Task to %s: %s", url, response.name)
        return response.name

    def dispatch_ingestion(self, payload: IngestionTaskPayload) -> Optional[str]:
        """Dispatch a full repository ingestion task."""
        return self._dispatch("/tasks/ingest", payload)

    def dispatch_incremental_pr(self, payload: IncrementalPRPayload) -> Optional[str]:
        """Dispatch an incremental PR merge graph-update task."""
        return self._dispatch("/tasks/incremental-pr", payload)

    def dispatch_incremental_push(self, payload: IncrementalPushPayload) -> Optional[str]:
        """Dispatch an incremental direct-push graph-update task."""
        return self._dispatch("/tasks/incremental-push", payload)

    def dispatch_pr_review(self, payload: PRReviewPayload) -> Optional[str]:
        """Dispatch a PR review task to the dedicated review-service via the codeReview queue."""
        return self._dispatch(
            "/tasks/review",
            payload,
            service_url=self._review_url,
            queue=self._review_queue,
        )
