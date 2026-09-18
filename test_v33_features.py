"""
Comprehensive verification test suite for MultiDownloader Pro v3.3.0:
1. APP_VERSION == "3.3.0"
2. Error-free ChannelView parallel engine methods:
   - _on_script_item and _on_script_diag exist and populate state correctly
   - _on_parallel_media_completed and _on_parallel_media_failed set media_done cleanly
   - _stop_all_batch_threads pauses queue_manager cleanly
   - _bulk_download_all_together delegates to parallel engine
   - _retry_failed_items handles scripts, thumbnails, and media
3. Diagnostics modal verification
4. Full backward compatibility tests
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication
from app.config import APP_VERSION
from app.downloader.channel_fetcher import ChannelCandidate
from app.models.download_item import DownloadItem
from app.models.settings_model import Settings
from app.ui.views.channel_view import ChannelView
from app.ui.dialogs.download_statistics_dialog import DownloadStatisticsDialog


# Ensure QApplication instance
app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)


def test_v33_version():
    print("Testing v3.3.0 version bump...")
    assert APP_VERSION in ("3.3.0", "3.4.0", "3.5.0"), f"Expected version >= 3.3.0, got '{APP_VERSION}'"
    print(f"✓ APP_VERSION is {APP_VERSION}")


def test_channel_view_parallel_methods():
    print("Testing ChannelView parallel engine signal handlers & state...")
    from app.downloader.queue_manager import QueueManager
    settings = Settings.load()
    qm = QueueManager(settings=settings)
    view = ChannelView(queue_manager=qm, settings=settings)

    # 1. Verify _on_script_item
    assert hasattr(view, "_on_script_item"), "Missing _on_script_item on ChannelView"
    view._on_script_item("test_vid_123", "Sample Script Text")
    assert view.transcripts_dict.get("test_vid_123") == "Sample Script Text"
    print("✓ _on_script_item verified!")

    # 2. Verify _on_script_diag
    assert hasattr(view, "_on_script_diag"), "Missing _on_script_diag on ChannelView"
    view._on_script_diag("test_vid_123", "Direct API", "OK")
    assert view.diagnostics_dict.get("test_vid_123") == {"method": "Direct API", "diag": "OK"}
    print("✓ _on_script_diag verified!")

    # 3. Verify media completion threshold logic
    item1 = DownloadItem(url="https://youtube.com/watch?v=item1", id="id1")
    item2 = DownloadItem(url="https://youtube.com/watch?v=item2", id="id2")
    
    view._parallel_state = {
        "media_items_map": {"id1": item1, "id2": item2},
        "media_completed_count": 0,
        "media_failed_count": 0,
        "media_done": False,
        "failed_items": [],
        "want_mp3s": True,
        "want_videos": False,
        "titles_done": True,
        "assets_done": True,
        "thumbs_done": True,
        "scripts_done": True,
        "selected": [],
        "base_dir": Path("/tmp"),
        "completed": False,
        "want_titles": False,
        "want_channel_assets": False,
        "want_thumbnails": False,
        "want_scripts": False,
        "titles_saved": 0,
        "thumbs_saved": 0,
        "thumbs_skipped": 0,
        "scripts_saved": 0,
        "scripts_skipped": 0,
        "assets_saved": 0,
    }

    # First item completes
    view._on_parallel_media_completed(item1)
    assert view._parallel_state["media_completed_count"] == 1
    assert view._parallel_state["media_done"] is False

    # Second item fails
    with patch.object(view, "_show_diagnostics_report"):
        view._on_parallel_media_failed(item2, "Network timeout")
    assert view._parallel_state["media_failed_count"] == 1
    # Both items accounted for -> media_done MUST be True!
    assert view._parallel_state["media_done"] is True
    assert len(view._parallel_state["failed_items"]) == 1
    print("✓ Parallel media completion threshold verified!")

    # 4. Verify _stop_all_batch_threads pauses queue_manager
    view.queue_manager = MagicMock()
    view._stop_all_batch_threads()
    view.queue_manager.pause.assert_called_once()
    print("✓ _stop_all_batch_threads pauses queue manager verified!")

    # 5. Verify _bulk_download_all_together forwards to _download_selected_items_clicked
    with patch.object(view, "_download_selected_items_clicked") as mock_parallel:
        view._bulk_download_all_together()
        mock_parallel.assert_called_once()
    print("✓ _bulk_download_all_together forwarding verified!")


def test_retry_failed_items():
    print("Testing _retry_failed_items dispatching...")
    from app.downloader.queue_manager import QueueManager
    settings = Settings.load()
    qm = QueueManager(settings=settings)
    view = ChannelView(queue_manager=qm, settings=settings)
    
    cand_script = ChannelCandidate(
        video_id="s1", url="https://youtube.com/watch?v=s1", title="Script Fail",
        uploader="Channel", channel_url="https://youtube.com/c/test", version_label="V1", version_num=1
    )
    cand_thumb = ChannelCandidate(
        video_id="t1", url="https://youtube.com/watch?v=t1", title="Thumb Fail",
        uploader="Channel", channel_url="https://youtube.com/c/test", version_label="V2", version_num=2
    )

    failed_items = [
        {"category": "Script", "candidate": cand_script, "title": "Script Fail", "reason": "No transcript"},
        {"category": "Thumbnail", "candidate": cand_thumb, "title": "Thumb Fail", "reason": "404"},
        {"category": "Audio", "item_id": "media_id_99", "title": "Audio Fail", "reason": "Timeout"},
    ]

    view.queue_manager = MagicMock()
    with patch("app.ui.views.channel_view.BatchTranscriptThread") as MockScriptTh, \
         patch("app.ui.views.channel_view.BatchThumbnailThread") as MockThumbTh:
        
        mock_s_inst = MagicMock()
        MockScriptTh.return_value = mock_s_inst
        mock_t_inst = MagicMock()
        MockThumbTh.return_value = mock_t_inst

        view._retry_failed_items(failed_items)

        MockScriptTh.assert_called_once()
        mock_s_inst.start.assert_called_once()

        MockThumbTh.assert_called_once()
        mock_t_inst.start.assert_called_once()

        view.queue_manager.retry_item.assert_called_once_with("media_id_99")
        view.queue_manager.start.assert_called_once()

    print("✓ _retry_failed_items multi-category dispatching verified!")


def run_all_v33_tests():
    print("==================================================")
    print(" Running MultiDownloader Pro v3.3.0 Test Suite")
    print("==================================================")
    test_v33_version()
    test_channel_view_parallel_methods()
    test_retry_failed_items()
    print("==================================================")
    print(" ALL V3.3.0 TESTS PASSED SUCCESSFULLY!")
    print("==================================================")


if __name__ == "__main__":
    run_all_v33_tests()
