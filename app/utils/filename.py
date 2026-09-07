"""
Safe filename sanitizer and formatter.
"""

import re
from app.config import MAX_FILENAME_LENGTH

INVALID_CHARS_REGEX = re.compile(r'[\\/*?:"<>|]')


def sanitize_filename(name: str, max_length: int = MAX_FILENAME_LENGTH) -> str:
    """Sanitizes a string to make it safe for filesystem paths across macOS, Windows, and Linux."""
    if not name:
        return "untitled"

    # Replace invalid chars with underscore or space
    clean = INVALID_CHARS_REGEX.sub("_", name)
    clean = re.sub(r"\s+", " ", clean).strip()

    # Remove trailing periods and spaces
    clean = clean.rstrip(". ")

    if len(clean) > max_length:
        clean = clean[:max_length].rstrip(". ")

    return clean or "untitled"
