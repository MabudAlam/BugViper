"""
Debug logging utilities
"""

import logging

# Configure basic logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


def debug_log(message: str):
    """Log debug message."""
    logging.debug(message)


def info_logger(message: str):
    """Log info message."""
    logging.info(message)


def error_logger(message: str):
    """Log error message."""
    logging.error(message)


def warning_logger(message: str):
    """Log warning message."""
    logging.warning(message)


def debug_logger(message: str):
    """Log debug message."""
    logging.debug(message)
