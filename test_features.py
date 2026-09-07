"""
Automated verification tests for:
1. Competitor Metadata Extraction & Purification
2. Competitor Thumbnails (Sequential naming)
3. Competitor Channel Assets (Banner & Logo)
4. Organization & Titles single folder format (Channel Name + Link + Title)
"""

import os
import shutil
import tempfile
from pathlib import Path
from app.downloader.channel_fetcher import ChannelCandidate
from app.downloader.metadata_purifier import MetadataPurifier
from app.downloader.channel_assets_fetcher import ChannelAssetsFetcher
from app.downloader.zip_packager import ZipPackager


def test_metadata_purifier():
    print("Testing MetadataPurifier...")
    channel_name = "SuperCompetitor"
    channel_url = "https://www.youtube.com/@SuperCompetitor"

    # Test title purification
    raw_title = "How to Code Faster | SuperCompetitor"
    purified_title = MetadataPurifier.purify_title(raw_title, channel_name=channel_name)
    assert purified_title == "How to Code Faster", f"Expected 'How to Code Faster', got '{purified_title}'"

    raw_title_2 = "SuperCompetitor - 10 Productivity Tips #SuperCompetitor"
    purified_title_2 = MetadataPurifier.purify_title(raw_title_2, channel_name=channel_name)
    assert purified_title_2 == "10 Productivity Tips", f"Expected '10 Productivity Tips', got '{purified_title_2}'"

    # Test description purification
    raw_desc = (
        "In this video we cover 10 incredible tips to double your output.\n\n"
        "TIMESTAMPS:\n"
        "00:00 - Introduction\n"
        "02:15 - Tip 1: Deep Work\n\n"
        "Check out our sponsor NordVPN at https://nordvpn.com/supercompetitor use code SUPER\n"
        "Follow me on Instagram: @supercompetitor\n"
        "Join our Discord community: https://discord.gg/abc1234\n"
        "Support my channel on Patreon: https://patreon.com/supercompetitor\n"
        "Buy my merch at https://store.supercompetitor.com\n"
        "Don't forget to subscribe and hit the bell icon!\n"
        "Leave a comment below with your thoughts.\n"
        "Copyright (c) 2024 SuperCompetitor. All rights reserved."
    )
    purified_desc = MetadataPurifier.purify_description(raw_desc, channel_name=channel_name, channel_url=channel_url)

    # Assert no links or promotional lines remain
    assert "https://" not in purified_desc
    assert "NordVPN" not in purified_desc
    assert "Instagram" not in purified_desc
    assert "Discord" not in purified_desc
    assert "Patreon" not in purified_desc
    assert "subscribe" not in purified_desc.lower()
    assert "SuperCompetitor" not in purified_desc
    assert "Tip 1: Deep Work" in purified_desc
    print("✓ Description purification verified!")

    # Test tags purification
    raw_tags = ["supercompetitor", "supercompetitor official", "coding tips", "productivity", "subscribe", "python"]
    purified_tags = MetadataPurifier.purify_tags(raw_tags, channel_name=channel_name)
    assert "supercompetitor" not in [t.lower() for t in purified_tags]
    assert "coding tips" in purified_tags
    assert "productivity" in purified_tags
    assert "python" in purified_tags
    print("✓ Tags purification verified!")

    # Test export to folder
    temp_dir = Path(tempfile.mkdtemp())
    try:
        candidates = [
            ChannelCandidate(
                video_id="vid1",
                url="https://www.youtube.com/watch?v=vid1",
                title="First Great Video",
                uploader=channel_name,
                channel_url=channel_url,
                version_label="V1",
                version_num=1,
            ),
            ChannelCandidate(
                video_id="vid2",
                url="https://www.youtube.com/watch?v=vid2",
                title="Second Great Video",
                uploader=channel_name,
                channel_url=channel_url,
                version_label="V2",
                version_num=2,
            ),
        ]
        meta_dict = {
            "vid1": {
                "title": "First Great Video | SuperCompetitor",
                "description": "Clean topic summary.\nhttps://badlink.com\nFollow us on Twitter: @competitor",
                "tags": ["coding", "supercompetitor"],
                "duration": 180,
                "upload_date": "20240101",
            },
            "vid2": {
                "title": "Second Great Video",
                "description": "Another clean topic summary.",
                "tags": ["design"],
                "duration": 240,
                "upload_date": "20240201",
            },
        }

        meta_dir = temp_dir / "Metadata"
        saved = MetadataPurifier.export_metadata_to_folder(candidates, meta_dict, meta_dir, channel_name, channel_url)
        assert len(saved) == 2
        assert (meta_dir / "V1 Metadata.txt").exists()
        assert (meta_dir / "V2 Metadata.txt").exists()

        with open(meta_dir / "V1 Metadata.txt", "r", encoding="utf-8") as f:
            v1_content = f.read()
        assert "=== V1 METADATA ===" in v1_content
        assert "Title:\nFirst Great Video" in v1_content
        assert "badlink.com" not in v1_content
        print("✓ Metadata folder export verified!")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_titles_folder_structure():
    print("Testing Titles folder structure...")
    temp_dir = Path(tempfile.mkdtemp())
    try:
        channel_name = "TechCompetitor"
        channel_url = "https://www.youtube.com/@TechCompetitor"

        candidates = [
            ChannelCandidate(
                video_id="v1",
                url="https://www.youtube.com/watch?v=v1",
                title="Ultimate AI Guide",
                uploader=channel_name,
                channel_url=channel_url,
                version_label="V1",
                version_num=1,
            ),
            ChannelCandidate(
                video_id="v2",
                url="https://www.youtube.com/watch?v=v2",
                title="Python in 100 Seconds",
                uploader=channel_name,
                channel_url=channel_url,
                version_label="V2",
                version_num=2,
            ),
        ]

        saved_files = ZipPackager.export_titles_folder(
            candidates=candidates,
            output_dir=temp_dir,
            channel_name=channel_name,
            channel_url=channel_url,
        )

        titles_dir = temp_dir / "Titles"
        assert titles_dir.exists(), "Titles folder must exist"
        assert (titles_dir / "V1 Title.txt").exists()
        assert (titles_dir / "V2 Title.txt").exists()
        assert (titles_dir / "titles.txt").exists()
        assert (temp_dir / "titles.txt").exists()

        # Check that no subfolders were created inside Titles
        subdirs = [p for p in titles_dir.iterdir() if p.is_dir()]
        assert len(subdirs) == 0, f"No subfolders should be created inside Titles, found: {subdirs}"

        # Check content format:
        # Line 1: Competitor Channel Name
        # Line 2: Competitor Channel Link
        # Line 3: Existing Video Title (V1. Ultimate AI Guide)
        with open(titles_dir / "V1 Title.txt", "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]

        assert lines[0] == channel_name, f"Line 1 must be '{channel_name}', got '{lines[0]}'"
        assert lines[1] == channel_url, f"Line 2 must be '{channel_url}', got '{lines[1]}'"
        assert lines[2] == "V1. Ultimate AI Guide", f"Line 3 must be 'V1. Ultimate AI Guide', got '{lines[2]}'"

        print("✓ Titles single folder and 3-line format verified successfully!")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_channel_assets_and_thumbnails():
    print("Testing ChannelAssetsFetcher...")
    temp_dir = Path(tempfile.mkdtemp())
    try:
        thumb_dir = temp_dir / "Thumbnails"
        # Test downloading a public test video thumbnail (e.g. YouTube official sample or Rick Astley)
        cand = ChannelCandidate(
            video_id="dQw4w9WgXcQ",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            title="Sample Video",
            version_label="V1",
            version_num=1,
            thumbnail="https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        )
        saved = ChannelAssetsFetcher.download_all_thumbnails([cand], thumb_dir)
        assert len(saved) == 1, "Expected 1 saved thumbnail"
        assert (thumb_dir / "V1 Thumbnail.jpg").exists(), "V1 Thumbnail.jpg must exist"
        assert (thumb_dir / "V1 Thumbnail.jpg").stat().st_size > 1024, "Thumbnail must not be empty"
        print("✓ V1 Thumbnail.jpg downloaded and verified!")

        # Test channel banner and logo extraction and download
        assets_dir = temp_dir / "Channel Assets"
        res = ChannelAssetsFetcher.download_channel_assets(
            channel_url="https://www.youtube.com/@Google",
            output_dir=assets_dir,
        )
        assert res.get("banner_path") or res.get("logo_path"), "Expected banner or logo"
        if res.get("banner_path"):
            assert res["banner_path"].exists()
            print(f"✓ Channel Banner downloaded: {res['banner_path'].name}")
        if res.get("logo_path"):
            assert res["logo_path"].exists()
            print(f"✓ Channel Logo downloaded: {res['logo_path'].name}")
        print("✓ Channel assets verified!")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_metadata_purifier()
    test_titles_folder_structure()
    test_channel_assets_and_thumbnails()
    print("\nALL AUTOMATED TESTS PASSED!")
