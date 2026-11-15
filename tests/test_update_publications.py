import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("updater", Path(__file__).resolve().parents[1] / "scripts/update_publications.py")
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
CONFIG = {"search_query": "au:Samborska", "author_names": ["Luba Samborska", "Liubov Samborska"], "overrides": {}}


def paper(identifier="2604.03412", **kwargs):
    return dict({"id": identifier, "title": "A graph paper", "authors": ["Greg Bodwin", "Luba Samborska"],
                 "published": "2026-04-03T19:15:44Z", "updated": "2026-07-09T01:24:09Z",
                 "primary_category": "cs.DS", "journal_ref": "", "doi": ""}, **kwargs)


def feed(entry, total=1):
    return f'''<feed xmlns="{updater.ATOM}" xmlns:opensearch="{updater.OPEN_SEARCH}" xmlns:arxiv="{updater.ARXIV}">
      <opensearch:totalResults>{total}</opensearch:totalResults><opensearch:startIndex>0</opensearch:startIndex>{entry}</feed>'''.encode()


ENTRY = '''<entry><id>http://arxiv.org/abs/2604.03412v3</id><title>Graphs &amp; paths</title>
  <published>2026-04-03T19:15:44Z</published><updated>2026-07-09T01:24:09Z</updated>
  <author><name>Luba Samborska</name></author><arxiv:primary_category term="cs.DS"/></entry>'''


class PublicationTests(unittest.TestCase):
    def test_both_names_and_unrelated_authors(self):
        for name in ("Luba Samborska", "Liubov Samborska"):
            self.assertTrue(updater.matches_author([name], CONFIG["author_names"]))
        for name in ("Viktoriia Samborska", "L. Samborska", "Luba Samborska-Smith"):
            self.assertFalse(updater.matches_author([name], CONFIG["author_names"]))

    def test_atom_entities_version_and_filter(self):
        records, total, _, count = updater.parse_feed(feed(ENTRY + ENTRY.replace("Luba Samborska", "Other Samborska"), 2), CONFIG["author_names"])
        self.assertEqual((total, count, len(records)), (2, 2, 1))
        self.assertEqual(records[0]["id"], "2604.03412")
        self.assertEqual(records[0]["title"], "Graphs & paths")

    def test_bad_responses_are_rejected(self):
        for payload in (b"<html>Unavailable</html>", b"broken", b"<!DOCTYPE feed><feed/>", b"<feed/>"):
            with self.assertRaises(updater.SyncError):
                updater.parse_feed(payload, CONFIG["author_names"])

    def test_merge_retains_missing_and_uses_newest_version(self):
        old = paper()
        newest = paper(title="Revised title", updated="2026-11-01T00:00:00Z")
        new_paper = paper("2611.08978", published="2026-11-06T00:00:00Z")
        merged = updater.merge_papers([old], [newest, old, new_paper], CONFIG)
        self.assertEqual([p["id"] for p in merged], ["2611.08978", "2604.03412"])
        self.assertEqual(merged[1]["title"], "Revised title")
        self.assertEqual(len(updater.merge_papers(merged, [new_paper], CONFIG)), 2)

    def test_untrusted_metadata_is_html_escaped(self):
        rendered = updater.render_publications([paper(title='<script>alert("x")</script>', authors=["<b>Other</b>", "Luba Samborska"])], CONFIG)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<b>Other</b>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_local_overrides_survive_metadata_changes(self):
        config = dict(CONFIG, overrides={"2604.03412": {"venue": "FOCS 2026", "description_html": "A curated summary."}})
        rendered = updater.render_publications([paper(title="Updated title")], config)
        self.assertIn("FOCS 2026", rendered)
        self.assertIn("A curated summary.", rendered)
        self.assertIn("Updated title", rendered)

    def test_exclusions(self):
        config = dict(CONFIG, exclude_ids=["2604.03412"])
        self.assertEqual(updater.merge_papers([paper()], [paper()], config), [])

    def test_refresh_preserves_submission_label_without_topic_badges(self):
        config = dict(CONFIG, overrides={"2611.08978": {"venue": "In Submission"},
                                         "2604.03412": {"venue": "FOCS 2026", "topic": "Graph algorithms"}})
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "index.html").write_text(updater.START + updater.END)
            updater.update_site(root, config, [paper(), paper("2611.08978")])
            updater.update_site(root, config, [paper(), paper("2611.08978", title="Revised title", updated="2026-11-09T00:00:00Z")])
            result = (root / "index.html").read_text()
            self.assertIn('<div class="paper-meta"><span class="venue">In Submission</span></div>', result)
            self.assertIn('<div class="paper-meta"><span class="venue">FOCS 2026</span></div>', result)
            self.assertNotIn('<span>cs.DS</span>', result)
            self.assertNotIn('Graph algorithms', result)
            self.assertIn('Revised title', result)
            self.assertIn('primaryClass = {cs.DS}', result)

    def test_idempotence_and_preservation_of_surrounding_page(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            page = "<main>Keep my bio" + updater.START + "old" + updater.END + "Keep my photo</main>"
            (root / "index.html").write_text(page)
            first = updater.update_site(root, CONFIG, [paper()])
            second = updater.update_site(root, CONFIG, [paper()])
            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            result = (root / "index.html").read_text()
            self.assertTrue(result.startswith("<main>Keep my bio"))
            self.assertTrue(result.endswith("Keep my photo</main>"))

    def test_fetch_failure_keeps_both_files_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "index.html").write_text(updater.START + updater.END)
            updater.update_site(root, CONFIG, [paper()])
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            with patch.object(updater, "fetch_publications", side_effect=updater.SyncError("Unavailable")):
                with self.assertRaises(updater.SyncError):
                    updater.update_site(root, CONFIG)
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})

    def test_zero_results_does_not_replace_existing_list(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return feed("", total=0)
        with patch.object(updater, "urlopen", return_value=Response()):
            with self.assertRaises(updater.SyncError):
                updater.fetch_publications(CONFIG)


if __name__ == "__main__":
    unittest.main()
