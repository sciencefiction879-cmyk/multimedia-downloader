"""
Resource & performance monitor for download concurrency, CPU, RAM, and network throughput.
"""

import time
import psutil
from typing import Dict, Any


class PerformanceMonitor:
    """Tracks system resource utilization and download throughput."""

    def __init__(self):
        self._last_net = psutil.net_io_counters()
        self._last_time = time.time()

    def get_snapshot(self) -> Dict[str, Any]:
        now = time.time()
        elapsed = max(now - self._last_time, 0.001)
        net = psutil.net_io_counters()

        bytes_recv = net.bytes_recv - self._last_net.bytes_recv
        speed_bytes_sec = bytes_recv / elapsed

        self._last_net = net
        self._last_time = now

        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None)

        return {
            "cpu_percent": cpu,
            "memory_percent": mem.percent,
            "memory_used_gb": round((mem.total - mem.available) / (1024**3), 2),
            "memory_total_gb": round(mem.total / (1024**3), 2),
            "network_speed_bytes_sec": speed_bytes_sec,
            "network_speed_str": self.format_speed(speed_bytes_sec),
        }

    @staticmethod
    def format_speed(speed_bytes_sec: float) -> str:
        if speed_bytes_sec < 1024:
            return f"{speed_bytes_sec:.1f} B/s"
        elif speed_bytes_sec < 1024 * 1024:
            return f"{speed_bytes_sec / 1024:.1f} KB/s"
        else:
            return f"{speed_bytes_sec / (1024 * 1024):.2f} MB/s"
