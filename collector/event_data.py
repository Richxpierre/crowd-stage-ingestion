"""Event-scoped catalog facts. Never infer sponsors from unrelated page logos."""
import re
from urllib.parse import urlsplit, urljoin
from bs4 import BeautifulSoup
from .parsers import clean_url, plain
from .metadata import country_code


def organization(value):
    if isinstance(value, str):
        return {'name': plain(value, 240)}
    if not isinstance(value, dict):
        return None
    name = plain(value.get('name'), 240)
    if not name:
        return None
    logo = value.get('logo')
    if isinstance(logo, dict):
        logo = logo.get('url') or logo.get('contentUrl')
    return {k: v for k, v in {'name': name, 'website_url': clean_url(value.get('url')),
                              'logo_url': clean_url(logo) if isinstance(logo, str) else None}.items() if v}


def schema_location(node):
    loc = node.get('location')
    if isinstance(loc, list):
        physical = [v for v in loc if isinstance(v, dict) and v.get('@type') != 'VirtualLocation']
        if len(physical) != 1:
            return {}
        loc = physical[0]
    if not isinstance(loc, dict) or loc.get('@type') == 'VirtualLocation':
        return {}
    name = plain(loc.get('name'), 240)
    if not name:
        return {}
    address = loc.get('address') if isinstance(loc.get('address'), dict) else {}
    country = address.get('addressCountry')
    if isinstance(country, dict):
        country = country.get('name') or country.get('identifier')
    details = {'name': name, 'website_url': clean_url(loc.get('url')),
               'street_address': plain(address.get('streetAddress'), 500),
               'city': plain(address.get('addressLocality'), 100),
               'administrative_area': plain(address.get('addressRegion'), 100),
               'postal_code': plain(address.get('postalCode'), 40),
               'country_code': country_code(country)}
    return {'venue_details': {k: v for k, v in details.items() if v}}


def schema_dates(node):
    fields = {}
    schedule = node.get('eventSchedule')
    if isinstance(schedule, list):
        schedule = schedule[0] if len(schedule) == 1 else None
    zone = schedule.get('scheduleTimezone') if isinstance(schedule, dict) else None
    if isinstance(zone, str) and len(zone) <= 100 and re.fullmatch(r'UTC|[A-Za-z_]+/[A-Za-z_+/-]+', zone):
        fields['timezone'] = zone  # Database checks the actual IANA name.
    # Date-only values never become invented midnight timestamps.
    for key, target in [('startDate', 'starts_at'), ('endDate', 'ends_at')]:
        value = node.get(key)
        if isinstance(value, str) and len(value)<=45 and re.fullmatch(r'\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:?\d{2})', value):
            fields[target] = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', value)
    return fields


def schema_details(node, page_url):
    result = {}
    org = node.get('organizer')
    if isinstance(org, list):
        org = org[0] if len(org) == 1 else None
    if organization(org):
        result['organizer'] = organization(org)
    sponsors = []
    for prop, role in [('sponsor', 'sponsor'), ('funder', 'partner')]:
        rows = node.get(prop) or []
        if not isinstance(rows, list):
            rows = [rows]
        for item in rows[:100]:
            entity = organization(item)
            if entity:
                sponsors.append({**entity, 'role': role, 'source_url': page_url, 'method': 'event_jsonld'})
    if sponsors:
        result['sponsors'] = sponsors[:100]
    # Explicit industry labels are separate from free-form keywords and topics.
    industries = node.get('industry') or []
    if isinstance(industries, str):
        industries = [industries]
    if isinstance(industries, list):
        labels = [s.strip() for s in industries if isinstance(s, str) and 2 <= len(s.strip()) <= 60]
        if labels:
            result['industries'] = [{'name': s, 'source_url': page_url, 'method': 'event_jsonld_industry'} for s in labels[:10]]
    return result


def html_sponsors(body, page_url, selectors=None):
    """Only explicit sponsor sections on a dedicated event page are eligible."""
    soup = BeautifulSoup(body, 'html.parser')
    selector = (selectors or {}).get('sponsors_selector') or 'section#sponsors,section.sponsors-section,[data-event-sponsors]'
    out = []
    for section in soup.select(selector)[:8]:
        if section.find_parent(['footer', 'nav', 'aside']):
            continue
        for link in section.select('a[href]')[:100]:
            url = clean_url(urljoin(page_url, link.get('href', '')))
            if not url or urlsplit(url).hostname == urlsplit(page_url).hostname:
                continue
            img = link.find('img')
            name = plain(link.get('aria-label') or (img.get('alt') if img else None) or link.get_text(' ', strip=True), 240)
            if not name or len(name) < 2 or name.casefold() in ('logo', 'sponsor', 'sponsors', 'learn more'):
                continue
            out.append({'name': name, 'website_url': url, 'logo_url': clean_url(urljoin(page_url, img.get('src', ''))) if img and img.get('src') else None,
                        'role': 'sponsor', 'source_url': page_url, 'method': 'event_html_sponsors'})
    # Repeated mobile/desktop markup must not create repeated sponsor links.
    return list({(s['name'].casefold(), s['website_url']): s for s in out}.values())[:100]

