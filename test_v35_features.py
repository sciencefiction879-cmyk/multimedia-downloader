"""
Comprehensive Test Suite for MultiDownloader Pro v3.5.0
Validates:
1. Version bump to 3.5.0
2. Competitor script word count removal & clean formatting (no timestamps, hashtags, labels)
3. Script content validation & no blank scripts
4. High concurrency spinbox range (up to 500) and smart default concurrency
5. 5 Automatic retries across all assets (transcripts, audio, thumbnails, assets, titles)
6. Popularity sorting ('Most Popular to Least Popular' and 'Least Popular to Most Popular') with V1..Vn consistency
7. Selective download & skip ranges (compound ranges, independent script/audio ranges, linked titles)
8. Phased & Parallel concurrent execution architecture
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

# Ensure QApplication exists for UI tests
app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)

from app.config import (
    APP_VERSION,
    MAX_CONCURRENT_DOWNLOADS,
    DEFAULT_MAX_RETRIES,
    ORDER_POPULAR_TO_LEAST,
    ORDER_LEAST_TO_POPULAR,
    CHANNEL_ORDER_OPTIONS,
)
from app.downloader.channel_fetcher import ChannelCandidate, ChannelFetcher
from app.downloader.transcript_fetcher import TranscriptFetcher
from app.downloader.zip_packager import ZipPackager
from app.downloader.queue_manager import QueueManager
from app.models.download_item import DownloadItem, DownloadStatus
from app.models.settings_model import Settings
from app.utils.range_parser import VRangeParser
from app.ui.views.channel_view import ChannelView


class TestV35Features(unittest.TestCase):

    def test_01_version_and_config(self):
        """Test v3.5.0 version string and updated limits."""
        self.assertIn(APP_VERSION, ["3.5.0", "3.6.0", "3.7.0"])
        self.assertEqual(MAX_CONCURRENT_DOWNLOADS, 500)
        self.assertEqual(DEFAULT_MAX_RETRIES, 5)
        self.assertIn(ORDER_POPULAR_TO_LEAST, CHANNEL_ORDER_OPTIONS)
        self.assertIn(ORDER_LEAST_TO_POPULAR, CHANNEL_ORDER_OPTIONS)

    def test_02_remove_competitor_word_count_line(self):
        """Test competitor script word count header is completely removed from all scripts."""
        raw_text = (
            "V1 Competitor Script Word Count: 5,133\n\n"
            "This is the actual video voiceover content about astronomy.\n"
            "Here is another sentence explaining the cosmos #space #galaxy.\n\n"
            "V2 Competitor Script Word Count: 5,465\n\n"
            "Subscribe to our channel and visit our competitor website at https://competitor.com\n"
        )
        cleaned = TranscriptFetcher.clean_voiceover_script(raw_text)
        
        # Must not contain any word count line
        self.assertNotIn("Competitor Script Word Count", cleaned)
        self.assertNotIn("Word Count", cleaned)
        # Must not contain hashtags
        self.assertNotIn("#space", cleaned)
        self.assertNotIn("#galaxy", cleaned)
        # Must contain usable content
        self.assertIn("actual video voiceover content about astronomy", cleaned)

        # Also test format_script_with_metadata produces clean content with no competitor word counts or labels
        formatted = TranscriptFetcher.format_script_with_metadata(cleaned, "V1")
        self.assertNotIn("Competitor Script Word Count", formatted)
        self.assertNotIn("Word Count", formatted)
        self.assertNotIn("#space", formatted)
        self.assertIn("actual video voiceover content about astronomy", formatted)

    def test_03_clean_script_formatting_and_validation(self):
        """Test script content validation rejects blank, short, or placeholder text."""
        is_val, reason = TranscriptFetcher.validate_script_content("")
        self.assertFalse(is_val)

        is_val, reason = TranscriptFetcher.validate_script_content("   \n\n   ")
        self.assertFalse(is_val)

        is_val, reason = TranscriptFetcher.validate_script_content("[No transcript available for this video]")
        self.assertFalse(is_val)

        is_val, reason = TranscriptFetcher.validate_script_content("Too short")
        self.assertFalse(is_val)

        valid_sample = "Welcome to today's in-depth tutorial on building desktop applications with Python and Qt. In this guide we will cover every single phase."
        is_val, reason = TranscriptFetcher.validate_script_content(valid_sample)
        self.assertTrue(is_val)
        self.assertIn("Valid", reason)

    def test_04_export_transcripts_never_writes_blank_files(self):
        """Test that export_transcripts_to_folder never writes blank or placeholder scripts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            cand1 = ChannelCandidate(
                video_id="vid1", title="Valid Video", duration=120, upload_date="2026-01-01",
                uploader="Channel", channel_url="", thumbnail="", url="https://youtube.com/watch?v=vid1",
                original_index=1, version_label="V1", version_num=1
            )
            cand2 = ChannelCandidate(
                video_id="vid2", title="Empty Video", duration=120, upload_date="2026-01-01",
                uploader="Channel", channel_url="", thumbnail="", url="https://youtube.com/watch?v=vid2",
                original_index=2, version_label="V2", version_num=2
            )
            transcripts = {
                "vid1": "This is a valid voiceover script with enough words and clear paragraphs for the video.",
                "vid2": "[No transcript available for this video]",
            }

            saved = TranscriptFetcher.export_transcripts_to_folder([cand1, cand2], transcripts, out_dir)
            self.assertEqual(len(saved), 1)
            self.assertTrue((out_dir / "V1 Script.txt").exists())
            self.assertFalse((out_dir / "V2 Script.txt").exists())

    def test_05_popularity_sorting_and_v_numbering(self):
        """Test sorting candidates by view count in both directions with consistent V1..Vn labeling."""
        cands = [
            ChannelCandidate(
                video_id="vid_low", title="Low Views", duration=100, upload_date="2026-01-01",
                uploader="Channel", channel_url="", thumbnail="", url="https://youtube.com/watch?v=vid_low",
                original_index=1, version_label="V1", version_num=1, view_count=500
            ),
            ChannelCandidate(
                video_id="vid_high", title="High Views", duration=100, upload_date="2026-01-02",
                uploader="Channel", channel_url="", thumbnail="", url="https://youtube.com/watch?v=vid_high",
                original_index=2, version_label="V2", version_num=2, view_count=500000
            ),
            ChannelCandidate(
                video_id="vid_mid", title="Mid Views", duration=100, upload_date="2026-01-03",
                uploader="Channel", channel_url="", thumbnail="", url="https://youtube.com/watch?v=vid_mid",
                original_index=3, version_label="V3", version_num=3, view_count=25000
            ),
        ]

        # 1. Most Popular to Least Popular
        sorted_pop = ChannelFetcher.sort_candidates(cands, ORDER_POPULAR_TO_LEAST)
        self.assertEqual(sorted_pop[0].video_id, "vid_high")
        self.assertEqual(sorted_pop[0].version_label, "V1")
        self.assertEqual(sorted_pop[1].video_id, "vid_mid")
        self.assertEqual(sorted_pop[1].version_label, "V2")
        self.assertEqual(sorted_pop[2].video_id, "vid_low")
        self.assertEqual(sorted_pop[2].version_label, "V3")

        # 2. Least Popular to Most Popular
        sorted_least = ChannelFetcher.sort_candidates(cands, ORDER_LEAST_TO_POPULAR)
        self.assertEqual(sorted_least[0].video_id, "vid_low")
        self.assertEqual(sorted_least[0].version_label, "V1")
        self.assertEqual(sorted_least[1].video_id, "vid_mid")
        self.assertEqual(sorted_least[1].version_label, "V2")
        self.assertEqual(sorted_least[2].video_id, "vid_high")
        self.assertEqual(sorted_least[2].version_label, "V3")

    def test_06_range_parser_compound_and_selective(self):
        """Test compound V-range parsing and filtering for selective download manager."""
        # Compound range parsing: e.g. "V1-V10 + V25-V35" or "V3, V8, V17" or "V31+"
        res1 = VRangeParser.parse("V1-V10 + V25-V30", max_limit=50)
        expected1 = set(range(1, 11)) | set(range(25, 31))
        self.assertEqual(res1, expected1)

        res2 = VRangeParser.parse("V3, V8, V17")
        self.assertEqual(res2, {3, 8, 17})

        res3 = VRangeParser.parse("V31+", max_limit=40)
        self.assertEqual(res3, set(range(31, 41)))

        # Candidate filtering
        candidates = [
            ChannelCandidate(
                video_id=f"v{i}", title=f"Video {i}", duration=60, upload_date="2026-01-01",
                uploader="Ch", channel_url="", thumbnail="", url=f"http://vid{i}",
                original_index=i, version_label=f"V{i}", version_num=i
            )
            for i in range(1, 31)
        ]

        # Filter: Exclude V1-V10, V25-V35
        filtered_skip = VRangeParser.filter_candidates(candidates, exclude_spec="V1-V10, V25-V35")
        filtered_nums = [c.version_num for c in filtered_skip]
        self.assertEqual(filtered_nums, list(range(11, 25)))

        # Filter: Include specific range V11-V20
        filtered_inc = VRangeParser.filter_candidates(candidates, include_spec="V11-V20")
        inc_nums = [c.version_num for c in filtered_inc]
        self.assertEqual(inc_nums, list(range(11, 21)))

    def test_07_queue_manager_5_retries(self):
        """Test QueueManager automatically retries failed downloads up to 5 times."""
        settings = Settings()
        settings.max_retries = 5
        qm = QueueManager(settings)

        item = DownloadItem(
            url="https://youtube.com/watch?v=test",
            media_type="Audio",
            title="Test Audio",
            version_label="V1",
        )
        self.assertEqual(item.retries, 0)

        # Simulate 4 consecutive failures
        for i in range(1, 5):
            qm._on_worker_error(item, f"Simulated network dropout {i}")
            self.assertEqual(item.retries, i)
            self.assertEqual(item.status, DownloadStatus.QUEUED)
            self.assertIn(f"Retrying ({i}/5)", item.error_message)

        # 5th failure exceeds max_retries
        qm._on_worker_error(item, "Fatal error on 5th retry")
        self.assertEqual(item.retries, 5)

    def test_08_ui_concurrency_spinboxes_and_smart_defaults(self):
        """Test spinboxes allow setting concurrency up to 500 and auto-adapt to video count."""
        settings = Settings()
        qm = QueueManager(settings)
        view = ChannelView(qm, settings)

        # Spinboxes range check
        self.assertEqual(view.spin_script_concurrency.maximum(), 500)
        self.assertEqual(view.spin_audio_concurrency.maximum(), 500)
        self.assertEqual(view.spin_script_concurrency.minimum(), 1)
        self.assertEqual(view.spin_audio_concurrency.minimum(), 1)

        # User sets custom high concurrency e.g. 50 or 100
        view.spin_script_concurrency.setValue(50)
        self.assertEqual(view.spin_script_concurrency.value(), 50)
        view.spin_audio_concurrency.setValue(100)
        self.assertEqual(view.spin_audio_concurrency.value(), 100)

        # Smart default concurrency: when fetch finishes with 25 videos, spinboxes adapt
        mock_candidates = [
            ChannelCandidate(
                video_id=f"v{i}", title=f"Video {i}", duration=60, upload_date="2026-01-01",
                uploader="Ch", channel_url="", thumbnail="", url=f"http://vid{i}",
                original_index=i, version_label=f"V{i}", version_num=i, view_count=1000 * i
            )
            for i in range(1, 26)
        ]
        view._on_fetch_finished(mock_candidates)
        self.assertEqual(view.spin_script_concurrency.value(), 25)
        self.assertEqual(view.spin_audio_concurrency.value(), 25)

    def test_09_linked_titles_file_generation(self):
        """Test linked titles feature exports only active videos to Titles.txt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "Titles.txt"
            all_cands = [
                ChannelCandidate(
                    video_id=f"v{i}", title=f"Video {i}", duration=60, upload_date="2026-01-01",
                    uploader="Ch", channel_url="https://channel.url", thumbnail="", url=f"http://vid{i}",
                    original_index=i, version_label=f"V{i}", version_num=i
                )
                for i in range(1, 6)
            ]
            # Suppose only V2 and V4 are active
            active_cands = [all_cands[1], all_cands[3]]

            ZipPackager.export_single_titles_file(
                candidates=active_cands,
                output_file=out_file,
                channel_name="Test Channel",
                channel_url="https://channel.url",
            )

            self.assertTrue(out_file.exists())
            content = out_file.read_text(encoding="utf-8")
            self.assertIn("Total Videos: 2", content)
            self.assertIn("V2 — Video 2", content)
            self.assertIn("V4 — Video 4", content)
            self.assertNotIn("V1 — Video 1", content)
            self.assertNotIn("V3 — Video 3", content)
            self.assertNotIn("V5 — Video 5", content)

    def test_10_phased_and_parallel_pipeline_initialization(self):
        """Test phased & parallel pipeline setup with selective V-ranges."""
        settings = Settings()
        qm = QueueManager(settings)
        view = ChannelView(qm, settings)

        with tempfile.TemporaryDirectory() as tmpdir:
            view.txt_out_dir.setText(tmpdir)
            mock_candidates = [
                ChannelCandidate(
                    video_id=f"v{i}", title=f"Video {i}", duration=60, upload_date="2026-01-01",
                    uploader="Ch", channel_url="", thumbnail="", url=f"http://vid{i}",
                    original_index=i, version_label=f"V{i}", version_num=i, view_count=1000 * i
                )
                for i in range(1, 21)
            ]
            view.candidates = mock_candidates
            view._select_all_candidates()

            # Set skip range: skip V1-V5
            view.txt_skip_ranges.setText("V1-V5")
            # Set script range: V6-V10
            view.txt_script_v_range.setText("V6-V10")
            # Set audio range: V11-V15
            view.txt_audio_v_range.setText("V11-V15")
            # Set linked titles: true
            view.chk_titles_linked.setChecked(True)

            # Prevent modal popups or actual network execution
            with patch("PySide6.QtWidgets.QMessageBox.question", return_value=1), \
                 patch("PySide6.QtWidgets.QMessageBox.warning"), \
                 patch.object(view, "_run_phase_1_titles"):
                view._download_selected_items_clicked()

            state = view._phased_pipeline_state
            self.assertIsNotNone(state)
            # Active selected should exclude V1-V5 (so 15 items: V6 to V20)
            self.assertEqual(len(state["selected"]), 15)
            # Script candidates should be V6-V10 (5 items)
            self.assertEqual([c.version_num for c in state["script_candidates"]], [6, 7, 8, 9, 10])
            # Audio candidates should be V11-V15 (5 items)
            self.assertEqual([c.version_num for c in state["audio_candidates"]], [11, 12, 13, 14, 15])
            # Linked titles should include videos in either scripts or audio (V6 to V15)
            self.assertEqual([c.version_num for c in state["title_candidates"]], list(range(6, 16)))


if __name__ == "__main__":
    unittest.main()
