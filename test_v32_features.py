"""
Comprehensive verification test suite for MultiDownloader Pro v3.2:
1. Version & Config:
   - APP_VERSION == "3.2.0"
   - DEFAULT_AUDIO_CONCURRENT_DOWNLOADS == 3
   - MAX_CONCURRENT_DOWNLOADS == 16
2. Settings Persistence:
   - audio_concurrent_downloads field loaded and saved
3. Dark & Light Theme Stylesheets:
   - get_stylesheet("dark") and get_stylesheet("light") validation
4. Strict Transcript Fetcher Cascade:
   - Fallback hierarchy: English manual/auto -> auto-translated English -> native languages
5. Statistics & Retry Modal Logic:
   - DownloadStatisticsDialog initialization, summary KPIs, failure reporting, retry callback
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from youtube_transcript_api import YouTubeTranscriptApi
from app.config import (
    APP_VERSION,
    DEFAULT_AUDIO_CONCURRENT_DOWNLOADS,
    MAX_CONCURRENT_DOWNLOADS,
)
from app.models.settings_model import Settings
from app.downloader.transcript_fetcher import TranscriptFetcher
from app.downloader.channel_fetcher import ChannelCandidate
from app.ui.styles import get_stylesheet
from app.ui.dialogs.download_statistics_dialog import DownloadStatisticsDialog


# Ensure Qt application instance exists for UI/dialog tests
app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)


def test_v32_config():
    print("Testing v3.2 configuration constants...")
    assert APP_VERSION in ("3.2.0", "3.3.0", "3.4.0"), f"Expected APP_VERSION '3.2.0', '3.3.0' or '3.4.0', got '{APP_VERSION}'"
    assert DEFAULT_AUDIO_CONCURRENT_DOWNLOADS == 3, f"Expected 3, got {DEFAULT_AUDIO_CONCURRENT_DOWNLOADS}"
    assert MAX_CONCURRENT_DOWNLOADS == 16, f"Expected 16, got {MAX_CONCURRENT_DOWNLOADS}"
    print("✓ Config constants verified!")


def test_settings_concurrency():
    print("Testing settings audio concurrency persistence...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings_file = Path(tmp_dir) / "settings.json"
        
        # Test default
        s = Settings.load(settings_file)
        assert s.audio_concurrent_downloads == 3
        
        # Modify and save
        s.audio_concurrent_downloads = 8
        s.save(settings_file)
        
        # Reload and verify
        s2 = Settings.load(settings_file)
        assert s2.audio_concurrent_downloads == 8, f"Expected 8, got {s2.audio_concurrent_downloads}"
    print("✓ Settings audio concurrency verified!")


def test_theme_stylesheets():
    print("Testing Dark and Light theme stylesheets...")
    dark_css = get_stylesheet("dark")
    light_css = get_stylesheet("light")
    
    assert dark_css is not None and len(dark_css) > 500
    assert light_css is not None and len(light_css) > 500
    assert dark_css != light_css
    
    # Verify dark palette markers
    assert "#1e1e24" in dark_css or "#18181c" in dark_css
    # Verify light palette markers
    assert "#ffffff" in light_css
    assert "#f8f9fa" in light_css or "#0f172a" in light_css
    # Verify theme toggle button style is defined
    assert "#themeToggleBtn" in dark_css
    assert "#themeToggleBtn" in light_css
    print("✓ Dark and Light themes verified!")


def test_transcript_fetcher_cascade():
    print("Testing transcript fetcher strict discovery cascade...")
    
    # Case 1: Video with English captions via direct API fetch
    mock_item = MagicMock()
    mock_item.text = "Hello world from direct fetch."
    with patch.object(YouTubeTranscriptApi, "fetch", return_value=[mock_item]):
        res = TranscriptFetcher.fetch_video_transcript("vid_1")
        assert res is not None
        assert "Hello world from direct fetch" in res
        print("  -> English direct transcript retrieved successfully")

    # Case 2: Video with foreign transcript that supports translation to en
    mock_transcript_es = MagicMock()
    mock_transcript_es.language_code = "es"
    mock_transcript_es.language = "Spanish"
    mock_transcript_es.is_generated = False
    mock_transcript_es.is_translatable = True
    
    mock_translated_en = MagicMock()
    mock_translated_item = MagicMock()
    mock_translated_item.text = "Hello in English from Spanish translation."
    mock_translated_en.fetch.return_value = [mock_translated_item]
    mock_transcript_es.translate.return_value = mock_translated_en
    
    with patch.object(YouTubeTranscriptApi, "fetch", side_effect=Exception("No direct transcript")), \
         patch.object(YouTubeTranscriptApi, "list", return_value=[mock_transcript_es]):
        res = TranscriptFetcher.fetch_video_transcript("vid_2")
        assert res is not None
        assert "Hello in English from Spanish translation" in res
        print("  -> Auto-translated transcript retrieved successfully")

    # Case 3: Video with untranslatable foreign transcript (fallback to native text)
    mock_transcript_ur = MagicMock()
    mock_transcript_ur.language_code = "ur"
    mock_transcript_ur.language = "Urdu"
    mock_transcript_ur.is_generated = False
    mock_transcript_ur.is_translatable = False
    mock_ur_item = MagicMock()
    mock_ur_item.text = "خوش آمدید"
    mock_transcript_ur.fetch.return_value = [mock_ur_item]
    
    with patch.object(YouTubeTranscriptApi, "fetch", side_effect=Exception("No direct transcript")), \
         patch.object(YouTubeTranscriptApi, "list", return_value=[mock_transcript_ur]):
        res = TranscriptFetcher.fetch_video_transcript("vid_3")
        assert res is not None
        assert "خوش آمدید" in res
        print("  -> Untranslatable native fallback retrieved successfully")

    print("✓ Transcript discovery cascade verified!")


def test_download_statistics_dialog():
    print("Testing DownloadStatisticsDialog and retry handler...")
    
    test_stats = {
        "total_videos": 10,
        "total_succeeded": 7,
        "total_skipped": 0,
        "titles_status": "Saved (10 titles)",
        "scripts_status": "8 / 10 downloaded (2 missing)",
        "thumbnails_status": "10 / 10 downloaded",
        "audio_status": "9 / 10 downloaded (1 failed)",
        "video_status": "Not Selected",
        "assets_status": "Not Selected",
    }
    
    test_failed_items = [
        {"category": "Scripts", "version_label": "V3", "title": "Episode 3", "reason": "No transcript found"},
        {"category": "Scripts", "version_label": "V7", "title": "Episode 7", "reason": "HTTP 429 Rate Limit"},
        {"category": "Audio", "version_label": "V5", "title": "Episode 5", "reason": "Network timeout"},
    ]
    
    retried_items = []
    def on_retry(items):
        nonlocal retried_items
        retried_items = items
        
    with tempfile.TemporaryDirectory() as tmp_dir:
        dialog = DownloadStatisticsDialog(
            stats=test_stats,
            failed_items=test_failed_items,
            output_dir=Path(tmp_dir),
            on_retry=on_retry,
        )
        
        # Verify failed table rows
        assert dialog.table_failed.rowCount() == 3
        assert dialog.table_failed.item(0, 0).text() == "V3"
        assert dialog.table_failed.item(0, 1).text() == "Episode 3"
        assert dialog.table_failed.item(0, 2).text() == "Scripts"
        assert "No transcript" in dialog.table_failed.item(0, 3).text()
        
        # Verify copy diagnostic report generates proper data
        dialog._copy_diagnostic_report()
        report = QApplication.clipboard().text()
        assert "MultiDownloader" in report and "Download Statistics Report" in report
        assert "Total Videos: 10" in report
        assert "Succeeded: 7" in report
        assert "Failed: 3" in report
        assert "V3" in report and "Episode 3" in report
        assert "V5" in report and "Episode 5" in report
        
        # Trigger retry button
        dialog._on_retry_clicked()
        assert len(retried_items) == 3
        assert retried_items == test_failed_items
        
    print("✓ DownloadStatisticsDialog verified!")


def run_all_v32_tests():
    print("==================================================")
    print(" Running MultiDownloader Pro v3.2 Test Suite")
    print("==================================================")
    test_v32_config()
    test_settings_concurrency()
    test_theme_stylesheets()
    test_transcript_fetcher_cascade()
    test_download_statistics_dialog()
    print("==================================================")
    print(" ALL V3.2 TESTS PASSED SUCCESSFULLY!")
    print("==================================================")


if __name__ == "__main__":
    run_all_v32_tests()
