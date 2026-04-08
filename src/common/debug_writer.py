import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def make_review_dir(owner: str, repo: str, pr_number: int) -> Path:

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    review_dir = Path("output") / f"review-{timestamp}"
    review_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Review debug dir: {review_dir}")
    return review_dir


def write_step(review_dir: Path, filename: str, content: str) -> None:
    """Write a single step's debug output to <review_dir>/<filename>."""
    try:
        (review_dir / filename).write_text(content, encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to write {filename}: {e}")
