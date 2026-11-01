import json
import unittest
from unittest.mock import patch
from collector.parsers import parse_jsonld
from collector.sync import sync_one
from test_ingestion import FakeDB, SOURCE

class ConferenceQualityTests(unittest.TestCase):
    def test_unverified_and_session_sources_cannot_fetch(self):
        for source in ({**SOURCE, "record_scope":"unverified"},
                       {**SOURCE, "url":"https://official.org/schedule/export/schedule.ics"}):
            with self.subTest(source=source), patch("collector.sync.Fetcher") as fetcher:
                with self.assertRaises((PermissionError,ValueError)):
                    sync_one(FakeDB(),source)
                fetcher.assert_not_called()

    def test_news_publication_dates_are_not_conference_dates(self):
        class NewsFetcher:
            def __init__(self,*args,**kwargs): pass
            def fetch(self,*args): return 200,b"<rss><channel><item><title>Company news</title><pubDate>Mon, 01 Jun 2027 00:00:00 GMT</pubDate></item></channel></rss>",{}
        db=FakeDB()
        with patch("collector.sync.Fetcher",NewsFetcher):
            with self.assertRaisesRegex(ValueError,"No valid Event records"):
                sync_one(db,{**SOURCE,"kind":"jsonld"})
        self.assertEqual(db.events,[])

    def test_past_uncancelled_events_are_not_imported(self):
        class PastFetcher:
            def __init__(self,*args,**kwargs): pass
            def fetch(self,*args):
                return 200,b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:old\nSUMMARY:Past conference\nDTSTART;VALUE=DATE:20000101\nDTEND;VALUE=DATE:20000102\nEND:VEVENT\nEND:VCALENDAR",{}
        db=FakeDB()
        with patch("collector.sync.Fetcher",PastFetcher):
            result=sync_one(db,SOURCE)
        self.assertEqual(db.events,[])
        self.assertEqual(result["skipped_count"],1)
        self.assertEqual(db.runs[-1]["status"],"unchanged")

    def test_dedicated_event_identity_survives_a_rename(self):
        source={"url":"https://official.org/event","publisher":"official.org","selectors":{"single_event":True}}
        def parse(name):
            body=("<script type='application/ld+json'>"+json.dumps({"@type":"Event","name":name,"startDate":"2027-06-01"})+"</script>").encode()
            return parse_jsonld(body,source)[0]
        self.assertEqual(parse("Original name")["dedupe_key"],parse("Corrected name")["dedupe_key"])
        self.assertNotEqual(parse("Original name")["content_hash"],parse("Corrected name")["content_hash"])

    def test_catalog_does_not_merge_distinct_events(self):
        source={"url":"https://official.org/events","publisher":"official.org"}
        events=[{"@type":"Event","name":name,"startDate":"2027-06-01"} for name in ["One","Two"]]
        body=("<script type='application/ld+json'>"+json.dumps(events)+"</script>").encode()
        rows=parse_jsonld(body,source)
        self.assertEqual(len({r["dedupe_key"] for r in rows}),2)
