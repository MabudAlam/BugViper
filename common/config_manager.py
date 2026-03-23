"""
Configuration management utilities
"""

import os


def get_config_value(key: str, default: str = "") -> str:
    """Get configuration value from environment or default."""
    return os.getenv(key, default)
