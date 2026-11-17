import json
import unittest
from collector.event_data import schema_details, schema_location, schema_dates, html_sponsors
from collector.parse_passes import extract_pass, assemble, PASS_TYPES


class EventDataTests(unittest.TestCase):
    def test_venue_address_is_structured_and_virtual_location_is_not_a_venue(self):
        node = {'location': [{'@type': 'VirtualLocation', 'url': 'https://stream.org'},
                             {'@type': 'Place', 'name': 'Convention Center', 'address': {
                                 'streetAddress': '123 Main Street', 'addressLocality': 'Boston',
                                 'addressRegion': 'MA', 'postalCode': '02110', 'addressCountry': {'name': 'United States'}}}]}
        value = schema_location(node)['venue_details']
        self.assertEqual(value['country_code'], 'US')
        self.assertEqual(value['street_address'], '123 Main Street')
        self.assertEqual(schema_location({'location': {'@type': 'VirtualLocation', 'name': 'Online'}}), {})

    def test_timestamp_preserves_explicit_offset_but_never_invents_midnight(self):
        self.assertEqual(schema_dates({'startDate': '2027-05-01'}), {})
        self.assertEqual(schema_dates({'startDate': '2027-05-01T09:00:00-04:00'})['starts_at'], '2027-05-01T09:00:00-04:00')

    def test_sponsors_and_industries_are_not_inferred_from_keywords(self):
        node = {'sponsor': [{'@type': 'Organization', 'name': 'Acme', 'url': 'https://acme.org'}],
                'organizer': {'name': 'Organizer', 'url': 'https://organizer.org'}, 'keywords': ['AI', 'Party'], 'industry': 'Agricultural Robotics'}
        data = schema_details(node, 'https://event.org')
        self.assertEqual(data['sponsors'][0]['role'], 'sponsor')
        self.assertEqual(data['sponsors'][0]['source_url'], 'https://event.org')
        self.assertEqual(data['industries'][0]['name'], 'Agricultural Robotics')
        self.assertNotIn('industries', schema_details({'keywords': ['Agricultural Robotics']}, 'https://event.org'))

    def test_html_sponsors_require_explicit_section_and_ignore_footer(self):
        body = b'<main><img alt="Unrelated" src="x"><section id="sponsors"><a href="https://acme.org"><img alt="Acme" src="/logo.png"></a></section></main><footer><section id="sponsors"><a href="https://tracker.org">Tracker</a></section></footer>'
        result = html_sponsors(body, 'https://event.org')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], 'Acme')
        self.assertEqual(result[0]['logo_url'], 'https://event.org/logo.png')

    def test_sponsor_identity_does_not_leak_between_catalog_editions(self):
        nodes = [{'@type': 'Event', 'name': name, 'startDate': date, 'endDate': date,
                  'url': 'https://event.org/' + name, 'sponsor': {'@type': 'Organization', 'name': name+' Sponsor', 'url': 'https://'+name+'.org'}}
                 for name, date in [('One', '2027-05-01'), ('Two', '2027-06-01')]]
        body = ('<script type="application/ld+json">'+json.dumps(nodes)+'</script>').encode()
        source = {'url': 'https://event.org', 'name': 'Catalog', 'publisher': 'Organizer', 'selectors': {}}
        report = {kind: {'status': 'success', 'results': extract_pass(kind, {'https://event.org': body}, source, {}), 'errors': []} for kind in PASS_TYPES}
        records = assemble(report, source)
        self.assertEqual({r['title']: r['sponsors'][0]['name'] for r in records}, {'One': 'One Sponsor', 'Two': 'Two Sponsor'})

