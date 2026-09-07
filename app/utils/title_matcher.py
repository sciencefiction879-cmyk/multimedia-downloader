"""
Fuzzy title matching, prefix normalization, and sequence alignment engine.
"""

import re
import difflib
from typing import List, Dict, Any, Tuple, Optional

PREFIX_REGEX = re.compile(r"^(v\d+[\.\-\:\)\s]+|\[\d+\]\s*|#\d+\s*|\d+[\.\-\:\)]\s*)", re.IGNORECASE)
STOP_WORDS = frozenset({"a", "the", "on", "by", "from", "at", "with", "of", "to", "in", "and", "an", "for"})


def clean_title_for_matching(title: str) -> str:
    """Removes version numbers, prefixes, special chars, and normalizes title for fuzzy matching."""
    if not title:
        return ""
    stripped = PREFIX_REGEX.sub("", title.strip()).strip()
    cleaned = re.sub(r"[^\w\s]", " ", stripped.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def calculate_title_similarity(target_title: str, candidate_title: str) -> float:
    """Calculates fuzzy match score (0.0 - 1.0) between target title and candidate video title."""
    c_target = clean_title_for_matching(target_title)
    c_cand = clean_title_for_matching(candidate_title)

    if not c_target or not c_cand:
        return 0.0

    if c_target == c_cand:
        return 1.0

    # Substring check
    if len(c_cand) >= 5 and c_cand in c_target:
        return 0.95
    if len(c_target) >= 5 and c_target in c_cand:
        return 0.95

    # Token overlap & subset check
    target_tokens = set(c_target.split()) - STOP_WORDS
    cand_tokens = set(c_cand.split()) - STOP_WORDS

    if target_tokens and cand_tokens:
        if target_tokens.issubset(cand_tokens) or cand_tokens.issubset(target_tokens):
            return 0.95
        overlap = len(target_tokens & cand_tokens)
        union = len(target_tokens | cand_tokens)
        token_ratio = overlap / union if union > 0 else 0.0
    else:
        token_ratio = 0.0

    # Sequence matcher ratio
    seq_ratio = difflib.SequenceMatcher(None, c_target, c_cand).ratio()

    return max(seq_ratio, token_ratio)


def match_and_align_candidates(
    custom_titles: List[str],
    candidates: List[Any],
    threshold: float = 0.6,
) -> Dict[str, Dict[str, Any]]:
    """
    Aligns imported/generated custom titles with fetched channel/playlist video candidates.
    Returns a dict mapping video_id -> match info dict (version_label, target_title, score, match_type).
    """
    matched_info: Dict[str, Dict[str, Any]] = {}
    used_indices = set()

    for seq_idx, title in enumerate(custom_titles, start=1):
        v_label = f"V{seq_idx}"
        best_cand = None
        best_score = 0.0
        best_cand_idx = -1

        for c_idx, cand in enumerate(candidates):
            if c_idx in used_indices:
                continue
            cand_title = getattr(cand, "title", None) or (cand.get("title", "") if isinstance(cand, dict) else str(cand))
            score = calculate_title_similarity(title, cand_title)
            if score > best_score:
                best_score = score
                best_cand = cand
                best_cand_idx = c_idx

        if best_cand and best_score >= threshold:
            used_indices.add(best_cand_idx)
            vid_id = getattr(best_cand, "video_id", None) or (
                (best_cand.get("video_id") or best_cand.get("id")) if isinstance(best_cand, dict) else ""
            )
            matched_info[vid_id] = {
                "version_label": v_label,
                "version_num": seq_idx,
                "custom_title": title,
                "score": round(best_score * 100, 1),
                "matched": True,
            }

    return matched_info

