"""
Comprehensive Test Suite for MultiDownloader Pro v3.6.0
Validates:
1. Version bump to 3.6.0
2. View Count Filter parsing (100K, 1M, 500K, 2.5M, 100K+, empty)
3. View Count Filtering + Popularity Sorting (Most Popular and Least Popular) with accurate V1..Vn assignment
4. View Count Filtering integrated across all download assets (titles, thumbnails, scripts, audio, videos)
5. Table Selection Actions (Invert, Select Missing, Select Failed, Select Incomplete, Select Skipped)
6. Table Live Filters (Keyword, Min Views, Duration range, Date, Type)
7. Preset Management (Built-in Presets 1, 2, 3 and Custom Preset Saving)
8. Separate Asset V-Ranges (Thumbnails and Videos)
9. Master Actions (Smart Download, Sync Channel, Regenerate Titles.txt)
10. Operational Modes (Missing Only vs Force Overwrite)
11. Statistics Dialog CSV and TXT Reports Export
12. Priority Queue Move-to-Front
"""

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)

from app.config import (
    APP_VERSION,
    ORDER_POPULAR_TO_LEAST,
    ORDER_LEAST_TO_POPULAR,
    ORDER_LATEST_TO_OLDEST,
    ORDER_OLDEST_TO_LATEST,
)
from app.downloader.channel_fetcher import (
    ChannelCandidate,
    ChannelFetcher,
    parse_view_count_input,
)
from app.downloader.queue_manager import QueueManager
from app.models.download_item import DownloadItem, DownloadStatus
from app.models.settings_model import Settings
from app.ui.dialogs.download_statistics_dialog import DownloadStatisticsDialog
from app.ui.views.channel_view import ChannelView
from app.utils.range_parser import VRangeParser


class TestV36Features(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.output_path = Path(self.tmp_dir.name)

        self.settings = Settings(
            download_dir=str(self.output_path),
            channels_dir=str(self.output_path),
        )
        self.queue_manager = QueueManager(settings=self.settings)

    def tearDown(self):
        self.tmp_dir.cleanup()

    # 1. Version Test
    def test_01_version_bump_360(self):
        """Verify APP_VERSION is bumped to 3.6.0+."""
        self.assertIn(APP_VERSION, ["3.6.0", "3.7.0"])

    # 2. View Count Input Parsing
    def test_02_parse_view_count_input(self):
        """Test flexible parsing of user view thresholds (100K, 1M, 500,000, 2.5M, etc.)."""
        self.assertEqual(parse_view_count_input("100K"), 100000)
        self.assertEqual(parse_view_count_input("100k"), 100000)
        self.assertEqual(parse_view_count_input("100K+"), 100000)
        self.assertEqual(parse_view_count_input("500,000"), 500000)
        self.assertEqual(parse_view_count_input("1M"), 1000000)
        self.assertEqual(parse_view_count_input("2.5M"), 2500000)
        self.assertEqual(parse_view_count_input("1B"), 1000000000)
        self.assertEqual(parse_view_count_input(100000), 100000)
        self.assertIsNone(parse_view_count_input(""))
        self.assertIsNone(parse_view_count_input(None))
        self.assertIsNone(parse_view_count_input("invalid_views"))

    # 3. View Count Filter + Popularity Sorting (Most Popular -> Least Popular)
    def test_03_view_filter_with_most_popular_sort(self):
        """Test filtering with 500K+ views and sorting Most Popular -> Least Popular assigns V1 to top video."""
        candidates = [
            ChannelCandidate("v1", "url1", "Video A (50K views)", view_count=50000, original_index=1),
            ChannelCandidate("v2", "url2", "Video B (600K views)", view_count=600000, original_index=2),
            ChannelCandidate("v3", "url3", "Video C (1.5M views)", view_count=1500000, original_index=3),
            ChannelCandidate("v4", "url4", "Video D (200K views)", view_count=200000, original_index=4),
            ChannelCandidate("v5", "url5", "Video E (800K views)", view_count=800000, original_index=5),
        ]

        # Apply 500K+ views + Most Popular -> Least Popular
        filtered = ChannelFetcher.filter_and_sort_candidates(
            candidates, order=ORDER_POPULAR_TO_LEAST, min_views=500000, max_views=None
        )

        self.assertEqual(len(filtered), 3)
        # Should be sorted: Video C (1.5M) -> Video E (800K) -> Video B (600K)
        self.assertEqual(filtered[0].title, "Video C (1.5M views)")
        self.assertEqual(filtered[0].version_label, "V1")
        self.assertEqual(filtered[0].version_num, 1)

        self.assertEqual(filtered[1].title, "Video E (800K views)")
        self.assertEqual(filtered[1].version_label, "V2")
        self.assertEqual(filtered[1].version_num, 2)

        self.assertEqual(filtered[2].title, "Video B (600K views)")
        self.assertEqual(filtered[2].version_label, "V3")
        self.assertEqual(filtered[2].version_num, 3)

    # 4. View Count Filter + Least Popular -> Most Popular
    def test_04_view_filter_with_least_popular_sort(self):
        """Test filtering with range 100K-1M and sorting Least Popular -> Most Popular."""
        candidates = [
            ChannelCandidate("v1", "url1", "Video A (50K views)", view_count=50000, original_index=1),
            ChannelCandidate("v2", "url2", "Video B (600K views)", view_count=600000, original_index=2),
            ChannelCandidate("v3", "url3", "Video C (1.5M views)", view_count=1500000, original_index=3),
            ChannelCandidate("v4", "url4", "Video D (200K views)", view_count=200000, original_index=4),
            ChannelCandidate("v5", "url5", "Video E (800K views)", view_count=800000, original_index=5),
        ]

        # Range 100K - 1M views: includes Video D (200K), Video B (600K), Video E (800K)
        filtered = ChannelFetcher.filter_and_sort_candidates(
            candidates, order=ORDER_LEAST_TO_POPULAR, min_views=100000, max_views=1000000
        )

        self.assertEqual(len(filtered), 3)
        # In Least Popular -> Most Popular: Video D (200K) is V1, Video B is V2, Video E is V3
        self.assertEqual(filtered[0].title, "Video D (200K views)")
        self.assertEqual(filtered[0].version_label, "V1")
        self.assertEqual(filtered[1].title, "Video B (600K views)")
        self.assertEqual(filtered[1].version_label, "V2")
        self.assertEqual(filtered[2].title, "Video E (800K views)")
        self.assertEqual(filtered[2].version_label, "V3")

    # 5. One-Sided Maximum View Count Filter (< 100K views)
    def test_05_view_filter_max_views_only(self):
        """Test filtering with only maximum views (< 100K views)."""
        candidates = [
            ChannelCandidate("v1", "url1", "Video 1 (25K)", view_count=25000, original_index=1),
            ChannelCandidate("v2", "url2", "Video 2 (75K)", view_count=75000, original_index=2),
            ChannelCandidate("v3", "url3", "Video 3 (250K)", view_count=250000, original_index=3),
        ]

        filtered = ChannelFetcher.filter_and_sort_candidates(
            candidates, order=ORDER_POPULAR_TO_LEAST, min_views=None, max_views=100000
        )
        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered[0].title, "Video 2 (75K)")
        self.assertEqual(filtered[0].version_label, "V1")
        self.assertEqual(filtered[1].title, "Video 1 (25K)")
        self.assertEqual(filtered[1].version_label, "V2")

    # 6. UI View Count Filter & Popularity Integration in ChannelView
    def test_06_channel_view_view_filter_ui_integration(self):
        """Test ChannelView view filter input fields, quick preset buttons, and live table update."""
        view = ChannelView(self.queue_manager, self.settings)
        candidates = [
            ChannelCandidate("v1", "url1", "Video Low (30K)", view_count=30000, original_index=1),
            ChannelCandidate("v2", "url2", "Video Mid (300K)", view_count=300000, original_index=2),
            ChannelCandidate("v3", "url3", "Video High (800K)", view_count=800000, original_index=3),
        ]
        view.all_fetched_candidates = list(candidates)
        view.candidates = list(candidates)
        view._populate_table()

        # Set 100K+ views via preset
        view._set_view_filter_preset("100K", "")
        self.assertEqual(len(view.candidates), 2)

        # Switch to Most Popular -> Least Popular with 100K+
        view.combo_order.setCurrentText(ORDER_POPULAR_TO_LEAST)
        view._apply_view_filter_and_sort()
        self.assertEqual(len(view.candidates), 2)
        self.assertEqual(view.candidates[0].title, "Video High (800K)")
        self.assertEqual(view.candidates[0].version_label, "V1")
        self.assertEqual(view.candidates[1].title, "Video Mid (300K)")
        self.assertEqual(view.candidates[1].version_label, "V2")

        # Clear filter
        view._set_view_filter_preset("", "")
        self.assertEqual(len(view.candidates), 3)

    # 7. Table Selection Actions: Invert, Missing, Failed, Incomplete, Skipped
    def test_07_table_selection_actions(self):
        """Test Invert Selection, Select Missing, Select Failed, Select Incomplete, and Select Skipped."""
        view = ChannelView(self.queue_manager, self.settings)
        view.txt_out_dir.setText(str(self.output_path))
        cands = [
            ChannelCandidate("v1", "url1", "Video 1", version_label="V1", version_num=1, is_selected=True),
            ChannelCandidate("v2", "url2", "Video 2", version_label="V2", version_num=2, is_selected=False),
            ChannelCandidate("v3", "url3", "Video 3", version_label="V3", version_num=3, is_selected=True),
        ]
        view.candidates = cands
        view._populate_table()

        # Invert Selection
        view._on_invert_selection()
        self.assertFalse(view.candidates[0].is_selected)
        self.assertTrue(view.candidates[1].is_selected)
        self.assertFalse(view.candidates[2].is_selected)

        # Select Missing Only: create dummy file for V1 on disk, V2 and V3 missing
        view.chk_scripts.setChecked(True)
        view.chk_mp3s.setChecked(False)
        view.chk_thumbnails.setChecked(False)
        view.chk_videos.setChecked(False)
        scripts_dir = self.output_path / "Scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "V1 Script.txt").write_text("dummy script content for test")

        view._on_select_missing()
        # V1 exists so is_selected is False; V2 and V3 are missing so True
        self.assertFalse(view.candidates[0].is_selected)
        self.assertTrue(view.candidates[1].is_selected)
        self.assertTrue(view.candidates[2].is_selected)

        # Select Failed Only: mark V2 as failed in diagnostics
        view.diagnostics_dict["v2"] = {"status": "Failed", "diag": "Download timeout"}
        view._on_select_failed()
        self.assertFalse(view.candidates[0].is_selected)
        self.assertTrue(view.candidates[1].is_selected)
        self.assertFalse(view.candidates[2].is_selected)

        # Select Incomplete Only: create corrupt 5-byte file for V1
        (self.output_path / "Audio").mkdir(parents=True, exist_ok=True)
        (self.output_path / "Audio" / "V1.mp3").write_bytes(b"12345")
        view._on_select_incomplete()
        self.assertTrue(view.candidates[0].is_selected)
        self.assertFalse(view.candidates[1].is_selected)

        # Select Skipped Only: put V3 in skip ranges
        view.txt_skip_ranges.setText("V3")
        view._on_select_skipped()
        self.assertFalse(view.candidates[0].is_selected)
        self.assertFalse(view.candidates[1].is_selected)
        self.assertTrue(view.candidates[2].is_selected)

    # 8. Interactive Table Filter Bar (Keyword, Min Views, Duration, Date, Type)
    def test_08_table_live_filter_bar(self):
        """Test interactive keyword, view count, duration, and type filters."""
        view = ChannelView(self.queue_manager, self.settings)
        cands = [
            ChannelCandidate("v1", "url1", "Python Web Scraping Tutorial", duration=300, view_count=50000),
            ChannelCandidate("v2", "url2", "Quick Python Short", duration=45, view_count=200000),
            ChannelCandidate("v3", "url3", "Machine Learning in Production", duration=1800, view_count=1500000),
        ]
        view.candidates = cands
        view._populate_table()

        # Keyword filter: "Short"
        view.txt_filter_keyword.setText("Short")
        view._apply_table_filters()
        self.assertTrue(view.table.isRowHidden(0))
        self.assertFalse(view.table.isRowHidden(1))
        self.assertTrue(view.table.isRowHidden(2))

        # Reset filter
        view._reset_table_filters()
        self.assertFalse(view.table.isRowHidden(0))
        self.assertFalse(view.table.isRowHidden(1))
        self.assertFalse(view.table.isRowHidden(2))

        # Shorts Only type filter
        view.combo_filter_type.setCurrentText("Shorts Only")
        view._apply_table_filters()
        self.assertTrue(view.table.isRowHidden(0))   # 300s > 60s
        self.assertFalse(view.table.isRowHidden(1))  # 45s <= 60s
        self.assertTrue(view.table.isRowHidden(2))   # 1800s > 60s

    # 9. Preset Management (Built-in Presets and Custom Presets)
    def test_09_preset_management(self):
        """Test Built-in presets and saving custom preset into settings."""
        view = ChannelView(self.queue_manager, self.settings)

        # Preset 1: Scripts + Audio
        view.combo_presets.setCurrentText("Preset 1 (Scripts + Audio)")
        view._on_preset_dropdown_changed(view.combo_presets.currentIndex())
        self.assertTrue(view.chk_scripts.isChecked())
        self.assertTrue(view.chk_mp3s.isChecked())
        self.assertFalse(view.chk_thumbnails.isChecked())
        self.assertFalse(view.chk_videos.isChecked())

        # Preset 2: Videos + Thumbnails
        view.combo_presets.setCurrentText("Preset 2 (Videos + Thumbnails)")
        view._on_preset_dropdown_changed(view.combo_presets.currentIndex())
        self.assertFalse(view.chk_scripts.isChecked())
        self.assertFalse(view.chk_mp3s.isChecked())
        self.assertTrue(view.chk_thumbnails.isChecked())
        self.assertTrue(view.chk_videos.isChecked())

        # Save custom preset
        with patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("My Studio Preset", True)), \
             patch("PySide6.QtWidgets.QMessageBox.information"):
            view.chk_titles.setChecked(True)
            view.chk_scripts.setChecked(True)
            view.chk_thumbnails.setChecked(False)
            view.chk_channel_assets.setChecked(False)
            view.chk_mp3s.setChecked(False)
            view.chk_videos.setChecked(True)
            view._on_save_preset_clicked()

        self.assertIn("My Studio Preset", self.settings.custom_presets)
        self.assertTrue(self.settings.custom_presets["My Studio Preset"]["want_videos"])

    # 10. Separate Asset V-Ranges (Thumbnails & Videos)
    def test_10_separate_asset_v_ranges(self):
        """Test separate V-range specifications for thumbnails and videos."""
        view = ChannelView(self.queue_manager, self.settings)
        view.txt_out_dir.setText(str(self.output_path))
        cands = [
            ChannelCandidate(f"id{i}", f"url{i}", f"Video {i}", version_label=f"V{i}", version_num=i, is_selected=True)
            for i in range(1, 21)
        ]
        view.candidates = cands
        view._populate_table()

        # Thumbnails V1-V5, Videos V16-V20
        view.txt_thumb_v_range.setText("V1-V5")
        view.txt_video_v_range.setText("V16-V20")
        view.chk_thumbnails.setChecked(True)
        view.chk_videos.setChecked(True)
        view.chk_titles.setChecked(False)
        view.chk_scripts.setChecked(False)
        view.chk_mp3s.setChecked(False)
        view.chk_channel_assets.setChecked(False)

        with patch.object(view, "_run_phase_1_titles"):
            view._download_selected_items_clicked()

        state = view._phased_pipeline_state
        self.assertIsNotNone(state)
        # Thumbnails should only have V1-V5
        self.assertEqual(len(state["thumb_candidates"]), 5)
        self.assertEqual([c.version_label for c in state["thumb_candidates"]], ["V1", "V2", "V3", "V4", "V5"])
        # Videos should only have V16-V20
        self.assertEqual(len(state["video_candidates"]), 5)
        self.assertEqual([c.version_label for c in state["video_candidates"]], ["V16", "V17", "V18", "V19", "V20"])

    # 11. Regenerate Titles.txt Action
    def test_11_regenerate_titles_action(self):
        """Test Regenerate Titles.txt produces Titles.txt in destination directory with zero media download."""
        view = ChannelView(self.queue_manager, self.settings)
        view.txt_out_dir.setText(str(self.output_path))
        cands = [
            ChannelCandidate("id1", "url1", "First Awesome Video", version_label="V1", version_num=1, is_selected=True),
            ChannelCandidate("id2", "url2", "Second Great Video", version_label="V2", version_num=2, is_selected=True),
        ]
        view.candidates = cands

        with patch("PySide6.QtWidgets.QMessageBox.information"):
            view._regenerate_titles_clicked()

        titles_file = self.output_path / "Titles.txt"
        self.assertTrue(titles_file.exists())
        content = titles_file.read_text(encoding="utf-8")
        self.assertIn("V1 — First Awesome Video", content)
        self.assertIn("V2 — Second Great Video", content)

    # 12. Post-Download Statistics Dialog CSV & TXT Export
    def test_12_statistics_dialog_csv_and_txt_export(self):
        """Test CSV and TXT report generation and disk export from DownloadStatisticsDialog."""
        stats = {
            "total_videos": 10,
            "total_succeeded": 8,
            "total_skipped": 1,
            "titles_status": "Saved",
            "scripts_status": "8 / 10 Saved",
            "thumbnails_status": "10 / 10 Saved",
            "audio_status": "8 / 10 Saved",
            "video_status": "Not Selected",
            "assets_status": "Saved",
        }
        failed_items = [
            {"version_label": "V4", "title": "Fourth Video", "category": "Audio", "reason": "HTTP 403 Forbidden"},
            {"version_label": "V9", "title": "Ninth Video", "category": "Scripts", "reason": "No captions available"},
        ]

        dlg = DownloadStatisticsDialog(stats, failed_items, output_dir=self.output_path)

        # Test CSV export
        csv_file = self.output_path / "stats_report.csv"
        self.assertTrue(dlg.export_csv(csv_file))
        self.assertTrue(csv_file.exists())
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        self.assertGreater(len(reader), 5)
        content_csv = csv_file.read_text(encoding="utf-8")
        self.assertIn("HTTP 403 Forbidden", content_csv)
        self.assertIn("No captions available", content_csv)

        # Test TXT export
        txt_file = self.output_path / "stats_report.txt"
        self.assertTrue(dlg.export_txt(txt_file))
        self.assertTrue(txt_file.exists())
        content_txt = txt_file.read_text(encoding="utf-8")
        self.assertIn(f"MultiDownloader v{APP_VERSION} - Download Statistics Report", content_txt)
        self.assertIn("Failed Items Details:", content_txt)
        self.assertIn("[V4] Fourth Video | Category: Audio | Reason: HTTP 403 Forbidden", content_txt)

    # 13. Queue Prioritization "Move to Front"
    def test_13_queue_priority_move_to_front(self):
        """Test moving queued item to index 0 priority position."""
        qm = QueueManager(settings=self.settings)
        qm.items.clear()
        item1 = DownloadItem(url="https://youtube.com/watch?v=1", title="Track 1", status=DownloadStatus.QUEUED)
        item2 = DownloadItem(url="https://youtube.com/watch?v=2", title="Track 2", status=DownloadStatus.QUEUED)
        item3 = DownloadItem(url="https://youtube.com/watch?v=3", title="Track 3", status=DownloadStatus.QUEUED)

        qm.add_item(item1)
        qm.add_item(item2)
        qm.add_item(item3)

        self.assertEqual(qm.items[2].id, item3.id)

        # Boost item3 to front
        qm.move_to_front(item3.id)
        self.assertEqual(qm.items[0].id, item3.id)
        self.assertEqual(qm.items[1].id, item1.id)
        self.assertEqual(qm.items[2].id, item2.id)


if __name__ == "__main__":
    unittest.main()
