"""
V-Number Range Parser and Formatter for MultiDownloader Pro v3.1.
Parses user-specified ranges such as '1-10', '1–25', '20–30', '47–52', 'V1 to V10', 'V20-V30', 'V1, V5, V10'.
"""

import re
from typing import Set, List, Optional, Tuple


class VRangeParser:
    """Parses and formats V-number ranges and selections."""

    @staticmethod
    def parse(query: str, max_limit: Optional[int] = None) -> Set[int]:
        """
        Parses a custom V-range string into a set of 1-indexed integers.
        Supports:
            - '1-10', '1–25', '20–30', '47–52'
            - 'V1 to V10', 'V1 - V10', 'V1-V10', 'v1 to v10'
            - 'V1, V5, V10', '1, 5, 10'
            - 'V20-V30, V35, V40 to V45'
            - 'all', 'ALL', '*'
        """
        if not query or not query.strip():
            return set()

        text = query.strip()
        if text.lower() in ("all", "*", "everything", "unlimited"):
            if max_limit and max_limit > 0:
                return set(range(1, max_limit + 1))
            return set()

        # Normalize 'to', 'through', en-dash '–', em-dash '—' to '-'
        text = re.sub(r"\s*(?:to|through)\s*", "-", text, flags=re.IGNORECASE)
        text = text.replace("–", "-").replace("—", "-")

        # Split by comma, semicolon, or whitespace outside ranges
        chunks = re.split(r"[,;\s]+", text)
        result: Set[int] = set()

        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue

            # Check if range e.g. "V1-V10" or "1-10" or "20-30" or "47-52"
            if "-" in chunk:
                parts = chunk.split("-", 1)
                s_str = re.sub(r"[^\d]", "", parts[0])
                e_str = re.sub(r"[^\d]", "", parts[1])

                if s_str.isdigit() and e_str.isdigit():
                    start_val = int(s_str)
                    end_val = int(e_str)
                    if start_val > end_val:
                        start_val, end_val = end_val, start_val

                    for num in range(start_val, end_val + 1):
                        if num > 0:
                            if max_limit is None or num <= max_limit:
                                result.add(num)
            else:
                # Single item e.g. "V1", "V5", "47"
                num_str = re.sub(r"[^\d]", "", chunk)
                if num_str.isdigit():
                    num = int(num_str)
                    if num > 0:
                        if max_limit is None or num <= max_limit:
                            result.add(num)

        return result

    @staticmethod
    def parse_fetch_query(query: str) -> Tuple[Optional[int], Optional[Set[int]]]:
        """
        Parses user input in the 'Videos to Fetch' input field.
        Returns (max_fetch_count, auto_select_set):
            - '20-30' -> (30, {20, 21, ..., 30})
            - '47-52' -> (52, {47, 48, ..., 52})
            - '1-25'  -> (25, {1, 2, ..., 25})
            - '50'    -> (50, {1, ..., 50})
            - 'All'   -> (None, None)
        """
        if not query or not query.strip():
            return 50, None

        q = query.strip()
        if q.lower() in ("all", "*", "unlimited", "everything", "all (unlimited)"):
            return None, None

        # Normalize dashes and 'to'
        clean = re.sub(r"\s*(?:to|through)\s*", "-", q, flags=re.IGNORECASE)
        clean = clean.replace("–", "-").replace("—", "-")

        # Range format: e.g. 20-30 or 47-52
        if "-" in clean:
            parts = clean.split("-", 1)
            s_str = re.sub(r"[^\d]", "", parts[0])
            e_str = re.sub(r"[^\d]", "", parts[1])
            if s_str.isdigit() and e_str.isdigit():
                s_val = int(s_str)
                e_val = int(e_str)
                if s_val > e_val:
                    s_val, e_val = e_val, s_val
                target_set = set(range(s_val, e_val + 1))
                return e_val, target_set

        # Single number e.g. "50", "80", "100"
        digits = re.sub(r"[^\d]", "", clean)
        if digits.isdigit():
            val = int(digits)
            if val > 0:
                return val, set(range(1, val + 1))

        return 50, None

    @staticmethod
    def format_set(numbers: Set[int]) -> str:
        """
        Formats a set of integers into compact range notation:
        {1, 2, 3, 5, 8, 9, 10} -> 'V1-V3, V5, V8-V10'
        {47, 48, 49, 50, 51, 52} -> 'V47-V52'
        """
        if not numbers:
            return "None"

        sorted_nums = sorted(numbers)
        ranges: List[Tuple[int, int]] = []
        start = sorted_nums[0]
        end = sorted_nums[0]

        for n in sorted_nums[1:]:
            if n == end + 1:
                end = n
            else:
                ranges.append((start, end))
                start = n
                end = n
        ranges.append((start, end))

        parts = []
        for s, e in ranges:
            if s == e:
                parts.append(f"V{s}")
            else:
                parts.append(f"V{s}-V{e}")

        return ", ".join(parts)

