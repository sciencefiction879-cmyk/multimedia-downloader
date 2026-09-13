"""
V-Number Range Parser and Formatter for MultiDownloader.
Parses user-specified ranges such as 'V1 to V10', 'V20-V30', 'V1, V5, V10', '1-5, 8, 12-15'.
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
            - 'V1 to V10', 'V1 - V10', 'V1-V10', 'v1 to v10'
            - 'V1, V5, V10', '1, 5, 10'
            - 'V20-V30, V35, V40 to V45'
            - 'all', 'ALL', '*'
        """
        if not query or not query.strip():
            return set()

        text = query.strip()
        if text.lower() in ("all", "*", "everything"):
            if max_limit and max_limit > 0:
                return set(range(1, max_limit + 1))
            return set()

        # Normalize 'to', 'through', en-dash, em-dash to '-'
        text = re.sub(r"\s+(?:to|through)\s+", "-", text, flags=re.IGNORECASE)
        text = text.replace("–", "-").replace("—", "-")

        # Split by comma or semicolon
        chunks = re.split(r"[,;]+", text)
        result: Set[int] = set()

        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue

            # Check if range e.g. "V1 - V10" or "1-10"
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
                # Single item e.g. "V1" or "5"
                num_str = re.sub(r"[^\d]", "", chunk)
                if num_str.isdigit():
                    num = int(num_str)
                    if num > 0:
                        if max_limit is None or num <= max_limit:
                            result.add(num)

        return result

    @staticmethod
    def format_set(numbers: Set[int]) -> str:
        """
        Formats a set of integers into compact range notation:
        {1, 2, 3, 5, 8, 9, 10} -> 'V1-V3, V5, V8-V10'
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
