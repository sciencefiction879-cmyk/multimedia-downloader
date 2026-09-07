"""
GPU detector and hardware acceleration capability analyzer for macOS / Apple Silicon VideoToolbox and other GPUs.
"""

import sys
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class GPUCapability:
    name: str
    vendor: str
    has_nvenc: bool = False
    has_qsv: bool = False
    has_amf: bool = False
    has_videotoolbox: bool = False


class GPUDetector:
    """Detects available hardware acceleration for media encoding."""

    _cached_capability: Optional[GPUCapability] = None

    @classmethod
    def detect(cls) -> GPUCapability:
        if cls._cached_capability is not None:
            return cls._cached_capability

        cap = GPUCapability(name="CPU Software Encoder", vendor="Generic")

        if sys.platform == "darwin":
            cap.name = "Apple Silicon VideoToolbox" if sys.maxsize > 2**32 else "Apple VideoToolbox"
            cap.vendor = "Apple"
            cap.has_videotoolbox = True
            cls._cached_capability = cap
            return cap

        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin:
            try:
                out = subprocess.check_output([ffmpeg_bin, "-encoders"], stderr=subprocess.STDOUT, text=True, timeout=5)
                if "nvenc" in out.lower():
                    cap.has_nvenc = True
                    cap.vendor = "NVIDIA"
                    cap.name = "NVIDIA NVENC"
                elif "qsv" in out.lower():
                    cap.has_qsv = True
                    cap.vendor = "Intel"
                    cap.name = "Intel QuickSync"
                elif "amf" in out.lower():
                    cap.has_amf = True
                    cap.vendor = "AMD"
                    cap.name = "AMD AMF"
            except Exception:
                pass

        cls._cached_capability = cap
        return cap
