"""
Speed limit bypass and download acceleration engine for MultiDownloader.
Detects JavaScript runtimes (Node, Deno), multi-connection downloaders (aria2c),
and configures yt-dlp to bypass bandwidth throttling.
"""

import os
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from app.models.settings_model import Settings
from app.utils.logger import logger


class SpeedOptimizer:
    """Provides system detection and yt-dlp option tuning to bypass YouTube speed limits."""

    _cached_node: Optional[str] = None
    _cached_aria2c: Optional[str] = None

    @classmethod
    def get_js_runtime(cls) -> Optional[Tuple[str, str]]:
        """
        Locates a supported JavaScript runtime (Node.js, Deno, or Bun).
        Required by yt-dlp to solve the YouTube 'n' signature challenge
        that otherwise throttles download speeds down to ~40-70 KB/s.
        """
        # 1. Node.js
        node_candidates = [
            "/usr/local/bin/node",
            "/opt/homebrew/bin/node",
            shutil.which("node"),
        ]
        for path in node_candidates:
            if path and os.path.isfile(path) and os.access(path, os.X_OK):
                return "node", path

        # 2. Deno
        deno_candidates = [
            "/opt/homebrew/bin/deno",
            "/usr/local/bin/deno",
            str(Path.home() / ".deno" / "bin" / "deno"),
            shutil.which("deno"),
        ]
        for path in deno_candidates:
            if path and os.path.isfile(path) and os.access(path, os.X_OK):
                return "deno", path

        # 3. Bun
        bun_candidates = [
            "/opt/homebrew/bin/bun",
            "/usr/local/bin/bun",
            str(Path.home() / ".bun" / "bin" / "bun"),
            shutil.which("bun"),
        ]
        for path in bun_candidates:
            if path and os.path.isfile(path) and os.access(path, os.X_OK):
                return "bun", path

        return None

    @classmethod
    def get_aria2c_path(cls) -> Optional[str]:
        """
        Locates the aria2c multi-connection download utility on macOS.
        Splits downloads into up to 16 parallel TCP connections to saturate bandwidth.
        """
        candidates = [
            "/opt/homebrew/bin/aria2c",
            "/usr/local/bin/aria2c",
            shutil.which("aria2c"),
        ]
        for path in candidates:
            if path and os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    @classmethod
    def build_speed_options(cls, settings: Optional[Settings] = None, is_audio: bool = True) -> Dict[str, Any]:
        """
        Builds a comprehensive dictionary of yt-dlp speed-limit bypass options.
        """
        if settings is None:
            settings = Settings.load()

        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "continuedl": True,
            "nopart": False,
            # Socket & resilient retry tuning
            "socket_timeout": 30,
            "retries": 25,
            "fragment_retries": 25,
            "extractor_retries": 5,
            "file_access_retries": 5,
            # Optimized buffer & chunk sizes to maintain continuous TCP throughput
            "buffersize": 1024 * 1024,      # 1MB buffer
            "http_chunk_size": 2 * 1024 * 1024,  # 2MB chunks (prevents GVS connection drops)
        }

        # Method 1: JavaScript Runtime n-challenge solver (Bypasses YouTube 40KB/s throttle)
        if getattr(settings, "bypass_throttling", True):
            js_info = cls.get_js_runtime()
            if js_info:
                runtime_type, runtime_path = js_info
                opts["js_runtimes"] = {runtime_type: {"path": runtime_path}}
                opts["remote_components"] = ["ejs:github"]
                logger.debug(f"SpeedOptimizer: Using {runtime_type} ({runtime_path}) for n-challenge solving")

        # Method 2: Multi-Connection Parallel Chunking (aria2c or native fragments)
        aria2c_path = cls.get_aria2c_path()
        if getattr(settings, "use_aria2c", True) and aria2c_path:
            opts["external_downloader"] = {"default": aria2c_path}
            opts["external_downloader_args"] = {
                "default": [
                    "-x", "16",                 # 16 connections per server
                    "-s", "16",                 # 16 split connections per file
                    "-j", "16",                 # 16 parallel pieces
                    "-k", "1M",                 # 1MB chunk size
                    "--min-split-size=1M",
                    "--file-allocation=none",   # Fast allocation without disk stall
                    "--summary-interval=0",     # Silent progress
                ]
            }
            logger.debug(f"SpeedOptimizer: Using aria2c multi-connection downloader: {aria2c_path}")
        else:
            # Fallback to native concurrent fragment downloading
            opts["concurrent_fragment_downloads"] = 16

        # Method 3: IPv4 Routing (Bypasses throttled IPv6 carrier routes)
        if getattr(settings, "force_ipv4", False):
            opts["source_address"] = "0.0.0.0"

        # Method 4: PO Token (Proof of Origin Token) support
        extractor_args = {}
        po_tok = getattr(settings, "po_token", "").strip()
        if po_tok:
            extractor_args["youtube"] = {"po_token": [f"web+{po_tok}"]}

        # Method 5: Player client preference
        client_pref = getattr(settings, "preferred_player_client", "auto").lower()
        if client_pref and client_pref != "auto":
            if "youtube" not in extractor_args:
                extractor_args["youtube"] = {}
            extractor_args["youtube"]["player_client"] = [client_pref]

        if extractor_args:
            opts["extractor_args"] = extractor_args

        # Format selector
        if is_audio:
            opts["format"] = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best"
        else:
            opts["format"] = "bestvideo+bestaudio/best"

        return opts

    @classmethod
    def get_methods_status_summary(cls, settings: Optional[Settings] = None) -> Dict[str, Dict[str, Any]]:
        """
        Returns status and concise usage instruction for all speed bypass methods.
        """
        if settings is None:
            settings = Settings.load()

        js_info = cls.get_js_runtime()
        aria2c_path = cls.get_aria2c_path()

        return {
            "js_runtime": {
                "name": "1. JavaScript Runtime (Node.js/Deno) nsig Solver",
                "active": js_info is not None,
                "detail": f"Detected: {js_info[0]} at {js_info[1]}" if js_info else "Not found (Install with `brew install node`)",
                "instruction": "Solves YouTube's dynamic n-signature challenge in real-time to avoid the 40-70 KB/s speed cap. Auto-enabled when Node.js is present.",
            },
            "aria2c_multiconnection": {
                "name": "2. Multi-Connection Chunk Downloader (aria2c)",
                "active": aria2c_path is not None,
                "detail": f"Installed: {aria2c_path} (16 split connections)" if aria2c_path else "Not found. Run `brew install aria2` in Terminal",
                "instruction": "Splits each media file into 16 parallel chunks across 16 HTTP streams, multiplying download speed up to 10x.",
            },
            "native_fragments": {
                "name": "3. Native Parallel Fragment Streaming",
                "active": aria2c_path is None,
                "detail": "Active: 16 concurrent fragments",
                "instruction": "Built-in multi-threaded chunk streamer used automatically when aria2c is not installed.",
            },
            "chunk_tuning": {
                "name": "4. Optimized 2MB HTTP Chunk Sizing",
                "active": True,
                "detail": "2MB chunk size + 1MB socket buffer",
                "instruction": "Prevents YouTube Google Video Server (GVS) connection drops and keeps TCP windows saturated.",
            },
            "browser_cookies": {
                "name": "5. Session & Cookie Authentication",
                "active": bool(getattr(settings, "use_browser_cookies", False)),
                "detail": f"Source: {getattr(settings, 'cookie_browser', 'None')}",
                "instruction": "Transfers logged-in session cookies to bypass YouTube bot detection, captcha triggers, and rate-limits.",
            },
            "force_ipv4": {
                "name": "6. Force IPv4 CDN Routing",
                "active": bool(getattr(settings, "force_ipv4", False)),
                "detail": "0.0.0.0 binding (IPv4 edge routes)",
                "instruction": "Routes requests over IPv4 to bypass congested or throttled ISP IPv6 peering routes.",
            },
        }
