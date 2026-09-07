"""
Exponential backoff retry handler for network and download failures.
"""

import time
from typing import Callable, Any
from app.utils.logger import logger


class RetryHandler:
    """Executes callables with exponential backoff and jitter."""

    @staticmethod
    def execute_with_retry(
        func: Callable[[], Any],
        max_retries: int = 3,
        initial_delay: float = 1.0,
        backoff_factor: float = 2.0,
    ) -> Any:
        delay = initial_delay
        last_exception = None

        for attempt in range(1, max_retries + 1):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt == max_retries:
                    logger.error(f"Attempt {attempt}/{max_retries} failed. No more retries: {e}")
                    raise e

                logger.warning(f"Attempt {attempt}/{max_retries} failed: {e}. Retrying in {delay:.1f}s...")
                time.sleep(delay)
                delay *= backoff_factor

        raise last_exception
