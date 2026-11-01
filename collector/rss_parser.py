"""Compatibility adapter. Publication dates are never conference dates."""
from .feeds import parse_feed


def parse_rss(body, source):
    return parse_feed(body, source)
