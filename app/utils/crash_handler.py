"""
Crash and uncaught exception handler for graceful reporting.
"""

import sys
import traceback
from datetime import datetime
from app.config import LOGS_DIR
from app.utils.logger import logger


def setup_crash_handler():
    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.critical(f"Uncaught exception:\n{err_msg}")

        try:
            crash_file = LOGS_DIR / f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            with open(crash_file, "w", encoding="utf-8") as f:
                f.write(f"Timestamp: {datetime.now().isoformat()}\n")
                f.write(f"Exception: {exc_type.__name__}: {exc_value}\n\n")
                f.write(err_msg)
        except Exception:
            pass

    sys.excepthook = _excepthook
