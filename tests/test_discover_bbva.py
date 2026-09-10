"""Offline tests using invented HTML, never archived publisher pages."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import discover_bbva as bbva


def card(url="/en/publicaciones/example/", summary="<p class='cardTexto'>A <em>short</em> introduction.</p>"):
    return f"""<div id='card-1' data-fecha='2026-09-10' data-permalink='{url}'>
      <article><a href='{url}'><img src='unused.png'>
      <h3 class='cardTitulo'>Example &amp; <span>analysis</span></h3>
      {summary}</a></article></div>"""


class DiscoveryTests(unittest.TestCase):
    def test_nested_text_entities_and_duplicate_fragments(self):
        items = bbva.parse_listing(card() + card(url="/en/publicaciones/example/#section"))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Example & analysis")
        self.assertEqual(items[0]["listing_summary"], "A short introduction.")
        self.assertEqual(items[0]["url"], "https://www.bbvaresearch.com/en/publicaciones/example/")

    def test_absent_summary_not_invented_and_outside_text_ignored(self):
        items = bbva.parse_listing(card(summary="") + "<p class='cardTexto'>Outside card</p>")
        self.assertNotIn("listing_summary", items[0])

    def test_bad_card_or_changed_page_rejected(self):
        for html in ["<html>Access denied</html>", card().replace("2026-09-10", "invalid"),
                     card(url="https://example.com/en/publicaciones/example/"),
                     card().replace("</div>", "")]:
            with self.subTest(html=html), self.assertRaises(ValueError):
                bbva.parse_listing(html)

    def test_conflicting_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            bbva.parse_listing(card() + card().replace("Example &amp;", "Different &amp;"))

    @patch.object(bbva, "fetch", return_value=("User-agent: *\nDisallow: /en/", "text/plain"))
    def test_robots_denial_stops_before_listing(self, fetch):
        with self.assertRaises(ValueError):
            bbva.collect()
        fetch.assert_called_once_with(bbva.ROBOTS_URL)

    @patch.object(bbva.time, "sleep")
    @patch.object(bbva, "fetch", side_effect=[
        ("User-agent: *\nDisallow:\nCrawl-delay: 7", "text/plain"),
        (card(), "text/html"),
    ])
    def test_only_robots_and_listing_requested_and_delay_honoured(self, fetch, sleep):
        data = bbva.collect()
        self.assertEqual([call.args[0] for call in fetch.call_args_list],
                         [bbva.ROBOTS_URL, bbva.LISTING_URL])
        sleep.assert_called_once_with(7)
        self.assertEqual(data["item_count"], 1)

    @patch.object(bbva.subprocess, "run")
    def test_http_block_has_no_retry(self, run):
        run.return_value = bbva.subprocess.CompletedProcess([], 0, b"Denied\n403\ntext/html")
        with self.assertRaises(ValueError):
            bbva.fetch(bbva.LISTING_URL)
        self.assertEqual(run.call_count, 1)

    @patch.object(bbva, "collect", side_effect=ValueError("blocked"))
    @patch.object(bbva, "write_output")
    def test_failure_does_not_write(self, write, collect):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bbva.main(), 1)
        write.assert_not_called()

    def test_json_replacement_and_failed_serialization_preserve_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.json"
            bbva.write_output({"items": []}, output)
            self.assertEqual(json.loads(output.read_text()), {"items": []})
            previous = output.read_bytes()
            with self.assertRaises(TypeError):
                bbva.write_output({"invalid": object()}, output)
            self.assertEqual(output.read_bytes(), previous)
            self.assertEqual(list(Path(folder).iterdir()), [output])


if __name__ == "__main__":
    unittest.main()
