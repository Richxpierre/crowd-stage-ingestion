import unittest
from unittest.mock import patch,Mock
from collector.fetch import Fetcher,UnsafeDestination,RobotsPolicyRedirect

def response(status=200,location=None,body=b'User-agent: *\nDisallow: /private',kind='text/plain'):
 r=Mock();r.status_code=status;r.headers={'Content-Type':kind}
 if location:r.headers['Location']=location
 r.iter_content.return_value=[body]
 return r
class RobotsRedirectTests(unittest.TestCase):
 def test_public_cross_authority_redirect_keeps_original_policy_scope(self):
  f=Fetcher('Research (+https://example.org)');f.session.get=Mock(side_effect=[response(301,'https://policy.example.net/wp/robots.txt'),response()])
  with patch('collector.fetch.host_of',return_value='public.example'):
   policy=f._get_robots('organizer.example.org')
  self.assertFalse(policy.can_fetch('Research','https://organizer.example.org/private'))
  self.assertEqual(f.session.get.call_count,2)
 def test_html_policy_is_not_an_allow_all_and_is_temporarily_cached(self):
  f=Fetcher('Research (+https://example.org)');f.session.get=Mock(return_value=response(kind='text/html',body=b'<h1>Conference</h1>'))
  with patch('collector.fetch.host_of',return_value='public.example'):
   for _ in range(2):
    with self.assertRaises(RobotsPolicyRedirect):f._get_robots('organizer.example.org')
  self.assertEqual(f.session.get.call_count,1)
 def test_private_policy_redirect_is_never_requested(self):
  f=Fetcher('Research (+https://example.org)');f.session.get=Mock(return_value=response(301,'https://private.internal/robots.txt'))
  def guard(url):
   if 'private.internal' in url:raise UnsafeDestination('Private destination')
   return 'public.example'
  with patch('collector.fetch.host_of',side_effect=guard):
   with self.assertRaises(UnsafeDestination):f._get_robots('organizer.example.org')
  self.assertEqual(f.session.get.call_count,1)
 def test_policy_redirect_loop_is_bounded(self):
  f=Fetcher('Research (+https://example.org)');f.session.get=Mock(return_value=response(301,'https://organizer.example.org/robots.txt'))
  with patch('collector.fetch.host_of',return_value='public.example'):
   with self.assertRaises(PermissionError):f._get_robots('organizer.example.org')
  self.assertEqual(f.session.get.call_count,6)

