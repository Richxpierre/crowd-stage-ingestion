import unittest
import re
from bs4 import BeautifulSoup
from collector.mini_enrichment import field_nodes

class TestMiniEnrichmentGaps(unittest.TestCase):
    def test_stats_patterns_capture_free_and_ticket_pricing(self):
        html = """
        <html>
            <body>
                <div class="pricing-card">
                    <p>Free admission for all registered attendees</p>
                    <span class="price">Tickets from $299 USD</span>
                </div>
            </body>
        </html>
        """
        nodes, selectors = field_nodes(html, 'event_stats')
        values = [n['value'] for n in nodes]
        self.assertTrue(any('Free admission' in v for v in values))
        self.assertTrue(any('$299' in v for v in values))

    def test_location_patterns_capture_venue_names(self):
        html = """
        <html>
            <body>
                <div class="venue-info">
                    <h1>Moscone Convention Center</h1>
                    <p>747 Howard St, San Francisco, CA</p>
                </div>
            </body>
        </html>
        """
        nodes, selectors = field_nodes(html, 'event_location')
        values = [n['value'] for n in nodes]
        self.assertTrue(any('Convention Center' in v for v in values))

    def test_attendees_pattern_capture(self):
        html = """
        <html>
            <body>
                <section class="highlights">
                    <p>Join over 3,500 industry attendees and delegates</p>
                </section>
            </body>
        </html>
        """
        nodes, selectors = field_nodes(html, 'event_stats')
        values = [n['value'] for n in nodes]
        self.assertTrue(any('3,500' in v for v in values))

if __name__ == '__main__':
    unittest.main()
