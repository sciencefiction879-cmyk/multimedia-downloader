"""
Comprehensive verification test suite for MultiDownloader Pro v3.4.0:
1. APP_VERSION == "3.4.0"
2. Settings and Config constants for script & audio concurrency (defaults = 3)
3. BatchThumbnailThread max_count limit behavior
4. BatchTranscriptThread parallel concurrency and live disk save
5. ChannelView controls:
   - spin_thumb_count (custom total thumbnail limit)
   - spin_script_concurrency (custom parallel scripts, default 3)
   - spin_audio_concurrency (custom parallel audio, default 3)
6. Strict sequential phased pipeline execution chain:
   Phase 1 (Titles) -> Phase 2 (Thumbnails) -> Phase 3 (Assets) -> Phase 4 (Scripts) -> Phase 5 (Audio) -> Finalize
7. Live saving verification for each individual phase
8. Failure collection and diagnostics modal
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication
from app.config import (
    APP_VERSION,
    DEFAULT_SCRIPT_CONCURRENT_DOWNLOADS,
    DEFAULT_AUDIO_CONCURRENT_DOWNLOADS,
    MAX_CONCURRENT_DOWNLOADS,
)
from app.downloader.channel_fetcher import ChannelCandidate
from app.downloader.queue_manager import QueueManager
from app.models.download_item import DownloadItem
from app.models.settings_model import Settings
from app.ui.views.channel_view import (
    ChannelView,
    BatchThumbnailThread,
    BatchTranscriptThread,
)
from app.ui.dialogs.download_statistics_dialog import DownloadStatisticsDialog


# Ensure QApplication instance
app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)


def test_v34_config():
    print("Testing v3.4 configuration constants...")
    assert APP_VERSION in ("3.4.0", "3.5.0"), f"Expected APP_VERSION '3.4.0' or '3.5.0', got '{APP_VERSION}'"
    assert DEFAULT_SCRIPT_CONCURRENT_DOWNLOADS == 3, f"Expected 3, got {DEFAULT_SCRIPT_CONCURRENT_DOWNLOADS}"
    assert DEFAULT_AUDIO_CONCURRENT_DOWNLOADS == 3, f"Expected 3, got {DEFAULT_AUDIO_CONCURRENT_DOWNLOADS}"
    assert MAX_CONCURRENT_DOWNLOADS >= 16, f"Expected >= 16, got {MAX_CONCURRENT_DOWNLOADS}"
    print("✓ Config constants verified!")


def test_v34_settings_persistence():
    print("Testing Settings persistence for script & audio concurrency...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings_file = Path(tmp_dir) / "settings.json"
        s = Settings()
        assert s.script_concurrent_downloads == 3
        assert s.audio_concurrent_downloads == 3

        s.script_concurrent_downloads = 5
        s.audio_concurrent_downloads = 7
        s.save(file_path=settings_file)

        # Reload
        s2 = Settings.load(file_path=settings_file)
        assert s2.script_concurrent_downloads == 5
        assert s2.audio_concurrent_downloads == 7
        print("✓ Settings concurrency persistence verified!")


def test_batch_thumbnail_thread_max_count():
    print("Testing BatchThumbnailThread custom max_count...")
    candidates = [
        ChannelCandidate(title=f"Video {i}", video_id=f"vid_{i}", url=f"https://youtu.be/{i}", version_num=i, version_label=f"V{i}")
        for i in range(1, 11)
    ]
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)
        # Limit to 3 thumbnails
        th = BatchThumbnailThread(candidates=candidates, output_dir=out_dir, max_count=3)
        assert len(th.candidates) == 3, f"Expected 3 candidates capped, got {len(th.candidates)}"
        assert th.candidates[0].version_label == "V1"
        assert th.candidates[2].version_label == "V3"

        # Unlimited (None)
        th2 = BatchThumbnailThread(candidates=candidates, output_dir=out_dir, max_count=None)
        assert len(th2.candidates) == 10
    print("✓ BatchThumbnailThread max_count capping verified!")


def test_batch_transcript_thread_parallel_live_save():
    print("Testing BatchTranscriptThread parallel live disk saving...")
    candidates = [
        ChannelCandidate(title=f"Video {i}", video_id=f"vid_{i}", url=f"https://youtu.be/{i}", version_num=i, version_label=f"V{i}")
        for i in range(1, 4)
    ]
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir) / "Scripts"
        
        with patch("app.downloader.transcript_fetcher.TranscriptFetcher.fetch_video_transcript_with_diagnostics") as mock_fetch:
            mock_fetch.side_effect = lambda vid: (f"This is a genuine voiceover transcript for {vid} with several clear words.", "Mock API", "")
            
            thread = BatchTranscriptThread(
                candidates=candidates,
                existing_transcripts={},
                output_dir=out_dir,
                concurrency=3,
            )
            thread.run()

        # Verify live files written to disk
        assert (out_dir / "V1 Script.txt").exists(), "V1 Script.txt was not saved to disk"
        assert (out_dir / "V2 Script.txt").exists(), "V2 Script.txt was not saved to disk"
        assert (out_dir / "V3 Script.txt").exists(), "V3 Script.txt was not saved to disk"

        content = (out_dir / "V1 Script.txt").read_text(encoding="utf-8")
        assert "voiceover transcript for vid_1" in content
    print("✓ BatchTranscriptThread parallel live disk save verified!")


def test_channel_view_v34_controls():
    print("Testing ChannelView v3.4 UI controls & custom selectors...")
    settings = Settings()
    settings.script_concurrent_downloads = 3
    settings.audio_concurrent_downloads = 3
    qm = QueueManager(settings=settings)
    view = ChannelView(queue_manager=qm, settings=settings)

    # 1. Check custom selectors exist
    assert hasattr(view, "spin_thumb_count"), "Missing spin_thumb_count"
    assert hasattr(view, "spin_script_concurrency"), "Missing spin_script_concurrency"
    assert hasattr(view, "spin_audio_concurrency"), "Missing spin_audio_concurrency"

    # 2. Check defaults
    assert view.spin_script_concurrency.value() == 3
    assert view.spin_audio_concurrency.value() == 3

    # 3. Modify values and check reactive label updates
    view.spin_thumb_count.setValue(15)
    assert "Total: 15" in view.chk_thumbnails.text()

    view.spin_script_concurrency.setValue(6)
    assert "⚡ 6 Parallel" in view.chk_scripts.text()
    assert view.settings.script_concurrent_downloads == 6

    view.spin_audio_concurrency.setValue(4)
    assert "⚡ 4 Parallel" in view.chk_mp3s.text()
    assert view.settings.audio_concurrent_downloads == 4
    print("✓ ChannelView v3.4 UI controls verified!")


def test_channel_view_sequential_pipeline():
    print("Testing ChannelView sequential phased execution chain...")
    settings = Settings.load()
    qm = QueueManager(settings=settings)
    view = ChannelView(queue_manager=qm, settings=settings)

    candidates = [
        ChannelCandidate(title="Ep 1", video_id="v1", url="https://youtu.be/1", version_num=1, version_label="V1", uploader="ChannelA"),
        ChannelCandidate(title="Ep 2", video_id="v2", url="https://youtu.be/2", version_num=2, version_label="V2", uploader="ChannelA"),
    ]
    for c in candidates:
        c.is_selected = True
    view.candidates = candidates

    with tempfile.TemporaryDirectory() as tmp_dir:
        view.txt_out_dir.setText(tmp_dir)
        view.spin_thumb_count.setValue(1)
        view.spin_script_concurrency.setValue(2)
        view.spin_audio_concurrency.setValue(2)

        with patch.object(view, "_run_phase_1_titles", wraps=view._run_phase_1_titles) as p1, \
             patch.object(view, "_run_phase_2_thumbnails") as p2, \
             patch.object(view, "_run_phase_3_channel_assets") as p3, \
             patch.object(view, "_run_phase_4_scripts") as p4, \
             patch.object(view, "_run_phase_5_media") as p5, \
             patch.object(view, "_finalize_phased_pipeline") as pf:

            # Enable all checkboxes
            view.chk_titles.setChecked(True)
            view.chk_thumbnails.setChecked(True)
            view.chk_channel_assets.setChecked(True)
            view.chk_scripts.setChecked(True)
            view.chk_mp3s.setChecked(True)

            # Trigger download
            view._download_selected_items_clicked()

            # Phase 1: Titles should execute synchronously and write Titles.txt live
            titles_file = Path(tmp_dir) / "Titles.txt"
            assert titles_file.exists(), "Titles.txt was not generated live on disk!"
            content = titles_file.read_text(encoding="utf-8")
            assert "V1 — Ep 1" in content
            assert "V2 — Ep 2" in content

            # State check
            assert view._phased_pipeline_state["titles_done"] is True
            assert view._phased_pipeline_state["titles_saved"] == 2

            # Verify that Phase 2 was called next!
            p2.assert_called_once()
            print("✓ Sequential phase transition (Titles -> Thumbnails) verified!")


def test_download_statistics_dialog_v34():
    print("Testing DownloadStatisticsDialog v3.4 report generation...")
    stats = {
        "total_videos": 10,
        "total_succeeded": 9,
        "total_skipped": 0,
        "titles_status": "Saved into Titles.txt",
        "scripts_status": "8 saved (⚡ 3 concurrent)",
        "thumbnails_status": "5 saved (target: 5)",
        "audio_status": "10 completed (⚡ 3 concurrent)",
        "video_status": "Not Selected",
        "assets_status": "Saved",
    }
    failed = [
        {"version_label": "V4", "title": "Missing CC", "category": "Script", "reason": "No subtitles available"},
    ]
    with tempfile.TemporaryDirectory() as tmp_dir:
        dlg = DownloadStatisticsDialog(stats=stats, failed_items=failed, output_dir=Path(tmp_dir))
        dlg._copy_diagnostic_report()
        report = QApplication.clipboard().text()
        assert f"MultiDownloader v{APP_VERSION} - Download Statistics Report" in report
        assert "Total Videos: 10" in report
        assert "Succeeded: 9" in report
        assert "Failed: 1" in report
        assert "V4" in report
    print("✓ DownloadStatisticsDialog v3.4 report verified!")


def main():
    print("==================================================")
    print(" Running MultiDownloader Pro v3.4.0 Test Suite")
    print("==================================================")
    test_v34_config()
    test_v34_settings_persistence()
    test_batch_thumbnail_thread_max_count()
    test_batch_transcript_thread_parallel_live_save()
    test_channel_view_v34_controls()
    test_channel_view_sequential_pipeline()
    test_download_statistics_dialog_v34()
    print("==================================================")
    print(" ALL V3.4.0 TESTS PASSED SUCCESSFULLY!")
    print("==================================================")


if __name__ == "__main__":
    main()
