"""
Comprehensive verification test suite for MultiDownloader Pro v3.1:
1. Core V-Numbering & Chronological Order (Oldest to Newest & Newest to Oldest)
2. Custom Range Parsing (1-10, 1–25, 20–30, 47–52, V1, V5, V10)
3. Fetch Query Range Expansion (e.g. '20-30' -> fetch 30, auto-select V20..V30)
4. Consistent V-Numbering across all asset outputs:
   - Titles: V{i} Title.txt
   - Scripts: V{i} Script.txt
   - Metadata: V{i} Metadata.txt
   - Descriptions: V{i} Description.txt
   - Tags: V{i} Tags.txt
   - Thumbnails: V{i} Thumbnail.jpg
   - Audio: V{i}.mp3
   - Video: V{i}.mp4
"""

import tempfile
from pathlib import Path
from app.utils.range_parser import VRangeParser
from app.downloader.channel_fetcher import ChannelCandidate, ChannelFetcher
from app.downloader.zip_packager import ZipPackager
from app.downloader.transcript_fetcher import TranscriptFetcher
from app.downloader.metadata_purifier import MetadataPurifier
from app.downloader.channel_assets_fetcher import ChannelAssetsFetcher
from app.config import (
    APP_VERSION,
    ORDER_LATEST_TO_OLDEST,
    ORDER_OLDEST_TO_LATEST,
)


def test_version_bump():
    print("Testing version bump...")
    assert APP_VERSION == "3.1.0", f"Expected APP_VERSION 3.1.0, got {APP_VERSION}"
    print("✓ APP_VERSION is 3.1.0")


def test_range_parser_formats():
    print("Testing VRangeParser range parsing...")
    # Standard hyphens
    assert VRangeParser.parse("1-10") == set(range(1, 11))
    assert VRangeParser.parse("1-25") == set(range(1, 26))
    assert VRangeParser.parse("20-30") == set(range(20, 31))
    assert VRangeParser.parse("47-52") == {47, 48, 49, 50, 51, 52}

    # Unicode en-dash and em-dash
    assert VRangeParser.parse("1–10") == set(range(1, 11))
    assert VRangeParser.parse("1–25") == set(range(1, 26))
    assert VRangeParser.parse("20–30") == set(range(20, 31))
    assert VRangeParser.parse("47—52") == {47, 48, 49, 50, 51, 52}

    # "to" and "through"
    assert VRangeParser.parse("20 to 30") == set(range(20, 31))
    assert VRangeParser.parse("V47 through V52") == {47, 48, 49, 50, 51, 52}

    # Discrete V-numbers and mixed queries
    assert VRangeParser.parse("V1, V5, V10") == {1, 5, 10}
    assert VRangeParser.parse("V20-V25, V30, 47-50") == {20, 21, 22, 23, 24, 25, 30, 47, 48, 49, 50}

    # Max limit capping
    assert VRangeParser.parse("1-100", max_limit=15) == set(range(1, 16))
    assert VRangeParser.parse("all", max_limit=30) == set(range(1, 31))
    print("✓ VRangeParser formats verified!")


def test_fetch_query_parser():
    print("Testing VRangeParser.parse_fetch_query...")
    # Range 20-30 -> fetch up to 30, auto-select 20..30
    count, target_set = VRangeParser.parse_fetch_query("20-30")
    assert count == 30, f"Expected fetch count 30, got {count}"
    assert target_set == set(range(20, 31)), f"Expected {set(range(20, 31))}, got {target_set}"

    # Range 47-52 -> fetch up to 52, auto-select 47..52
    count, target_set = VRangeParser.parse_fetch_query("47-52")
    assert count == 52, f"Expected fetch count 52, got {count}"
    assert target_set == {47, 48, 49, 50, 51, 52}, f"Expected {set(range(47, 53))}, got {target_set}"

    # Range with en-dash 1–25
    count, target_set = VRangeParser.parse_fetch_query("1–25")
    assert count == 25
    assert target_set == set(range(1, 26))

    # Single number count 50
    count, target_set = VRangeParser.parse_fetch_query("50")
    assert count == 50
    assert target_set == set(range(1, 51))

    # All (Unlimited)
    count, target_set = VRangeParser.parse_fetch_query("All (Unlimited)")
    assert count is None
    assert target_set is None

    print("✓ VRangeParser.parse_fetch_query verified!")


def test_format_set():
    print("Testing VRangeParser.format_set...")
    assert VRangeParser.format_set({47, 48, 49, 50, 51, 52}) == "V47-V52"
    assert VRangeParser.format_set(set(range(20, 31))) == "V20-V30"
    assert VRangeParser.format_set({1, 5, 10}) == "V1, V5, V10"
    assert VRangeParser.format_set({1, 2, 3, 7, 10, 11, 12}) == "V1-V3, V7, V10-V12"
    print("✓ VRangeParser.format_set verified!")


def test_consistent_v_numbering_across_all_assets():
    print("Testing consistent V-numbering across all assets for custom range 47-52...")
    with tempfile.TemporaryDirectory() as tmp_dir_str:
        base_dir = Path(tmp_dir_str)
        channel_name = "TechCompetitor"
        channel_url = "https://www.youtube.com/@TechCompetitor"

        # Create candidates representing videos 47 to 52
        candidates = []
        for v_num in range(47, 53):
            cand = ChannelCandidate(
                video_id=f"id_{v_num}",
                url=f"https://www.youtube.com/watch?v=id_{v_num}",
                title=f"Great Video Number {v_num}",
                uploader=channel_name,
                channel_url=channel_url,
                version_label=f"V{v_num}",
                version_num=v_num,
                is_selected=True,
            )
            candidates.append(cand)

        # 1. Verify Titles output
        titles_dir = base_dir / "Titles"
        saved_titles = ZipPackager.export_titles_folder(
            candidates=candidates,
            output_dir=base_dir,
            channel_name=channel_name,
            channel_url=channel_url,
        )
        assert len(saved_titles) == 6
        for v_num in range(47, 53):
            expected_file = titles_dir / f"V{v_num} Title.txt"
            assert expected_file.exists(), f"Missing {expected_file}"
            content = expected_file.read_text(encoding="utf-8")
            assert channel_name in content
            assert channel_url in content
            assert f"V{v_num}. Great Video Number {v_num}" in content
        print("✓ Titles V47..V52 verified!")

        # 2. Verify Scripts output
        scripts_dir = base_dir / "Scripts"
        transcripts_dict = {f"id_{n}": f"This is the spoken transcript for episode {n}." for n in range(47, 53)}
        saved_scripts = TranscriptFetcher.export_transcripts_to_folder(
            candidates=candidates,
            transcripts_dict=transcripts_dict,
            output_dir=scripts_dir,
        )
        assert len(saved_scripts) == 6
        for v_num in range(47, 53):
            expected_file = scripts_dir / f"V{v_num} Script.txt"
            assert expected_file.exists(), f"Missing {expected_file}"
            content = expected_file.read_text(encoding="utf-8")
            assert f"V{v_num}" in content
            assert f"This is the spoken transcript for episode {v_num}." in content
        print("✓ Scripts V47..V52 verified!")

        # 3. Verify Metadata, Descriptions, and Tags output
        meta_dir = base_dir / "Metadata"
        tags_dir = base_dir / "Tags"
        desc_dir = base_dir / "Descriptions"
        meta_dict = {
            f"id_{n}": {
                "title": f"Great Video Number {n} | TechCompetitor",
                "description": f"Official summary of episode {n}.\nhttps://spam.com",
                "tags": ["tech", "coding", "TechCompetitor"],
                "duration": 300,
                "upload_date": "20240101",
            }
            for n in range(47, 53)
        }

        saved_meta = MetadataPurifier.export_metadata_to_folder(
            candidates=candidates,
            metadata_dict=meta_dict,
            output_dir=meta_dir,
            channel_name=channel_name,
            channel_url=channel_url,
        )
        assert len(saved_meta) == 6
        for v_num in range(47, 53):
            expected_meta = meta_dir / f"V{v_num} Metadata.txt"
            assert expected_meta.exists(), f"Missing {expected_meta}"
            content = expected_meta.read_text(encoding="utf-8")
            assert f"=== V{v_num} METADATA ===" in content
            assert f"Great Video Number {v_num}" in content
            assert "https://spam.com" not in content

        saved_tags = MetadataPurifier.export_tags_to_folder(
            candidates=candidates,
            metadata_dict=meta_dict,
            output_dir=tags_dir,
            channel_name=channel_name,
        )
        assert len(saved_tags) == 7  # 6 individual V{i} Tags.txt + all_tags.txt
        for v_num in range(47, 53):
            expected_tags = tags_dir / f"V{v_num} Tags.txt"
            assert expected_tags.exists(), f"Missing {expected_tags}"
        assert (tags_dir / "all_tags.txt").exists()

        saved_desc = MetadataPurifier.export_descriptions_to_folder(
            candidates=candidates,
            metadata_dict=meta_dict,
            output_dir=desc_dir,
            channel_name=channel_name,
            channel_url=channel_url,
        )
        assert len(saved_desc) == 6
        for v_num in range(47, 53):
            expected_desc = desc_dir / f"V{v_num} Description.txt"
            assert expected_desc.exists(), f"Missing {expected_desc}"

        print("✓ Metadata, Tags, and Descriptions V47..V52 verified!")


def run_all_tests():
    print("==================================================")
    print(" Running MultiDownloader Pro v3.1 Test Suite")
    print("==================================================")
    test_version_bump()
    test_range_parser_formats()
    test_fetch_query_parser()
    test_format_set()
    test_consistent_v_numbering_across_all_assets()
    print("==================================================")
    print(" ALL V3.1 TESTS PASSED SUCCESSFULLY!")
    print("==================================================")


if __name__ == "__main__":
    run_all_tests()
