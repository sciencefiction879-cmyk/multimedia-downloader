"""
Application logging infrastructure.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from app.config import LOG_FILE, DEFAULT_LOG_LEVEL

_logger = logging.getLogger("MultiDownloader")
_logger.setLevel(getattr(logging, DEFAULT_LOG_LEVEL, logging.INFO))

if not _logger.handlers:
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    ch.setFormatter(fmt)
    _logger.addHandler(ch)

    # Rotating file handler
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(str(LOG_FILE), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        _logger.addHandler(fh)
    except Exception:
        pass

logger = _logger
