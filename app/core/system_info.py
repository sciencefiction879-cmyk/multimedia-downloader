"""
System information and resource discovery on macOS and other operating systems.
"""

import os
import platform
import sys
import psutil
from typing import Dict, Any


class SystemInfo:
    """Discovers CPU, RAM, and OS metrics."""

    @staticmethod
    def get_os_name() -> str:
        if sys.platform == "darwin":
            return f"macOS {platform.mac_ver()[0] or platform.release()}"
        return f"{platform.system()} {platform.release()}"

    @staticmethod
    def get_cpu_info() -> Dict[str, Any]:
        return {
            "cores_physical": psutil.cpu_count(logical=False) or 1,
            "cores_logical": psutil.cpu_count(logical=True) or 1,
            "usage_percent": psutil.cpu_percent(interval=None),
        }

    @staticmethod
    def get_memory_info() -> Dict[str, Any]:
        mem = psutil.virtual_memory()
        return {
            "total_gb": round(mem.total / (1024**3), 2),
            "available_gb": round(mem.available / (1024**3), 2),
            "used_percent": mem.percent,
        }

    @staticmethod
    def is_apple_silicon() -> bool:
        return sys.platform == "darwin" and platform.machine() in ("arm64", "aarch64")
