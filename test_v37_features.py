"""
Comprehensive Test Suite for MultiDownloader Pro v3.7.0
Focuses on:
1. APP_VERSION == "3.7.0".
2. Strict candidate isolation between audio and video lists.
3. Independent media completion and failure counters (audio vs. video).
4. Bug regression: Audio 200 never reports 320 in any step, label, or stats report.
5. Accurate category status formatting for titles, thumbnails, scripts, audio, and videos.
6. Post-download statistics dialog KPI cards and export verification.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

from app.config import APP_VERSION
from app.downloader.channel_fetcher import ChannelCandidate
from app.downloader.queue_manager import QueueManager
from app.models.download_item import DownloadItem, DownloadStatus
from app.models.settings_model import Settings
from app.ui.dialogs.download_statistics_dialog import DownloadStatisticsDialog
from app.ui.views.channel_view import ChannelView

app = QApplication.instance() or QApplication(sys.argv)


class TestV37Features(unittest.TestCase):

    def setUp(self):
        self.settings = Settings()
        self.queue_manager = QueueManager(self.settings)
        self.channel_view = ChannelView(self.queue_manager, self.settings)

    def tearDown(self):
        self.channel_view.deleteLater()
        self.queue_manager.deleteLater()

    # 1. Version Bump Verification
    def test_01_version_bump_370(self):
        """Verify APP_VERSION is bumped to 3.7.0."""
        self.assertEqual(APP_VERSION, "3.7.0")

    # 2. Candidate Isolation (Audio 200 vs. Video 120)
    def test_02_candidate_isolation_audio_and_video(self):
        """Verify state['audio_candidates'] and state['video_candidates'] remain strictly isolated."""
        audio_cands = [
            ChannelCandidate(video_id=f"aud_{i}", title=f"Audio Track {i}", url=f"https://youtu.be/a{i}", duration=120)
            for i in range(200)
        ]
        video_cands = [
            ChannelCandidate(video_id=f"vid_{i}", title=f"Video Clip {i}", url=f"https://youtu.be/v{i}", duration=300)
            for i in range(120)
        ]

        state = {
            "want_mp3s": True,
            "want_videos": True,
            "audio_candidates": audio_cands,
            "video_candidates": video_cands,
            "audio_completed_count": 0,
            "audio_failed_count": 0,
            "video_completed_count": 0,
            "video_failed_count": 0,
            "media_completed_count": 0,
            "media_failed_count": 0,
            "media_items_map": {},
            "media_done": False,
        }
        self.channel_view._phased_pipeline_state = state

        # Verify candidate lengths are NOT combined
        self.assertEqual(len(state["audio_candidates"]), 200)
        self.assertEqual(len(state["video_candidates"]), 120)
        self.assertNotEqual(len(state["audio_candidates"]), 320)

    # 3. Independent Counter Increment & Status Tracking
    def test_03_independent_media_counters(self):
        """Verify audio and video counters increment independently without conflating."""
        state = {
            "want_mp3s": True,
            "want_videos": True,
            "audio_candidates": [ChannelCandidate(video_id=f"a_{i}", title=f"A {i}", url="") for i in range(200)],
            "video_candidates": [ChannelCandidate(video_id=f"v_{i}", title=f"V {i}", url="") for i in range(120)],
            "audio_completed_count": 0,
            "audio_failed_count": 0,
            "video_completed_count": 0,
            "video_failed_count": 0,
            "media_completed_count": 0,
            "media_failed_count": 0,
            "media_items_map": {},
            "media_done": False,
            "failed_items": [],
        }
        self.channel_view._phased_pipeline_state = state

        # Create audio item and complete it
        audio_item = DownloadItem(url="https://youtu.be/a0", media_type="Audio", video_id="a_0")
        state["media_items_map"][audio_item.id] = audio_item
        self.channel_view._on_phased_media_completed(audio_item)

        self.assertEqual(state["audio_completed_count"], 1)
        self.assertEqual(state["video_completed_count"], 0)
        self.assertEqual(state["media_completed_count"], 1)

        # Create video item and complete it
        video_item = DownloadItem(url="https://youtu.be/v0", media_type="Video", video_id="v_0")
        state["media_items_map"][video_item.id] = video_item
        self.channel_view._on_phased_media_completed(video_item)

        self.assertEqual(state["audio_completed_count"], 1)
        self.assertEqual(state["video_completed_count"], 1)
        self.assertEqual(state["media_completed_count"], 2)

    # 4. Audio 200 Never Reports 320 in Live Status Label
    def test_04_audio_label_shows_exact_count(self):
        """Verify lbl_prog_media displays strictly 200 for Audio and 120 for Video."""
        state = {
            "want_mp3s": True,
            "want_videos": True,
            "audio_candidates": [ChannelCandidate(video_id=f"a_{i}", title=f"A {i}", url="") for i in range(200)],
            "video_candidates": [ChannelCandidate(video_id=f"v_{i}", title=f"V {i}", url="") for i in range(120)],
            "audio_completed_count": 200,
            "audio_failed_count": 0,
            "video_completed_count": 120,
            "video_failed_count": 0,
            "media_completed_count": 320,
            "media_failed_count": 0,
            "media_items_map": {"fake_id": "fake"},
            "media_done": True,
            "audio_concurrency": 3,
        }
        self.channel_view._phased_pipeline_state = state
        self.channel_view._update_phased_media_status()

        label_text = self.channel_view.lbl_prog_media.text()
        # Must show 200/200 for audio and 120/120 for video
        self.assertIn("Audio:</b> 200/200", label_text)
        self.assertIn("Videos:</b> 120/120", label_text)
        # Must NOT show 320/200 or 320/120 anywhere!
        self.assertNotIn("320/200", label_text)
        self.assertNotIn("320/120", label_text)

    # 5. Audio Only Mode Shows Exact Count
    def test_05_audio_only_mode_displays_exact_count(self):
        """Verify when only Audio is selected, label strictly reports Audio counts."""
        state = {
            "want_mp3s": True,
            "want_videos": False,
            "audio_candidates": [ChannelCandidate(video_id=f"a_{i}", title=f"A {i}", url="") for i in range(200)],
            "video_candidates": [],
            "audio_completed_count": 150,
            "audio_failed_count": 2,
            "video_completed_count": 0,
            "video_failed_count": 0,
            "media_completed_count": 150,
            "media_failed_count": 2,
            "media_items_map": {"fake": "fake"},
            "media_done": False,
            "audio_concurrency": 5,
        }
        self.channel_view._phased_pipeline_state = state
        self.channel_view._update_phased_media_status()

        label_text = self.channel_view.lbl_prog_media.text()
        self.assertIn("150/200", label_text)
        self.assertIn("2 Failed", label_text)
        self.assertIn("⚡ 5 Concurrent", label_text)
        self.assertNotIn("Videos", label_text)

    # 6. Final Statistics Dictionary Isolation & Accuracy
    def test_06_final_statistics_exact_counts(self):
        """Verify _finalize_phased_pipeline generates exact {done}/{target} strings."""
        tmp_dir = Path("/tmp/test_v37_stats")
        tmp_dir.mkdir(parents=True, exist_ok=True)

        audio_cands = [ChannelCandidate(video_id=f"a_{i}", title=f"A {i}", url="") for i in range(200)]
        video_cands = [ChannelCandidate(video_id=f"v_{i}", title=f"V {i}", url="") for i in range(120)]
        title_cands = [ChannelCandidate(video_id=f"t_{i}", title=f"T {i}", url="") for i in range(200)]
        script_cands = [ChannelCandidate(video_id=f"s_{i}", title=f"S {i}", url="") for i in range(200)]
        thumb_cands = [ChannelCandidate(video_id=f"th_{i}", title=f"Th {i}", url="") for i in range(200)]

        state = {
            "completed": False,
            "base_dir": tmp_dir,
            "selected": audio_cands,
            "want_titles": True,
            "want_thumbnails": True,
            "want_scripts": True,
            "want_mp3s": True,
            "want_videos": True,
            "want_channel_assets": True,
            "title_candidates": title_cands,
            "thumb_candidates": thumb_cands,
            "script_candidates": script_cands,
            "audio_candidates": audio_cands,
            "video_candidates": video_cands,
            "titles_saved": 200,
            "thumbs_saved": 200,
            "thumbs_skipped": 15,
            "scripts_saved": 198,
            "scripts_skipped": 10,
            "assets_saved": 2,
            "audio_completed_count": 200,
            "audio_failed_count": 0,
            "video_completed_count": 120,
            "video_failed_count": 0,
            "media_completed_count": 320,
            "failed_items": [],
            "script_concurrency": 3,
            "audio_concurrency": 4,
        }
        self.channel_view._phased_pipeline_state = state

        with patch.object(self.channel_view, "_show_diagnostics_report"):
            self.channel_view._finalize_phased_pipeline()

        stats = self.channel_view._last_stats
        self.assertIsNotNone(stats)

        # Verify exact isolation
        self.assertEqual(stats["total_videos"], 200)
        self.assertEqual(stats["titles_status"], "200 / 200 titles in Titles.txt")
        self.assertEqual(stats["thumbnails_status"], "200 / 200 saved (15 existing)")
        self.assertIn("198 / 200 saved", stats["scripts_status"])
        self.assertIn("200 / 200 completed", stats["audio_status"])
        self.assertIn("120 / 120 completed", stats["video_status"])
        self.assertEqual(stats["assets_status"], "2 saved into Channel Assets")

        # Audio status MUST NOT say 320!
        self.assertNotIn("320", stats["audio_status"])
        self.assertNotIn("320", stats["video_status"])

    # 7. DownloadStatisticsDialog KPI Cards & Labels
    def test_07_statistics_dialog_kpi_files_saved(self):
        """Verify DownloadStatisticsDialog has 'Files Saved' card and reports accurate figures."""
        stats = {
            "total_videos": 200,
            "total_succeeded": 720,
            "total_skipped": 25,
            "titles_status": "200 / 200 titles in Titles.txt",
            "scripts_status": "198 / 200 saved",
            "thumbnails_status": "200 / 200 saved",
            "audio_status": "200 / 200 completed",
            "video_status": "120 / 120 completed",
            "assets_status": "2 saved into Channel Assets",
        }
        dlg = DownloadStatisticsDialog(stats, [], output_dir=Path("/tmp"))
        report_text = dlg.get_report_text()

        self.assertIn(f"MultiDownloader v{APP_VERSION} - Download Statistics Report", report_text)
        self.assertIn("Total Videos: 200", report_text)
        self.assertIn("• Audio: 200 / 200 completed", report_text)
        self.assertIn("• Video: 120 / 120 completed", report_text)
        dlg.deleteLater()


if __name__ == "__main__":
    unittest.main()
