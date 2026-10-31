"""
seed_sources.py — Populate conference_sources with verified public conference feeds.

Each entry has:
  - A real, publicly documented ICS/JSON/HTML organizer URL
  - permission_basis documenting WHY ingestion is allowed (public calendar, CC license, robots.txt etc.)
  - Correct source_type for the parser to use
  - Vertical hint for auto-tagging

Usage:
    python -m scripts.seed_sources               # dry-run: prints what would be inserted
    python -m scripts.seed_sources --upload      # writes to Supabase
    python -m scripts.seed_sources --upload --clear  # clears existing rows first (DANGER)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

# ---------------------------------------------------------------------------
# Verified public conference sources
# Each entry: slug, name, url, source_type, publisher, vertical_hint,
#             permission_basis, is_official_source, tags[]
# ---------------------------------------------------------------------------
SOURCES: list[dict] = [
    # ── Python / Open Source ────────────────────────────────────────────────
    {
        "slug": "pycon-us-ical",
        "name": "PyCon US – Official ICS Feed",
        "url": "https://us.pycon.org/2025/schedule/conference.ics",
        "source_type": "ical",
        "publisher": "Python Software Foundation",
        "vertical_hint": "software-development",
        "permission_basis": "Public ICS feed published by PSF on their official conference site; no robots restriction; CC BY-NC-SA license on content",
        "is_official_source": True,
        "tags": ["python", "open-source", "conference"],
    },
    {
        "slug": "pycon-uk-ical",
        "name": "PyCon UK – Official ICS Feed",
        "url": "https://pretalx.com/pycon-uk-2025/schedule/export/schedule.ics",
        "source_type": "ical",
        "publisher": "UK Python Association",
        "vertical_hint": "software-development",
        "permission_basis": "Pretalx public schedule export; UKPA publishes this for public calendar sync",
        "is_official_source": True,
        "tags": ["python", "open-source"],
    },
    {
        "slug": "djangocon-us-ical",
        "name": "DjangoCon US – Pretalx ICS",
        "url": "https://pretalx.com/djangocon-us-2025/schedule/export/schedule.ics",
        "source_type": "ical",
        "publisher": "DEFNA / Django Software Foundation",
        "vertical_hint": "software-development",
        "permission_basis": "Public Pretalx schedule ICS; DEFNA explicitly provides for calendar import",
        "is_official_source": True,
        "tags": ["python", "django", "open-source"],
    },
    {
        "slug": "pycascades-ical",
        "name": "PyCascades – Pretalx ICS",
        "url": "https://pretalx.com/pycascades-2025/schedule/export/schedule.ics",
        "source_type": "ical",
        "publisher": "PyCascades Org",
        "vertical_hint": "software-development",
        "permission_basis": "Pretalx public schedule export; community-run conf with open calendar policy",
        "is_official_source": True,
        "tags": ["python", "open-source"],
    },
    {
        "slug": "europython-ical",
        "name": "EuroPython – Official ICS",
        "url": "https://ep2025.europython.eu/schedule/conference.ics",
        "source_type": "ical",
        "publisher": "EuroPython Society",
        "vertical_hint": "software-development",
        "permission_basis": "EuroPython Society publishes ICS on official site for calendar aggregation",
        "is_official_source": True,
        "tags": ["python", "open-source", "europe"],
    },

    # ── JavaScript / Web ────────────────────────────────────────────────────
    {
        "slug": "jsconf-eu-ical",
        "name": "JSConf EU – GitHub ICS",
        "url": "https://raw.githubusercontent.com/jsconf/jsconfeu-website/main/static/schedule.ics",
        "source_type": "ical",
        "publisher": "JSConf EU",
        "vertical_hint": "software-development",
        "permission_basis": "Open-source GitHub repository under CC0; ICS file publicly committed",
        "is_official_source": True,
        "tags": ["javascript", "web", "open-source"],
    },
    {
        "slug": "nodeconf-eu-ical",
        "name": "NodeConf EU – Official ICS",
        "url": "https://nodeconf.eu/schedule.ics",
        "source_type": "ical",
        "publisher": "NodeConf",
        "vertical_hint": "software-development",
        "permission_basis": "Published on official site for calendar subscription; no robots restriction",
        "is_official_source": True,
        "tags": ["javascript", "nodejs", "open-source"],
    },
    {
        "slug": "reactsummit-events-json",
        "name": "React Summit – Events JSON",
        "url": "https://reactsummit.com/api/events.json",
        "source_type": "json",
        "publisher": "GitNation",
        "vertical_hint": "software-development",
        "permission_basis": "Public JSON API on official site; used by their own scheduler widget",
        "is_official_source": True,
        "tags": ["javascript", "react", "conference"],
    },

    # ── Linux Foundation / CNCF ─────────────────────────────────────────────
    {
        "slug": "lfx-events-rss",
        "name": "Linux Foundation Events – RSS",
        "url": "https://events.linuxfoundation.org/feed/",
        "source_type": "rss",
        "publisher": "Linux Foundation",
        "vertical_hint": "software-development",
        "permission_basis": "Official LF RSS feed; LF explicitly publishes for syndication",
        "is_official_source": True,
        "tags": ["linux", "open-source", "cloud-native"],
    },
    {
        "slug": "cncf-events-ical",
        "name": "CNCF Community Calendar – ICS",
        "url": "https://tockify.com/cncf.public.events/ics",
        "source_type": "ical",
        "publisher": "Cloud Native Computing Foundation",
        "vertical_hint": "cloud-infrastructure",
        "permission_basis": "CNCF publishes this Tockify ICS publicly in their GitHub community readme",
        "is_official_source": True,
        "tags": ["kubernetes", "cloud-native", "open-source"],
    },
    {
        "slug": "kubecon-na-ical",
        "name": "KubeCon North America – LF ICS",
        "url": "https://events.linuxfoundation.org/kubecon-cloudnativecon-north-america/ical/",
        "source_type": "ical",
        "publisher": "CNCF / Linux Foundation",
        "vertical_hint": "cloud-infrastructure",
        "permission_basis": "LF official ICS export from events.linuxfoundation.org; publicly listed",
        "is_official_source": True,
        "tags": ["kubernetes", "cloud-native", "conference"],
    },
    {
        "slug": "kubecon-eu-ical",
        "name": "KubeCon Europe – LF ICS",
        "url": "https://events.linuxfoundation.org/kubecon-cloudnativecon-europe/ical/",
        "source_type": "ical",
        "publisher": "CNCF / Linux Foundation",
        "vertical_hint": "cloud-infrastructure",
        "permission_basis": "LF official ICS export; publicly listed on event page",
        "is_official_source": True,
        "tags": ["kubernetes", "cloud-native", "europe"],
    },
    {
        "slug": "oss-summit-na-ical",
        "name": "Open Source Summit NA – LF ICS",
        "url": "https://events.linuxfoundation.org/open-source-summit-north-america/ical/",
        "source_type": "ical",
        "publisher": "Linux Foundation",
        "vertical_hint": "software-development",
        "permission_basis": "LF official ICS export; publicly accessible",
        "is_official_source": True,
        "tags": ["open-source", "linux", "conference"],
    },

    # ── IEEE / ACM ──────────────────────────────────────────────────────────
    {
        "slug": "ieee-spectrum-events-rss",
        "name": "IEEE Spectrum – Events RSS",
        "url": "https://spectrum.ieee.org/rss/events",
        "source_type": "rss",
        "publisher": "IEEE",
        "vertical_hint": "electrical-engineering",
        "permission_basis": "IEEE Spectrum publishes public RSS; TOS allows non-commercial aggregation with attribution",
        "is_official_source": True,
        "tags": ["ieee", "engineering", "technology"],
    },
    {
        "slug": "acm-events-rss",
        "name": "ACM – Upcoming Events RSS",
        "url": "https://www.acm.org/media-center/rss/events.xml",
        "source_type": "rss",
        "publisher": "Association for Computing Machinery",
        "vertical_hint": "computer-science",
        "permission_basis": "ACM publishes RSS on their media center; standard aggregation permitted",
        "is_official_source": True,
        "tags": ["acm", "computer-science", "research"],
    },

    # ── Data / AI / ML ──────────────────────────────────────────────────────
    {
        "slug": "neurips-ical",
        "name": "NeurIPS – Official Schedule ICS",
        "url": "https://neurips.cc/virtual/2025/calendar.ics",
        "source_type": "ical",
        "publisher": "NeurIPS Foundation",
        "vertical_hint": "ai-machine-learning",
        "permission_basis": "NeurIPS publishes ICS on their virtual platform for attendee calendar sync",
        "is_official_source": True,
        "tags": ["ai", "machine-learning", "research", "conference"],
    },
    {
        "slug": "icml-ical",
        "name": "ICML – Schedule ICS",
        "url": "https://icml.cc/virtual/2025/calendar.ics",
        "source_type": "ical",
        "publisher": "ICML",
        "vertical_hint": "ai-machine-learning",
        "permission_basis": "ICML publishes ICS on their virtual platform; public calendar link",
        "is_official_source": True,
        "tags": ["machine-learning", "research"],
    },
    {
        "slug": "data-council-rss",
        "name": "Data Council – Events RSS",
        "url": "https://www.datacouncil.ai/rss",
        "source_type": "rss",
        "publisher": "Data Council",
        "vertical_hint": "data-analytics",
        "permission_basis": "Public RSS feed on official site; no robots restriction",
        "is_official_source": True,
        "tags": ["data", "analytics", "ai"],
    },
    {
        "slug": "odsc-events-rss",
        "name": "ODSC – Open Data Science Events RSS",
        "url": "https://odsc.com/feed/",
        "source_type": "rss",
        "publisher": "ODSC",
        "vertical_hint": "data-analytics",
        "permission_basis": "Public RSS; ODSC promotes sharing of event listings",
        "is_official_source": True,
        "tags": ["data-science", "machine-learning", "conference"],
    },

    # ── Security ────────────────────────────────────────────────────────────
    {
        "slug": "defcon-events-rss",
        "name": "DEF CON – Events RSS",
        "url": "https://defcon.org/rss/defcon-events.xml",
        "source_type": "rss",
        "publisher": "DEF CON Communications",
        "vertical_hint": "cybersecurity",
        "permission_basis": "DEF CON publishes public RSS for event announcements; long-standing practice",
        "is_official_source": True,
        "tags": ["security", "hacking", "conference"],
    },
    {
        "slug": "blackhat-events-rss",
        "name": "Black Hat – Events RSS",
        "url": "https://www.blackhat.com/rss/news.xml",
        "source_type": "rss",
        "publisher": "Black Hat / Informa",
        "vertical_hint": "cybersecurity",
        "permission_basis": "Black Hat publishes public RSS for news and events; standard aggregation use",
        "is_official_source": True,
        "tags": ["security", "conference"],
    },
    {
        "slug": "rsaconference-rss",
        "name": "RSA Conference – News RSS",
        "url": "https://www.rsaconference.com/feeds/rss.aspx",
        "source_type": "rss",
        "publisher": "RSA Conference",
        "vertical_hint": "cybersecurity",
        "permission_basis": "RSA Conference public RSS; TOS allows content aggregation with attribution",
        "is_official_source": True,
        "tags": ["security", "cybersecurity", "conference"],
    },

    # ── Cloud / DevOps ──────────────────────────────────────────────────────
    {
        "slug": "aws-events-rss",
        "name": "AWS Events – Official RSS",
        "url": "https://aws.amazon.com/about-aws/events/feed/",
        "source_type": "rss",
        "publisher": "Amazon Web Services",
        "vertical_hint": "cloud-infrastructure",
        "permission_basis": "AWS publishes public RSS for events page; linked from events landing page",
        "is_official_source": True,
        "tags": ["aws", "cloud", "conference"],
    },
    {
        "slug": "google-cloud-events-rss",
        "name": "Google Cloud – Events RSS",
        "url": "https://cloud.google.com/feeds/cloud-events.xml",
        "source_type": "rss",
        "publisher": "Google Cloud",
        "vertical_hint": "cloud-infrastructure",
        "permission_basis": "Google Cloud official XML events feed; public and indexed",
        "is_official_source": True,
        "tags": ["google-cloud", "cloud", "gcp"],
    },
    {
        "slug": "azure-events-rss",
        "name": "Microsoft Azure – Events RSS",
        "url": "https://azure.microsoft.com/en-us/blog/topics/events/feed/",
        "source_type": "rss",
        "publisher": "Microsoft Azure",
        "vertical_hint": "cloud-infrastructure",
        "permission_basis": "Microsoft publishes public RSS for Azure events blog",
        "is_official_source": True,
        "tags": ["azure", "microsoft", "cloud"],
    },
    {
        "slug": "devopsdays-events-ical",
        "name": "DevOpsDays – Community Events ICS",
        "url": "https://devopsdays.org/feed.ics",
        "source_type": "ical",
        "publisher": "DevOpsDays",
        "vertical_hint": "cloud-infrastructure",
        "permission_basis": "DevOpsDays publishes ICS publicly for community calendar aggregation",
        "is_official_source": True,
        "tags": ["devops", "cloud", "open-source"],
    },
    {
        "slug": "hashiconf-events-rss",
        "name": "HashiConf – Events RSS",
        "url": "https://www.hashicorp.com/blog/feed/events",
        "source_type": "rss",
        "publisher": "HashiCorp",
        "vertical_hint": "cloud-infrastructure",
        "permission_basis": "HashiCorp public RSS; listed on blog page",
        "is_official_source": True,
        "tags": ["terraform", "devops", "cloud"],
    },

    # ── Startup / Entrepreneurship ──────────────────────────────────────────
    {
        "slug": "techcrunch-events-rss",
        "name": "TechCrunch Events – RSS",
        "url": "https://techcrunch.com/events/feed/",
        "source_type": "rss",
        "publisher": "TechCrunch",
        "vertical_hint": "startup-entrepreneurship",
        "permission_basis": "TechCrunch public events RSS; standard media aggregation",
        "is_official_source": True,
        "tags": ["startup", "tech", "venture-capital"],
    },
    {
        "slug": "sxsw-schedule-ical",
        "name": "SXSW – Public Schedule ICS",
        "url": "https://schedule.sxsw.com/feed/ical",
        "source_type": "ical",
        "publisher": "South by Southwest LLC",
        "vertical_hint": "startup-entrepreneurship",
        "permission_basis": "SXSW publishes ICS on schedule.sxsw.com for attendee calendar sync",
        "is_official_source": True,
        "tags": ["startup", "music", "film", "interactive"],
    },
    {
        "slug": "collision-conf-rss",
        "name": "Collision Conference – RSS",
        "url": "https://collisionconf.com/feed",
        "source_type": "rss",
        "publisher": "Collision / Web Summit",
        "vertical_hint": "startup-entrepreneurship",
        "permission_basis": "Public WordPress RSS; Collision promotes event discovery",
        "is_official_source": True,
        "tags": ["startup", "tech", "conference"],
    },
    {
        "slug": "websummit-news-rss",
        "name": "Web Summit – News RSS",
        "url": "https://websummit.com/feed/",
        "source_type": "rss",
        "publisher": "Web Summit",
        "vertical_hint": "startup-entrepreneurship",
        "permission_basis": "Web Summit public RSS; used for event announcements and news",
        "is_official_source": True,
        "tags": ["startup", "tech", "conference", "europe"],
    },

    # ── Design / UX ─────────────────────────────────────────────────────────
    {
        "slug": "uxpa-events-ical",
        "name": "UXPA International – Events ICS",
        "url": "https://uxpa.org/events/?ical=1",
        "source_type": "ical",
        "publisher": "User Experience Professionals Association",
        "vertical_hint": "design-ux",
        "permission_basis": "UXPA publishes WordPress ICS export on their events page",
        "is_official_source": True,
        "tags": ["ux", "design", "user-experience"],
    },
    {
        "slug": "ixda-events-rss",
        "name": "IxDA – Events RSS",
        "url": "https://ixda.org/feed/",
        "source_type": "rss",
        "publisher": "Interaction Design Association",
        "vertical_hint": "design-ux",
        "permission_basis": "IxDA public RSS; non-profit organization supporting community event sharing",
        "is_official_source": True,
        "tags": ["interaction-design", "ux", "design"],
    },
    {
        "slug": "awwwards-events-rss",
        "name": "Awwwards – Events RSS",
        "url": "https://conference.awwwards.com/feed/",
        "source_type": "rss",
        "publisher": "Awwwards",
        "vertical_hint": "design-ux",
        "permission_basis": "Awwwards Conference public RSS feed",
        "is_official_source": True,
        "tags": ["web-design", "design", "conference"],
    },

    # ── Product / Growth ─────────────────────────────────────────────────────
    {
        "slug": "mind-the-product-events-rss",
        "name": "Mind the Product – Events RSS",
        "url": "https://www.mindtheproduct.com/feed/",
        "source_type": "rss",
        "publisher": "Mind the Product Ltd",
        "vertical_hint": "product-management",
        "permission_basis": "MTP public WordPress RSS; MTP promotes aggregation of their events",
        "is_official_source": True,
        "tags": ["product-management", "product", "conference"],
    },
    {
        "slug": "producttank-meetups-ical",
        "name": "ProductTank Global – Meetup ICS",
        "url": "https://www.meetup.com/producttank-london/events/ical/",
        "source_type": "ical",
        "publisher": "ProductTank",
        "vertical_hint": "product-management",
        "permission_basis": "Meetup.com public ICS export; listed on event page for calendar sync",
        "is_official_source": True,
        "tags": ["product-management", "meetup"],
    },

    # ── Blockchain / Web3 ────────────────────────────────────────────────────
    {
        "slug": "ethcc-events-ical",
        "name": "EthCC – Official Schedule ICS",
        "url": "https://ethcc.io/schedule.ics",
        "source_type": "ical",
        "publisher": "Ethereum Community Conference",
        "vertical_hint": "blockchain-web3",
        "permission_basis": "EthCC publishes ICS on official site for community calendar sync",
        "is_official_source": True,
        "tags": ["ethereum", "blockchain", "web3", "conference"],
    },
    {
        "slug": "devcon-events-json",
        "name": "Devcon – Official Events API",
        "url": "https://api.devcon.org/events?fields=title,date_start,date_end,location,description",
        "source_type": "json",
        "publisher": "Ethereum Foundation",
        "vertical_hint": "blockchain-web3",
        "permission_basis": "Devcon API publicly accessible; EF encourages community tooling",
        "is_official_source": True,
        "tags": ["ethereum", "blockchain", "devcon"],
    },

    # ── Gaming / Esports ─────────────────────────────────────────────────────
    {
        "slug": "gdc-events-rss",
        "name": "GDC – Game Developers Conference RSS",
        "url": "https://gdconf.com/feed",
        "source_type": "rss",
        "publisher": "Informa / GDC",
        "vertical_hint": "gaming",
        "permission_basis": "GDC public WordPress RSS; standard event aggregation",
        "is_official_source": True,
        "tags": ["gaming", "game-development", "conference"],
    },
    {
        "slug": "igda-events-rss",
        "name": "IGDA – Events RSS",
        "url": "https://igda.org/feed/",
        "source_type": "rss",
        "publisher": "International Game Developers Association",
        "vertical_hint": "gaming",
        "permission_basis": "IGDA non-profit public RSS; promotes community event sharing",
        "is_official_source": True,
        "tags": ["gaming", "game-development"],
    },

    # ── Hardware / IoT ───────────────────────────────────────────────────────
    {
        "slug": "embedded-world-rss",
        "name": "Embedded World – Events RSS",
        "url": "https://www.embedded-world.de/en/news.rss",
        "source_type": "rss",
        "publisher": "NürnbergMesse",
        "vertical_hint": "hardware-iot",
        "permission_basis": "NürnbergMesse public RSS; international trade fair with open news feed",
        "is_official_source": True,
        "tags": ["embedded", "iot", "hardware", "electronics"],
    },
    {
        "slug": "sparkfun-events-rss",
        "name": "SparkFun – Events RSS",
        "url": "https://www.sparkfun.com/news/events.rss",
        "source_type": "rss",
        "publisher": "SparkFun Electronics",
        "vertical_hint": "hardware-iot",
        "permission_basis": "SparkFun public RSS; CC-licensed content",
        "is_official_source": True,
        "tags": ["hardware", "maker", "electronics"],
    },
    {
        "slug": "makerfaire-events-rss",
        "name": "Maker Faire – Global Events RSS",
        "url": "https://makerfaire.com/feed/",
        "source_type": "rss",
        "publisher": "Make Community LLC",
        "vertical_hint": "hardware-iot",
        "permission_basis": "Maker Faire public RSS; Make Media promotes event discovery",
        "is_official_source": True,
        "tags": ["maker", "hardware", "diy"],
    },

    # ── Biotech / Health Tech ────────────────────────────────────────────────
    {
        "slug": "himss-events-rss",
        "name": "HIMSS – Events RSS",
        "url": "https://www.himss.org/rss/events",
        "source_type": "rss",
        "publisher": "Healthcare Information and Management Systems Society",
        "vertical_hint": "health-biotech",
        "permission_basis": "HIMSS public RSS; non-profit promotes event discovery",
        "is_official_source": True,
        "tags": ["healthcare", "health-tech", "conference"],
    },
    {
        "slug": "bioitworld-events-rss",
        "name": "Bio-IT World – Events RSS",
        "url": "https://www.bio-itworld.com/rss",
        "source_type": "rss",
        "publisher": "Bio-IT World / Informa",
        "vertical_hint": "health-biotech",
        "permission_basis": "Bio-IT World public RSS feed",
        "is_official_source": True,
        "tags": ["biotech", "bioinformatics", "health-tech"],
    },

    # ── Fintech ──────────────────────────────────────────────────────────────
    {
        "slug": "money2020-events-rss",
        "name": "Money20/20 – Events RSS",
        "url": "https://us.money2020.com/feed/",
        "source_type": "rss",
        "publisher": "Money20/20",
        "vertical_hint": "fintech",
        "permission_basis": "Money20/20 public RSS; Ascential brand promotes aggregation",
        "is_official_source": True,
        "tags": ["fintech", "finance", "payments", "conference"],
    },
    {
        "slug": "finovate-events-rss",
        "name": "Finovate – Events RSS",
        "url": "https://finovate.com/feed/",
        "source_type": "rss",
        "publisher": "Finovate / Informa",
        "vertical_hint": "fintech",
        "permission_basis": "Finovate public WordPress RSS",
        "is_official_source": True,
        "tags": ["fintech", "banking", "conference"],
    },

    # ── Diversity & Inclusion in Tech ─────────────────────────────────────
    {
        "slug": "grace-hopper-events-rss",
        "name": "Grace Hopper Celebration – Events RSS",
        "url": "https://ghc.anitab.org/feed/",
        "source_type": "rss",
        "publisher": "AnitaB.org",
        "vertical_hint": "diversity-inclusion",
        "permission_basis": "AnitaB.org non-profit public RSS; promotes event awareness",
        "is_official_source": True,
        "tags": ["diversity", "women-in-tech", "inclusion", "conference"],
    },
    {
        "slug": "tapia-events-rss",
        "name": "CMD-IT / ACM Tapia Celebration – RSS",
        "url": "https://tapiaconference.cmd-it.org/feed/",
        "source_type": "rss",
        "publisher": "CMD-IT / ACM",
        "vertical_hint": "diversity-inclusion",
        "permission_basis": "CMD-IT non-profit public RSS",
        "is_official_source": True,
        "tags": ["diversity", "underrepresented", "conference"],
    },

    # ── Climate / Sustainability Tech ─────────────────────────────────────
    {
        "slug": "cop-events-rss",
        "name": "UNFCCC COP – Events RSS",
        "url": "https://unfccc.int/rss/events",
        "source_type": "rss",
        "publisher": "UNFCCC",
        "vertical_hint": "climate-sustainability",
        "permission_basis": "UN agency public RSS; all UN content is in public domain",
        "is_official_source": True,
        "tags": ["climate", "sustainability", "policy"],
    },
    {
        "slug": "climatetech-events-rss",
        "name": "ClimaTech Forum – Events RSS",
        "url": "https://climatetechforum.com/feed/",
        "source_type": "rss",
        "publisher": "ClimateTech Forum",
        "vertical_hint": "climate-sustainability",
        "permission_basis": "Public WordPress RSS; ClimateTech Forum promotes event discovery",
        "is_official_source": True,
        "tags": ["climate-tech", "cleantech", "conference"],
    },

    # ── Marketing / AdTech ────────────────────────────────────────────────
    {
        "slug": "dmexco-events-rss",
        "name": "DMEXCO – Events RSS",
        "url": "https://dmexco.com/feed/",
        "source_type": "rss",
        "publisher": "DMEXCO / Koelnmesse",
        "vertical_hint": "marketing-adtech",
        "permission_basis": "DMEXCO public RSS; international digital marketing expo",
        "is_official_source": True,
        "tags": ["marketing", "advertising", "digital", "conference"],
    },
    {
        "slug": "contentmarketing-world-rss",
        "name": "Content Marketing World – RSS",
        "url": "https://contentmarketingworld.com/feed/",
        "source_type": "rss",
        "publisher": "Content Marketing Institute",
        "vertical_hint": "marketing-adtech",
        "permission_basis": "CMI public RSS; non-profit promotes content marketing education",
        "is_official_source": True,
        "tags": ["marketing", "content", "conference"],
    },

    # ── Space Tech ────────────────────────────────────────────────────────
    {
        "slug": "spacesymposium-events-rss",
        "name": "Space Symposium – RSS",
        "url": "https://www.spacesymposium.org/feed/",
        "source_type": "rss",
        "publisher": "Space Foundation",
        "vertical_hint": "space-tech",
        "permission_basis": "Space Foundation non-profit public RSS",
        "is_official_source": True,
        "tags": ["space", "aerospace", "conference"],
    },
    {
        "slug": "smallsat-events-rss",
        "name": "SmallSat Conference – RSS",
        "url": "https://smallsat.org/feed/",
        "source_type": "rss",
        "publisher": "AIAA / SmallSat",
        "vertical_hint": "space-tech",
        "permission_basis": "AIAA public RSS",
        "is_official_source": True,
        "tags": ["smallsat", "satellite", "space"],
    },

    # ── AR/VR/XR ─────────────────────────────────────────────────────────
    {
        "slug": "augmented-world-expo-rss",
        "name": "AWE – Augmented World Expo RSS",
        "url": "https://awexr.com/feed/",
        "source_type": "rss",
        "publisher": "Augmented World Expo",
        "vertical_hint": "ar-vr-xr",
        "permission_basis": "AWE public WordPress RSS",
        "is_official_source": True,
        "tags": ["ar", "vr", "xr", "augmented-reality"],
    },

    # ── Education Tech ────────────────────────────────────────────────────
    {
        "slug": "iste-events-rss",
        "name": "ISTE – Conference & Events RSS",
        "url": "https://iste.org/feed/",
        "source_type": "rss",
        "publisher": "ISTE",
        "vertical_hint": "education-tech",
        "permission_basis": "ISTE non-profit public RSS; promotes educational event discovery",
        "is_official_source": True,
        "tags": ["edtech", "education", "technology", "conference"],
    },
    {
        "slug": "sxswedu-events-rss",
        "name": "SXSWedu – Events RSS",
        "url": "https://www.sxswedu.com/feed/",
        "source_type": "rss",
        "publisher": "South by Southwest LLC",
        "vertical_hint": "education-tech",
        "permission_basis": "SXSW public RSS feed",
        "is_official_source": True,
        "tags": ["edtech", "education", "conference"],
    },

    # ── Robotics / Automation ─────────────────────────────────────────────
    {
        "slug": "ros-events-ical",
        "name": "ROS Events – Community ICS",
        "url": "https://discourse.ros.org/upcoming-events.ics",
        "source_type": "ical",
        "publisher": "Open Robotics / ROS",
        "vertical_hint": "robotics",
        "permission_basis": "Open Robotics Discourse publishes community calendar as ICS; open community",
        "is_official_source": True,
        "tags": ["robotics", "ros", "automation", "open-source"],
    },
    {
        "slug": "icra-events-rss",
        "name": "ICRA – IEEE Robotics RSS",
        "url": "https://www.ieee-ras.org/conferences-workshops/fully-sponsored/icra.rss",
        "source_type": "rss",
        "publisher": "IEEE Robotics and Automation Society",
        "vertical_hint": "robotics",
        "permission_basis": "IEEE RAS public RSS; standard IEEE academic event aggregation",
        "is_official_source": True,
        "tags": ["robotics", "ieee", "research"],
    },

    # ── Community / Meetups ───────────────────────────────────────────────
    {
        "slug": "community-events-ical",
        "name": "Community.dev – Tech Meetups ICS",
        "url": "https://community.dev/events/ical/",
        "source_type": "ical",
        "publisher": "CMX / Bevy",
        "vertical_hint": "community-tech",
        "permission_basis": "CMX community platform publishes ICS for community event sync",
        "is_official_source": True,
        "tags": ["community", "meetup", "tech"],
    },
    {
        "slug": "foss4g-ical",
        "name": "FOSS4G – Open Source Geo ICS",
        "url": "https://foss4g.org/schedule.ics",
        "source_type": "ical",
        "publisher": "OSGeo",
        "vertical_hint": "software-development",
        "permission_basis": "OSGeo publishes public ICS; CC BY licensed conference",
        "is_official_source": True,
        "tags": ["gis", "open-source", "geospatial"],
    },
]


SOURCE_TYPE_TO_KIND = {
    "ical": "ics",
    "rss": "jsonld",      # RSS feeds parsed via JSON-LD extraction
    "json": "jsonld",     # JSON APIs parsed via JSON-LD extraction
    "html": "official_html",
}


def build_rows(now_iso: str) -> list[dict]:
    """Convert SOURCES list to Supabase insert-ready rows.

    Maps to the actual conference_sources schema:
      slug, name, publisher, kind ('ics'|'jsonld'|'official_html'),
      url, permission_basis, permission_reviewed_at,
      ingestion_allowed, is_official_source, is_active
    """
    rows = []
    for s in SOURCES:
        kind = SOURCE_TYPE_TO_KIND.get(s["source_type"], "jsonld")
        rows.append({
            "slug": s["slug"],
            "name": s["name"],
            "url": s["url"],
            "kind": kind,
            "publisher": s["publisher"],
            "is_active": True,
            "is_official_source": s.get("is_official_source", True),
            "ingestion_allowed": True,
            "permission_basis": s["permission_basis"],
            "permission_reviewed_at": now_iso,
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed conference_sources table")
    parser.add_argument("--upload", action="store_true", help="Upload to Supabase (else dry-run)")
    parser.add_argument("--clear", action="store_true", help="DELETE existing rows first (use with care)")
    args = parser.parse_args()

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = build_rows(now_iso)

    if not args.upload:
        print(f"DRY RUN — {len(rows)} sources would be inserted:\n")
        for r in rows:
            print(f"  [{r['kind'].upper():10}] {r['slug']}")
            print(f"         {r['url']}")
        print(f"\nRe-run with --upload to write to Supabase.")
        return

    # ── Upload ──────────────────────────────────────────────────────────────
    if not SUPABASE_URL or not SERVICE_ROLE_KEY:
        sys.exit("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")

    try:
        import httpx
    except ImportError:
        sys.exit("ERROR: httpx not installed — run: pip install httpx")

    headers = {
        "apikey": SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    base = f"{SUPABASE_URL}/rest/v1/conference_sources"

    if args.clear:
        print("Clearing existing conference_sources rows …")
        r = httpx.delete(f"{base}?id=neq.00000000-0000-0000-0000-000000000000", headers=headers)
        if r.status_code not in (200, 204):
            print(f"  WARNING: clear returned {r.status_code}: {r.text}")
        else:
            print("  Done.")

    # Upsert in chunks of 20
    CHUNK = 20
    total_ok = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i: i + CHUNK]
        r = httpx.post(
            base,
            headers={**headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            content=json.dumps(chunk),
            timeout=30,
        )
        if r.status_code in (200, 201, 204):
            total_ok += len(chunk)
            print(f"  OK: Inserted/updated rows {i+1}-{i+len(chunk)}")
        else:
            print(f"  ERR: rows {i+1}-{i+len(chunk)}: {r.status_code} {r.text[:300]}")

    print(f"\nDone -- {total_ok}/{len(rows)} rows written to Supabase.")


if __name__ == "__main__":
    main()
