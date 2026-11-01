import asyncio
import unittest
from unittest.mock import Mock,AsyncMock,patch
import time
from urllib.robotparser import RobotFileParser
from collector.fetch import Fetcher,RobotsPathDenied,UnsafeDestination
from collector.crawler_reader import Crawl4AIReader

class RobotsOverrideTests(unittest.TestCase):
    def fetcher(self,respect=True):
        f=Fetcher('CrowdStage test',respect_robots=respect)
        f.last_fetch={}
        response=Mock(status_code=200,headers={})
        response.iter_content.return_value=[b'<h1>Conference</h1>']
        f.session.get=Mock(return_value=response)
        policy=RobotFileParser();policy.parse(['User-agent: *','Disallow: /'])
        f._get_robots=Mock(return_value=policy)
        return f
    @patch('collector.fetch.host_of',return_value='directory.org')
    def test_default_policy_blocks_disallowed_page(self,host):
        f=self.fetcher()
        with self.assertRaises(RobotsPathDenied):f.fetch('https://directory.org/conferences')
        f.session.get.assert_not_called()
    @patch('collector.fetch.host_of',return_value='directory.org')
    def test_override_never_requests_robots(self,host):
        f=self.fetcher(False)
        self.assertEqual(f.fetch('https://directory.org/conferences')[0],200)
        f._get_robots.assert_not_called()
        self.assertEqual(f.session.get.call_args.args[0],'https://directory.org/conferences')
    @patch('collector.fetch.host_of',side_effect=UnsafeDestination('Private host'))
    def test_override_keeps_public_network_guard(self,host):
        f=self.fetcher(False)
        with self.assertRaises(UnsafeDestination):f.fetch('https://internal.local/')
        f.session.get.assert_not_called()
    @patch('collector.fetch.host_of',return_value='directory.org')
    def test_page_access_block_is_not_reported_as_robots(self,host):
        f=self.fetcher(False);f.session.get.return_value.status_code=403
        f.session.get.return_value.headers={'Server':'cloudflare'}
        with self.assertRaisesRegex(PermissionError,'page access block, not a robots'):f.fetch('https://directory.org/conferences')
    def test_override_is_scoped_to_aggregator_not_linked_organizer(self):
        reader=Crawl4AIReader('CrowdStage test',skip_robots_for='https://directory.org/')
        reader.policy_fetcher.fetch=Mock(return_value=(200,b'<urlset/>',{}))
        reader.direct_fetcher.fetch=Mock(return_value=(200,b'<urlset/>',{}))
        asyncio.run(reader.read('https://directory.org/events.xml'))
        asyncio.run(reader.read('https://organizer.org/events.xml'))
        reader.direct_fetcher.fetch.assert_called_once_with('https://directory.org/events.xml')
        reader.policy_fetcher.fetch.assert_called_once_with('https://organizer.org/events.xml')
    def direct_route(self,url,method='GET'):
        reader=Crawl4AIReader('CrowdStage test',skip_robots_for='https://directory.org/')
        reader.current_url='https://directory.org/';reader.resource_deadline=time.monotonic()+30
        request=Mock(url=url,method=method,resource_type='script')
        request.is_navigation_request.return_value=False
        route=Mock(request=request);route.abort=AsyncMock();route.continue_=AsyncMock()
        return reader,route
    @patch('collector.crawler_reader.host_of',return_value='directory.org')
    def test_browser_transport_loads_public_page_directly(self,host):
        reader,route=self.direct_route('https://directory.org/listing')
        asyncio.run(reader._direct_request(route,Mock()))
        route.continue_.assert_awaited_once();route.abort.assert_not_awaited()
    @patch('collector.crawler_reader.host_of',side_effect=UnsafeDestination('Private DNS'))
    def test_browser_transport_checks_dns_before_dispatch(self,host):
        reader,route=self.direct_route('https://directory.org/private')
        asyncio.run(reader._direct_request(route,Mock()))
        route.abort.assert_awaited_once();route.continue_.assert_not_awaited()
    def test_browser_transport_blocks_foreign_hosts_and_form_posts(self):
        for url,method in [('https://private.local/','GET'),('https://directory.org/login','POST')]:
            reader,route=self.direct_route(url,method)
            asyncio.run(reader._direct_request(route,Mock()))
            route.abort.assert_awaited_once();route.continue_.assert_not_awaited()
