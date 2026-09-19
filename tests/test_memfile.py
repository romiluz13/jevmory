"""Memory-file line parser tests (M4): every pinned stance in the
module docstring gets a test. Line numbers are FILE line numbers."""

from __future__ import annotations

import unittest

from jev_md.audit.memfile import (
    MemoryLine,
    parse_memory_file,
    strip_inline_markup,
)


def numbers(lines):
    return [line.number for line in lines]


class ParseMemoryFileTest(unittest.TestCase):
    def test_one_file_line_is_one_auditable_line(self):
        text = (
            "# Project memory\n"
            "\n"
            "## Tooling\n"
            "- first claim\n"
            "- second claim\n"
        )
        lines = parse_memory_file(text)
        self.assertEqual(numbers(lines), [4, 5])
        self.assertEqual([line.text for line in lines],
                         ["first claim", "second claim"])

    def test_list_markers_all_recognized(self):
        text = "\n".join([
            "- dash item",
            "* star item",
            "+ plus item",
            "1. numbered item",
            "2) paren numbered item",
            "  - indented nested item",
        ])
        lines = parse_memory_file(text)
        self.assertEqual([line.text for line in lines], [
            "dash item", "star item", "plus item",
            "numbered item", "paren numbered item", "indented nested item",
        ])

    def test_plain_prose_lines_are_auditable(self):
        lines = parse_memory_file("Always use uv run in this repo.")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].text, "Always use uv run in this repo.")
        self.assertIsNone(lines[0].section)

    def test_blank_lines_skipped(self):
        self.assertEqual(parse_memory_file("\n\n   \n\t\n"), [])

    def test_headings_set_section_not_claims(self):
        text = (
            "# Project memory\n"
            "intro claim\n"
            "## Tooling\n"
            "- tooling claim\n"
            "### Sub\n"
            "- sub claim\n"
        )
        lines = parse_memory_file(text)
        self.assertEqual(numbers(lines), [2, 4, 6])
        self.assertEqual(
            [line.section for line in lines],
            ["Project memory", "Tooling", "Sub"],
        )

    def test_fenced_code_skipped_with_content(self):
        text = (
            "```\n"
            "- this is code, not a claim\n"
            "plain code line\n"
            "```\n"
            "- real claim\n"
        )
        lines = parse_memory_file(text)
        self.assertEqual(numbers(lines), [5])
        self.assertEqual(lines[0].text, "real claim")

    def test_fence_info_string_and_tilde_fences(self):
        text = (
            "```python\n"
            "x = 1\n"
            "```\n"
            "~~~\n"
            "- tilde code\n"
            "~~~\n"
            "- claim after\n"
        )
        lines = parse_memory_file(text)
        self.assertEqual(numbers(lines), [7])
        self.assertEqual(lines[0].text, "claim after")

    def test_tilde_line_does_not_close_backtick_fence(self):
        text = (
            "```\n"
            "- inside\n"
            "~~~\n"
            "- still inside\n"
            "```\n"
            "- outside\n"
        )
        lines = parse_memory_file(text)
        self.assertEqual(numbers(lines), [6])

    def test_marker_line_with_info_text_inside_fence_stays_content(self):
        text = (
            "```\n"
            "```python\n"
            "- nested marker content\n"
            "```\n"
            "- actual claim\n"
        )
        lines = parse_memory_file(text)
        # the ```python line does NOT close the fence (only a bare
        # marker run closes), so the claim is the last line
        self.assertEqual(numbers(lines), [5])

    def test_html_comments_never_audited(self):
        text = (
            "<!-- jev-md v0.1.0 sentinel — do not edit -->\n"
            "# jev.md\n"
            "- claim with <!-- inline note --> kept\n"
            "<!-- full line comment -->\n"
        )
        lines = parse_memory_file(text)
        self.assertEqual(numbers(lines), [3])
        self.assertEqual(lines[0].text, "claim with kept")

    def test_tables_and_rules_skipped(self):
        text = "\n".join([
            "| line | verdict |",
            "|---|---|",
            "| 1 | keep |",
            "---",
            "***",
            "- real claim",
        ])
        lines = parse_memory_file(text)
        self.assertEqual(numbers(lines), [6])

    def test_empty_list_marker_is_structure(self):
        lines = parse_memory_file("- \n-   \ntext")
        self.assertEqual([line.text for line in lines], ["text"])

    def test_empty_and_structural_only_files(self):
        self.assertEqual(parse_memory_file(""), [])
        self.assertEqual(parse_memory_file("# Title\n\n---\n\n| a |\n| b |\n"), [])

    def test_line_id_is_stable_receipt_identity(self):
        lines = parse_memory_file("- claim\n")
        self.assertEqual(lines[0].id, "line:1")
        self.assertIsInstance(lines[0], MemoryLine)


class StripInlineMarkupTest(unittest.TestCase):
    def test_bold_and_strong(self):
        self.assertEqual(strip_inline_markup("**bold claim**"), "bold claim")
        self.assertEqual(strip_inline_markup("__also bold__"), "also bold")

    def test_italic(self):
        self.assertEqual(strip_inline_markup("*ital*"), "ital")
        self.assertEqual(strip_inline_markup("_also ital_"), "also ital")

    def test_bold_before_italic_triple_stars(self):
        self.assertEqual(strip_inline_markup("***both***"), "both")

    def test_inline_code_marks_removed_content_kept(self):
        self.assertEqual(
            strip_inline_markup("run `make lint` first"), "run make lint first"
        )
        self.assertEqual(
            strip_inline_markup("use ``uv run`` here"), "use uv run here"
        )

    def test_links_keep_text_drop_url(self):
        self.assertEqual(
            strip_inline_markup("see [the docs](https://example.com/x)"),
            "see the docs",
        )

    def test_plain_text_untouched(self):
        self.assertEqual(
            strip_inline_markup(" plain text stays "), "plain text stays"
        )


if __name__ == "__main__":
    unittest.main()
