from flask import Flask, render_template, request, jsonify
from ddgs import DDGS
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import requests
import re
import json
import ipaddress
import os
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import zip_longest

import aircraft_identity as ident

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# Wikimedia, OpenAlex and Crossref ask API clients to identify themselves with
# a contact. Set AEROSCRAPING_CONTACT (an email or URL) in the host's
# environment, e.g. Render > Environment, instead of hard-coding it here.
_CONTACT = os.environ.get('AEROSCRAPING_CONTACT', '').strip()
SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Aeroscraping/3.0 (aerospace research discovery'
                  + (f'; contact: {_CONTACT}' if _CONTACT else '') + ')'
})
TIMEOUT = 25
ident.SESSION = SESSION  # Wikimedia requests use the same identified client

# Shops, print-on-demand and pin boards: they rarely describe the aircraft and
# their "drawings" are posters, so they are dropped from every result type.
LOW_QUALITY_DOMAINS = (
    'pinterest.', 'pixels.com', 'fineartamerica.', 'redbubble.', 'etsy.',
    'ebay.', 'amazon.', 'aliexpress.', 'alibaba.', 'walmart.', 'temu.',
    'trade-a-plane.', 'controller.com', 'aircraftforsale', 'globalplanesearch',
    'chegg.', 'coursehero.', 'scribd.', 'dreamstime.', 'shutterstock.',
    'alamy.', 'gettyimages.', 'istockphoto.', 'depositphotos.', '123rf.',
    'vecteezy.', 'freepik.', 'grokipedia.', 'quora.', 'tiktok.', 'facebook.',
    'instagram.', 'youtube.', 'x.com', 'twitter.', 'quizlet.',
    'allwiringsketch.', 'circuitblaze.', 'eleccircs.', 'wirecrafted.',
    'wiringall.', 'wiringdiagram', 'schematron.'
)

# Reference, government, manufacturer and established aviation publishers rank
# first in the web tab.
TRUSTED_DOMAINS = (
    'wikipedia.org', 'nasa.gov', 'faa.gov', 'easa.europa.eu', 'af.mil',
    'navy.mil', 'army.mil', 'defense.gov', 'airandspace.si.edu', 'si.edu',
    'britannica.com', 'boeing.com', 'airbus.com', 'lockheedmartin.com',
    'northropgrumman.com', 'dassault-aviation.com', 'saab.com', 'embraer.com',
    'txtav.com', 'cessna.', 'gulfstream.com', 'bombardier.com', 'ga-asi.com',
    'baesystems.com', 'leonardo.com', 'flightglobal.com', 'aerosociety.com',
    'aiaa.org', 'airandspaceforces.com', 'aviationweek.com', 'janes.com',
    'globalsecurity.org', 'militaryfactory.com', 'aopa.org', 'skybrary.aero',
    'f-16.net', 'nationalmuseum.af.mil', 'iwm.org.uk', 'rafmuseum.org.uk'
)


# Classified-ad and price-listing pages.
LISTING_TITLE = re.compile(r'\bfor sale\b|\bprice[sd]?\b.*\bavailable\b|\bbuy now\b', re.I)

# Image-search "drawings" from SEO content farms ("wiring diagram" sites) use
# generic words like diagram/schematic/guide. Image-search candidates must
# carry a real drawing term instead.
STRONG_DRAWING_TERMS = re.compile(
    r'three[\s-]?view|3[\s-]?view|blueprint|line drawing|line art|'
    r'general arrangement|orthographic|planform|cross[\s-]?section|'
    r'cutaway|cut-away|airfoil|aerofoil|2d drawing|scale drawing|'
    r'technical drawing|engineering drawing|side view|top view',
    re.I
)
SEO_DRAWING_TITLE = re.compile(
    r'wiring|everything you need|a visual guide|understanding|exploring|'
    r'complete .* guide|must-have|how to|explained|parts diagram',
    re.I
)


def domain_of(url):
    try:
        return (urlparse(url).hostname or '').lower()
    except ValueError:
        return ''


def _host_hits(host, entry):
    """Match on whole domain labels so 'x.com' does not match 'dropbox.com'."""
    labels = '.' + host + '.'
    if entry.endswith('.'):           # brand prefix, e.g. 'pinterest.'
        return '.' + entry in labels
    return labels.endswith('.' + entry + '.') or ('.' + entry) in labels


def is_low_quality(url):
    host = domain_of(url)
    return any(_host_hits(host, bad) for bad in LOW_QUALITY_DOMAINS)


def is_trusted(url):
    host = domain_of(url)
    return any(_host_hits(host, good) for good in TRUSTED_DOMAINS)


# Subject matching lives in aircraft_identity.SubjectMatcher: it matches the
# identified aircraft's names/designations ("F-16", "F16", "F-16C") without
# matching unrelated text that merely contains "16".


def has_drawing_evidence(title, description, filename=''):
    text = f'{title} {description} {filename}'.lower()
    drawing_markers = (
        'three view', 'three-view', '3-view', 'orthographic',
        'orthographic projection', 'blueprint', 'technical drawing',
        'engineering drawing', 'aircraft drawing', 'line drawing',
        'line art', 'outline drawing', 'airfoil', 'aerofoil',
        'airfoil profile', 'airfoil section',
        'cross-section', 'cross section', 'section view',
        'schematic', 'schematics', 'diagram', 'structural layout',
        'fuselage frame', 'general arrangement',
        'side view', 'top view', 'front view', 'rear view',
        'wing planform', 'planform',
        'plan view', 'elevation'
    )
    return any(marker in text for marker in drawing_markers)


def get_json(url, params=None, timeout=TIMEOUT):
    # Retries briefly on HTTP 429/5xx (Wikimedia and NTRS rate-limit bursts).
    return ident._get(url, params=params, timeout=timeout)


def ddg_text(query, region, safe, limit, backend="duckduckgo"):
    """Query one supported DDGS text backend; caller merges provider results."""
    # ddgs 9.x bug: with region "wt-wt" the google/bing backends also query
    # https://wt.wikipedia.org (which does not exist) and the whole call fails.
    if region == "wt-wt":
        region = "us-en"
    try:
        with DDGS() as ddgs:
            rows = list(ddgs.text(
                query, region=region, safesearch=safe,
                max_results=limit, backend=backend
            ))
        return rows, None
    except Exception as exc:
        return [], f"{backend}: {exc}"


def ddg_images(query, region, safe, limit):
    """DDGS image search has no backend argument."""
    try:
        with DDGS() as ddgs:
            rows = list(ddgs.images(
                query, region=region, safesearch=safe, max_results=limit
            ))
        return rows, None
    except Exception as exc:
        return [], f"Image search: {exc}"


def dedupe_web(items, matcher, limit):
    """
    Keep only pages that name the requested aircraft, drop shops/pin boards,
    and rank reference/government/manufacturer sources first.
    """
    seen, kept = set(), []
    for order, item in enumerate(items):
        title = str(item.get("title") or "").strip()
        url = str(item.get("href") or item.get("url") or "").strip()
        body = str(item.get("body") or item.get("snippet") or "").strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        if is_low_quality(url) or LISTING_TITLE.search(title):
            continue
        host = domain_of(url)
        if host.endswith('wikipedia.org') and not host.startswith(('en.', 'en.m.')):
            continue  # other-language copies of the same article
        strength = matcher.strength(title, f"{body} {url}")
        if strength == 0:
            continue
        key = url.split("#")[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        score = strength * 2 + (3 if is_trusted(url) else 0)
        kept.append((-score, order, {**item, "title": title, "href": url, "body": body}))
    kept.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in kept[:limit]]


# DuckDuckGo's and Brave's text backends return "No results found" for most
# requests from servers, so only the backends that answer are queried.
WEB_BACKENDS = ("google", "bing", "yahoo")


def search_web(search_name, matcher, region, safe, limit):
    """General web discovery for one identified aircraft or topic."""
    variants = [
        search_name, f'"{search_name}" aircraft',
        f'"{search_name}" specifications', f'"{search_name}" development history',
        f'"{search_name}" design technical report'
    ]
    per_job = max(20, min(50, (limit + len(variants) - 1) // len(variants)))
    jobs = [(term, backend) for term in variants for backend in WEB_BACKENDS]
    collected, errors = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(ddg_text, term, region, safe, per_job, backend): (term, backend)
            for term, backend in jobs
        }
        # Keep results in job order so the first variant's ranking leads.
        by_job = {}
        for future in as_completed(futures):
            term, backend = futures[future]
            try:
                found, error = future.result()
                by_job[(term, backend)] = found
                if error and 'No results found' not in error:
                    errors.append(f"{backend} ({term}): {error}")
            except Exception as exc:
                errors.append(f"{backend} ({term}): {exc}")
    for job in jobs:
        collected.extend(by_job.get(job, []))
    return dedupe_web(collected, matcher, limit), errors


def search_nasa_images(search_name, matcher, limit):
    """
    Search NASA's public Image and Video Library; no API key required.
    The aircraft must be named in the image *title* — NASA descriptions often
    mention an aircraft only in passing (e.g. a chase plane at a launch).
    """
    results, errors, seen = [], [], set()
    queries = [f'"{search_name}"', f'{search_name} aircraft']
    per_query = max(30, min(100, (limit + len(queries) - 1) // len(queries)))
    for term in queries:
        try:
            data = get_json(
                'https://images-api.nasa.gov/search',
                {
                    'q': term,
                    'media_type': 'image',
                    'page_size': per_query
                }
            )
            items = (data.get('collection') or {}).get('items') or []
            for item in items:
                meta_list = item.get('data') or [{}]
                meta = meta_list[0]
                title = meta.get('title') or 'NASA aerospace image'
                description = meta.get('description') or ''
                nasa_id = meta.get('nasa_id') or ''
                links = item.get('links') or []
                image_url = next(
                    (link.get('href') for link in links
                     if link.get('rel') == 'preview' and link.get('href')),
                    None
                )
                if not image_url:
                    continue
                if not matcher.matches(title):
                    continue
                key = nasa_id or image_url
                if key in seen:
                    continue
                seen.add(key)
                # item['href'] is the raw collection.json manifest, not a page.
                page_url = (f'https://images.nasa.gov/details/{nasa_id}'
                            if nasa_id else image_url)
                results.append({
                    'title': title,
                    'image': image_url,
                    'thumbnail': image_url,
                    'url': page_url,
                    'href': page_url,
                    'source': 'NASA Image and Video Library',
                    'description': BeautifulSoup(
                        description, 'html.parser'
                    ).get_text(' ', strip=True)[:1200],
                    'date': meta.get('date_created', ''),
                    'nasa_id': nasa_id,
                    'center': meta.get('center', 'NASA')
                })
                if len(results) >= limit:
                    return results[:limit], errors
        except Exception as exc:
            errors.append(f'NASA Images ({term}): {exc}')
    return results[:limit], errors


def search_images(search_name, matcher, identity, region, safe, limit):
    """
    Aircraft photographs, most reliable first:
      1. photos placed in the aircraft's Wikipedia article by its editors,
      2. the aircraft's own Wikimedia Commons category,
      3. NASA images whose title names the aircraft,
      4. general image search whose title names the aircraft.
    """
    errors, curated = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        nasa_future = pool.submit(search_nasa_images, search_name, matcher, limit)
        ddg_futures = [
            pool.submit(ddg_images, term, region, safe, 60)
            for term in (f'"{search_name}"', f'{search_name} aircraft')
        ]
        if identity:
            article_future = pool.submit(ident.article_photos, identity['title'])
            category_future = pool.submit(ident.category_photos, identity.get('qid'))
            # Articles also show rivals/predecessors (an AH-56 in the A-10
            # article), so article photos must name the aircraft in their
            # caption. Everything in the aircraft's own Commons category is
            # that aircraft.
            curated = [p for p in article_future.result()
                       if matcher.matches(p['title'], p.get('description', ''))]
            curated += category_future.result()
        nasa_results, nasa_errors = nasa_future.result()
        errors.extend(nasa_errors)
        collected = []
        for future in ddg_futures:
            rows, error = future.result()
            collected.extend(rows)
            if error:
                errors.append(error)

    output, seen = [], set()
    for item in curated + nasa_results:
        key = str(item.get("image") or item.get("url") or "").split("?")[0].lower()
        if key and key not in seen:
            seen.add(key)
            output.append(item)
    for item in collected:
        title = str(item.get("title") or "").strip()
        image_url = item.get("image") or item.get("thumbnail") or item.get("url")
        page_url = item.get("url") or image_url
        if not image_url or not title:
            continue
        if is_low_quality(page_url) or is_low_quality(image_url):
            continue
        # Title only: page URLs/sources name the site, not the pictured aircraft.
        if not matcher.matches(title):
            continue
        key = str(image_url).split("?")[0].lower()
        if key in seen:
            continue
        seen.add(key)
        output.append({
            "title": title, "image": image_url,
            "thumbnail": item.get("thumbnail") or image_url,
            "url": page_url, "href": page_url,
            "source": item.get("source") or "Image search"
        })
        if len(output) >= limit:
            break
    return output[:limit], errors



ASE_REPORT_TOPICS = [
    ("Aircraft configuration / three-view", "three view orthographic general arrangement dimensions"),
    ("Wing planform / sweep", "wing planform sweep geometry wing layout"),
    ("Empennage configuration", "empennage tailplane horizontal stabilizer vertical stabilizer"),
    ("Fuselage geometry / sections", "fuselage cross section frames stringers"),
    ("Wing structure / spars and ribs", "wing internal structure spar rib structural layout"),
    ("Landing gear arrangement", "landing gear layout gear arrangement technical diagram"),
    ("Propulsion / engine installation", "engine installation nacelle inlet exhaust propulsion layout"),
    ("Airfoil / high-lift devices", "airfoil profile section flap slat high lift device"),
    ("Flight controls / actuation", "aileron elevator rudder spoiler control surface actuation"),
    ("Avionics / cockpit", "cockpit instrument panel avionics flight deck layout")
]


def technical_search_terms(subject):
    """Automatically expand one aircraft query across all ASE 111 topics."""
    return [(label, f"{subject} {terms}") for label, terms in ASE_REPORT_TOPICS]


def search_ddg_technical_images(query, matcher, limit, errors):
    """
    Search drawing-specific phrases for the exact requested aircraft.
    Search engines often return valid drawings whose filenames/captions omit
    the aircraft name, so do not reject them solely on title-token matching.
    """
    output, seen = [], set()
    topic_queries = [
        ("Three-view / orthographic", f'"{query}" aircraft three view drawing'),
        ("General arrangement", f'"{query}" aircraft general arrangement drawing'),
        ("Wing planform", f'"{query}" aircraft wing planform drawing'),
        ("Airfoil / aerofoil", f'"{query}" {query} airfoil aerofoil section'),
        ("Structural drawing", f'"{query}" aircraft wing spar rib fuselage structure drawing'),
        ("Empennage / controls", f'"{query}" aircraft tail empennage control surface drawing'),
        ("Landing gear", f'"{query}" aircraft landing gear technical drawing'),
        ("Propulsion installation", f'"{query}" aircraft engine installation nacelle drawing'),
        ("Cockpit / avionics", f'"{query}" aircraft cockpit avionics layout diagram')
    ]
    per_query = max(20, min(60, (limit + len(topic_queries) - 1) // len(topic_queries)))
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(ddg_images, phrase, "wt-wt", "moderate", per_query): (label, phrase)
            for label, phrase in topic_queries
        }
        for future in as_completed(futures):
            label, phrase = futures[future]
            try:
                items, error = future.result()
                if error:
                    errors.append(f"{label}: {error}")
                    continue
                for item in items:
                    title = str(item.get("title") or "").strip()
                    image_url = item.get("image") or item.get("thumbnail") or item.get("url")
                    page_url = item.get("url") or image_url
                    if not image_url or not title:
                        continue
                    if is_low_quality(page_url) or is_low_quality(image_url):
                        continue
                    evidence = f"{title} {page_url or ''} {item.get('source') or ''}"
                    if not matcher.matches(title):
                        continue
                    if SEO_DRAWING_TITLE.search(title):
                        continue
                    if not STRONG_DRAWING_TERMS.search(evidence):
                        continue
                    key = str(image_url).split("?")[0].lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    output.append({
                        "title": title or f"{query} — {label} search candidate",
                        "href": page_url or image_url,
                        "url": page_url or image_url,
                        "image": image_url,
                        "thumbnail": item.get("thumbnail") or image_url,
                        "body": (
                            f"Returned for the exact aircraft query: {phrase}. "
                            "This is a drawing-search candidate; open the source and confirm "
                            "aircraft variant, view, dimensions, and drawing provenance."
                        ),
                        "description": f"Aircraft-specific drawing search candidate: {label}.",
                        "drawing_type": label,
                        "category": "Technical drawing candidate — verify source",
                        "source": item.get("source") or "Image search",
                        "license": "Check original source and reuse terms"
                    })
                    if len(output) >= limit:
                        return output[:limit]
            except Exception as exc:
                errors.append(f"{label}: {exc}")
    return output[:limit]


def search_commons_drawings(query, matcher, limit):
    """
    Search Wikimedia Commons for actual aircraft technical illustrations.
    Subject-only and topic-specific searches prevent over-constraining captions.
    """
    results, errors, seen = [], [], set()
    # Four grouped OR-queries cover every ASE 111 report topic. (Thirteen
    # separate queries per search got the app rate-limited by Commons.)
    subject = f'"{query}"'
    search_jobs = [
        ("Aircraft configuration / three-view",
         f'{subject} (3-view OR three-view OR orthographic OR "line drawing" OR "general arrangement" OR silhouette)'),
        ("Structure / cutaway",
         f'{subject} (cutaway OR blueprint OR schematic OR diagram OR "cross section" OR spar OR rib)'),
        ("Wing / airfoil / empennage",
         f'{subject} (planform OR airfoil OR aerofoil OR empennage OR tailplane OR flap OR slat) (drawing OR diagram OR profile)'),
        ("Systems / cockpit / landing gear",
         f'{subject} (cockpit OR "landing gear" OR engine OR nacelle OR "flight control") (diagram OR drawing OR layout OR schematic)'),
    ]
    per_query = 50
    seen_titles = set()

    for label, search_term in search_jobs:
        try:
            data = get_json(
                "https://commons.wikimedia.org/w/api.php",
                {
                    "action": "query",
                    "format": "json",
                    "formatversion": 2,
                    "generator": "search",
                    "gsrsearch": search_term,
                    "gsrnamespace": 6,
                    "gsrlimit": per_query,
                    "prop": "imageinfo",
                    "iiprop": "url|mime|extmetadata",
                    "iiextmetadatafilter": "ImageDescription|LicenseShortName",
                    "iiurlwidth": 1400
                }
            )
            pages = sorted((data.get("query") or {}).get("pages") or [],
                           key=lambda p: p.get("index", 999))
            for page in pages:
                raw_title = page.get("title", "").replace("File:", "")
                info = (page.get("imageinfo") or [{}])[0]
                mime = (info.get("mime") or "").lower()
                extension = raw_title.rsplit(".", 1)[-1].lower() if "." in raw_title else ""

                if mime and not mime.startswith("image/"):
                    continue
                if extension in {"pdf", "djvu", "doc", "docx", "ppt", "pptx"}:
                    continue

                metadata = info.get("extmetadata") or {}
                description_html = metadata.get("ImageDescription", {}).get("value", "")
                description = BeautifulSoup(
                    description_html, "html.parser"
                ).get_text(" ", strip=True)

                page_url = info.get("descriptionurl") or (
                    "https://commons.wikimedia.org/wiki/" +
                    page.get("title", "").replace(" ", "_")
                )
                preview = info.get("thumburl") or info.get("url")
                if not preview:
                    continue

                # Match the requested aircraft, but accept common drawing
                # captions such as "side/top/front/section" and "geometry".
                if not matcher.matches(raw_title, description):
                    continue
                if not has_drawing_evidence(raw_title, description):
                    continue

                key = page_url.split("#")[0].lower()
                # Same drawing uploaded as .png/.svg/.gif or with archive IDs.
                title_key = re.sub(r'\.(png|svg|gif|jpe?g|tiff?|webp)$', '', raw_title.lower())
                title_key = re.sub(r'[^a-z0-9]+', '', title_key)
                if key in seen or title_key in seen_titles:
                    continue
                seen.add(key)
                seen_titles.add(title_key)

                results.append({
                    "title": raw_title,
                    "href": page_url,
                    "url": page_url,
                    "image": preview,
                    "thumbnail": preview,
                    "body": description[:1800] or
                            f"Aircraft-specific technical illustration found under: {label}.",
                    "description": description[:1800],
                    "drawing_type": classify_drawing(raw_title + " " + description),
                    "category": "Technical drawing / engineering reference",
                    "source": "Wikimedia Commons",
                    "license": metadata.get("LicenseShortName", {}).get("value", "Check source page")
                })
                if len(results) >= limit:
                    return results[:limit], errors
        except Exception as exc:
            errors.append(f"Wikimedia Commons ({label}): {exc}")

    return results[:limit], errors


def search_ntrs_drawings(query, matcher, limit):
    """Find NASA-hosted engineering drawings and general-arrangement documents."""
    results, errors, seen = [], [], set()
    terms = [
        f'"{query}" aircraft general arrangement drawing',
        f'"{query}" aircraft three view drawing',
        f'"{query}" aircraft wing planform drawing',
        f'"{query}" aircraft structural drawing airfoil'
    ]
    for term in terms:
        try:
            data = get_json(
                "https://ntrs.nasa.gov/api/citations/search",
                {"q": term, "page.size": min(50, limit)},
                timeout=35
            )
            for record in data.get("results", []):
                title = str(record.get("title") or "").strip()
                abstract = str(record.get("abstract") or record.get("description") or "")
                rid = record.get("id")
                evidence = f"{title} {abstract}"
                if not title or not rid:
                    continue
                if not matcher.matches(title, abstract):
                    continue
                if not has_drawing_evidence(title, abstract):
                    continue
                href = f"https://ntrs.nasa.gov/citations/{rid}"
                if href in seen:
                    continue
                seen.add(href)
                results.append({
                    "title": title,
                    "href": href,
                    "url": href,
                    "body": BeautifulSoup(abstract, "html.parser").get_text(" ", strip=True)[:1800],
                    "drawing_type": classify_drawing(evidence),
                    "category": "NASA technical drawing / report",
                    "source": "NASA Technical Reports Server",
                    "license": "Check NASA record and document rights"
                })
                if len(results) >= limit:
                    return results[:limit], errors
        except Exception as exc:
            errors.append(f"NASA drawing search: {exc}")
    return results[:limit], errors


def search_technical_drawings(query, matcher, limit):
    """
    Single-input drawing aggregator: automatically searches all assignment topics
    through Wikimedia Commons, NASA NTRS and image search, then merges verified
    candidates. The three sources run in parallel.
    """
    errors = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        commons_future = pool.submit(search_commons_drawings, query, matcher, limit)
        nasa_future = pool.submit(search_ntrs_drawings, query, matcher, limit)
        image_future = pool.submit(search_ddg_technical_images, query, matcher, limit, errors)
        commons_results, commons_errors = commons_future.result()
        nasa_results, nasa_errors = nasa_future.result()
        image_results = image_future.result()
    errors.extend(commons_errors + nasa_errors)
    # Drawings named after the aircraft first; ones that only mention it in
    # the caption (e.g. a Tu-144 "compared with Concorde") after them.
    commons_results.sort(key=lambda d: -matcher.strength(d['title'], d.get('body', '')))

    merged, seen = [], set()
    # Real Commons line drawings first (they have previews), then NASA
    # drawing reports, then general image-search candidates.
    for item in commons_results + nasa_results + image_results:
        key = (item.get("href") or item.get("image") or "").split("#")[0].lower()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break

    return merged, errors

def classify_drawing(text):
    text = text.lower()
    labels = [
        ('Three-view / orthographic', ('three view', 'three-view', 'orthographic', 'general arrangement', 'side view', 'top view', 'front view', 'plan view', 'geometry')),
        ('Airfoil profile / section', ('airfoil', 'aerofoil', 'naca')),
        ('Wing planform / geometry', ('planform', 'wing geometry')),
        ('Wing internal structure', ('spar', 'rib', 'wing structure')),
        ('Fuselage cross-section', ('fuselage', 'cross section', 'cross-section', 'frame')),
        ('Empennage / control surfaces', ('empennage', 'rudder', 'elevator', 'tailplane')),
        ('Aircraft technical diagram', ('diagram', 'schematic', 'blueprint', 'drawing'))
    ]
    for label, terms in labels:
        if any(term in text for term in terms):
            return label
    return 'Aircraft engineering reference'


def search_ntrs(query, limit):
    """NASA Technical Reports Server: primary-source engineering reports."""
    results = []
    try:
        data = get_json(
            'https://ntrs.nasa.gov/api/citations/search',
            {'q': f'"{query}"', 'page.size': min(100, limit)},
            timeout=35
        )
        for record in data.get('results', []):
            title = record.get('title') or ''
            abstract = record.get('abstract') or record.get('description') or ''
            record_id = record.get('id')
            if not title or not record_id:
                continue
            href = f'https://ntrs.nasa.gov/citations/{record_id}'
            results.append({
                'title': title,
                'href': href,
                'body': BeautifulSoup(
                    str(abstract), 'html.parser'
                ).get_text(' ', strip=True)[:1800],
                'category': 'NASA Technical Report',
                'authors': ', '.join(record.get('authors') or []),
                'year': record.get('publicationDate') or record.get('stiType'),
                'source': 'NASA Technical Reports Server',
                'doi': ''
            })
            if len(results) >= limit:
                break
    except Exception as exc:
        return [], f'NASA NTRS: {exc}'
    return results, None


def research_openalex(query, limit):
    results = []
    try:
        data = get_json(
            'https://api.openalex.org/works',
            {
                'search': f'"{query}" aircraft',
                'per-page': min(100, limit),
                'sort': 'relevance_score:desc'
            }
        )
        for work in data.get('results', []):
            title = work.get('title') or ''
            doi = work.get('doi')
            url = (
                doi or
                (work.get('primary_location') or {}).get('landing_page_url') or
                work.get('id')
            )
            if not title or not url:
                continue
            authors = [
                (a.get('author') or {}).get('display_name', '')
                for a in (work.get('authorships') or [])[:4]
            ]
            results.append({
                'title': title,
                'href': url,
                'body': '',
                'category': 'OpenAlex research',
                'authors': ', '.join(x for x in authors if x),
                'year': work.get('publication_year'),
                'source': 'OpenAlex',
                'doi': doi or ''
            })
    except Exception as exc:
        return [], f'OpenAlex: {exc}'
    return results, None


def research_crossref(query, limit):
    results = []
    try:
        data = get_json(
            'https://api.crossref.org/works',
            {
                'query.bibliographic': f'{query} aircraft',
                'rows': min(100, limit),
                'select': 'title,author,abstract,DOI,published,URL,type,publisher'
            }
        )
        for work in (data.get('message') or {}).get('items', []):
            title = (work.get('title') or [''])[0]
            doi = work.get('DOI')
            url = ('https://doi.org/' + doi) if doi else work.get('URL')
            if not title or not url:
                continue
            abstract = BeautifulSoup(
                work.get('abstract') or '', 'html.parser'
            ).get_text(' ', strip=True)
            authors = [
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in (work.get('author') or [])[:4]
            ]
            date_parts = (
                (work.get('published') or {}).get('date-parts') or [[None]]
            )[0]
            results.append({
                'title': title,
                'href': url,
                'body': abstract[:1800],
                'category': 'Crossref publication',
                'authors': ', '.join(authors),
                'year': date_parts[0] if date_parts else None,
                'source': 'Crossref',
                'doi': doi or ''
            })
    except Exception as exc:
        return [], f'Crossref: {exc}'
    return results, None


@app.get('/')
def home():
    return render_template('index.html')


@app.post('/api/search')
@app.post('/undefinedsearch')
def api_search():
    data = request.get_json(silent=True) or {}
    query = str(data.get('query', '')).strip()
    if not query:
        return jsonify({'error': 'Enter an aircraft, aerospace topic, or engineering subject.'}), 400

    try:
        web_limit = max(1, min(1000, int(data.get('web_limit', 150))))
        image_limit = max(1, min(1000, int(data.get('image_limit', 150))))
    except (TypeError, ValueError):
        return jsonify({'error': 'Result limits must be whole numbers.'}), 400

    region = str(data.get('region', 'wt-wt'))
    if region not in {'wt-wt', 'ph-en', 'us-en', 'uk-en'}:
        region = 'wt-wt'
    safe = str(data.get('safesearch', 'moderate'))
    if safe not in {'moderate', 'strict', 'off'}:
        safe = 'moderate'

    errors = []
    # 1. Work out which aircraft this is (Wikipedia article with an aircraft
    #    infobox). Everything below is filtered against its names.
    identity = ident.resolve_aircraft(query)
    matcher = ident.subject_matcher(query, identity)
    search_name = ident.search_name_for(query, identity)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            'web': pool.submit(search_web, search_name, matcher, region, safe, web_limit),
            'images': pool.submit(search_images, search_name, matcher, identity,
                                  region, safe, image_limit),
            'drawings': pool.submit(search_technical_drawings, search_name, matcher, 150),
            'openalex': pool.submit(research_openalex, search_name, min(100, web_limit)),
            'crossref': pool.submit(research_crossref, search_name, min(100, web_limit)),
            'ntrs': pool.submit(search_ntrs, search_name, min(100, web_limit))
        }
        gathered = {}
        for name, future in futures.items():
            try:
                gathered[name] = future.result()
            except Exception as exc:
                gathered[name] = ([], [f'{name} search: {exc}'])

    web_results, web_errors = gathered['web']
    image_results, image_errors = gathered['images']
    drawing_results, drawing_errors = gathered['drawings']
    oa_results, oa_error = gathered['openalex']
    cr_results, cr_error = gathered['crossref']
    ntrs_results, ntrs_error = gathered['ntrs']

    # Research: keep the providers' relevance order, but list papers that name
    # the aircraft in their title before ones that only mention it in the
    # abstract. Interleave sources so one provider does not bury the others.
    research_results, research_seen = [], set()
    interleaved = []
    for row in zip_longest(ntrs_results, oa_results, cr_results):
        interleaved.extend(item for item in row if item)
    for order, item in enumerate(interleaved):
        strength = matcher.strength(item.get('title', ''), item.get('body', ''))
        if strength == 0:
            continue
        key = (item.get('doi') or item.get('href') or '').lower()
        title_key = re.sub(r'[^a-z0-9]+', '', item.get('title', '').lower())
        if not key or key in research_seen or title_key in research_seen:
            continue
        research_seen.update({key, title_key})
        research_results.append((-strength, order, item))
    research_results = [row[2] for row in sorted(research_results, key=lambda r: r[:2])]

    # The aircraft's encyclopedia article is the most useful single source,
    # so it always leads the web tab when the aircraft was identified.
    if identity:
        web_results = [r for r in web_results
                       if r['href'].split('#')[0] != identity['url']]
        web_results.insert(0, {
            'title': f"{identity['title']} — Wikipedia",
            'href': identity['url'],
            'body': identity.get('extract') or identity.get('description') or '',
            'category': 'Encyclopedia article',
            'source': 'Wikipedia'
        })

    for group in (web_errors, image_errors, drawing_errors):
        if isinstance(group, list):
            errors.extend(group)
    for error in (oa_error, cr_error, ntrs_error):
        if error:
            errors.append(error)

    return jsonify({
        'query': query,
        'aircraft': ({k: identity[k] for k in ('title', 'url', 'description', 'thumbnail')}
                     if identity else None),
        'web_results': web_results[:web_limit],
        'research_results': research_results[:min(150, max(20, web_limit))],
        'image_results': image_results[:image_limit],
        'drawing_results': drawing_results[:150],
        'errors': errors[:8],
        'report_scope': [
            'Aircraft configuration / three-view',
            'Wing planform / sweep',
            'Empennage configuration',
            'Fuselage geometry / sections',
            'Wing structure / spars and ribs',
            'Landing gear arrangement',
            'Propulsion / engine installation',
            'Airfoil / high-lift devices',
            'Flight controls / actuation',
            'Avionics / cockpit'
        ],
        'provider_summary': {
            'web': 'Wikipedia + web search (Google, Bing, Yahoo via ddgs), filtered to the identified aircraft',
            'research': 'NASA NTRS + OpenAlex + Crossref',
            'images': 'Wikipedia article photos + Wikimedia Commons category + NASA Image Library + image search',
            'drawings': 'Wikimedia Commons, NASA NTRS and image search, expanded across all ASE 111 report topics'
        }
    })


@app.post('/api/extract')
def api_extract():
    data = request.get_json(silent=True) or {}
    url = str(data.get('url', '')).strip()
    parsed = urlparse(url)

    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        return jsonify({'error': 'Enter a valid public HTTP(S) URL.'}), 400

    host = parsed.hostname.lower()
    if host in {'localhost', '127.0.0.1', '::1'} or host.endswith('.local'):
        return jsonify({'error': 'Local addresses cannot be fetched.'}), 400

    try:
        ip = ipaddress.ip_address(socket.gethostbyname(host))
        if not ip.is_global:
            return jsonify({'error': 'Private network addresses cannot be fetched.'}), 400
    except ValueError:
        pass
    except Exception:
        pass

    try:
        response = SESSION.get(
            url, timeout=TIMEOUT, allow_redirects=True
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        for tag in soup([
            'script', 'style', 'nav', 'footer', 'header',
            'noscript', 'form', 'svg'
        ]):
            tag.decompose()
        main = soup.find('main') or soup.find('article') or soup.body or soup
        parts = [
            re.sub(r'\s+', ' ', node.get_text(' ', strip=True))
            for node in main.find_all(['h1', 'h2', 'h3', 'p', 'li'])
        ]
        extracted = '\n\n'.join(part for part in parts if len(part) > 45)
        if not extracted:
            extracted = re.sub(r'\s+', ' ', main.get_text(' ', strip=True))
        return jsonify({
            'text': extracted[:24000] or
            'No readable page text was found. The source may require JavaScript or block automated access.'
        })
    except Exception as exc:
        return jsonify({
            'error': f'Could not extract this page: {exc}. Open the original source directly.'
        }), 502



from pathlib import Path as _Path
AIRCRAFT_DATA_PATH = _Path(__file__).with_name("aircraft_dataset.json")
try:
    with AIRCRAFT_DATA_PATH.open("r", encoding="utf-8") as _f:
        AIRCRAFT_DATA = json.load(_f)
except Exception:
    AIRCRAFT_DATA = []

def _aircraft_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

def find_aircraft_record(query):
    key = _aircraft_key(query)
    if not key:
        return None
    for record in AIRCRAFT_DATA:
        candidates = [record.get("name"), record.get("id"), record.get("icao")]
        if any(key == _aircraft_key(candidate) for candidate in candidates if candidate):
            return record
    # Match exact known aliases only; avoid fuzzy guessing on aircraft identity.
    aliases = {
        "a10warthog": "fairchild-republic-a-10c-thunderbolt-ii",
        "warthog": "fairchild-republic-a-10c-thunderbolt-ii",
        "f16": "lockheed-martin-f-16c-fighting-falcon",
        "f22": "lockheed-martin-f-22a-raptor",
        "f35": "lockheed-martin-f-35a-lightning-ii",
        "747": "boeing-747-400",
        "boeing747": "boeing-747-400",
        "787": "boeing-787-9",
        "777": "boeing-777-300er",
        "320": "airbus-a320",
        "a320neo": "airbus-a320",
        "jf17": "jf-17-thunder-fc-1",
        "globalhawk": "northrop-grumman-rq-4-global-hawk",
        "mq9": "general-atomics-mq-9-reaper",
        "concorde": "concorde"
    }
    target = aliases.get(key)
    if target:
        return next((r for r in AIRCRAFT_DATA if r["id"] == target), None)
    return None

@app.get("/api/aircraft")
def api_aircraft_catalog():
    return jsonify({
        "aircraft": [
            {"id": r["id"], "name": r["name"], "manufacturer": r["manufacturer"],
             "category": r["category"], "icao": r.get("icao")}
            for r in AIRCRAFT_DATA
        ],
        "count": len(AIRCRAFT_DATA)
    })

@app.get("/api/aircraft/<aircraft_id>")
def api_aircraft_info(aircraft_id):
    record = next((r for r in AIRCRAFT_DATA if r["id"] == aircraft_id), None)
    if not record:
        return jsonify({"available": False, "message": "This aircraft is not available in the current Aeroscraping aircraft dataset."}), 404
    return jsonify({"available": True, "aircraft": record})

AIRFOIL_AIRCRAFT_MAP = {
    # David Lednicer, The Incomplete Guide to Airfoil Usage (UIUC-hosted).
    "boeing-747-100": [
        {"name":"Wing root: BAC 463 to BAC 468","file":"","evidence":"UIUC guide lists BAC 463–468 for the Boeing 747-100 wing root.","url":"https://m-selig.web.engr.illinois.edu/ads/aircraft.html"},
        {"name":"Wing tip: BAC 469 to BAC 474","file":"","evidence":"UIUC guide lists BAC 469–474 for the Boeing 747-100 wing tip.","url":"https://m-selig.web.engr.illinois.edu/ads/aircraft.html"}
    ],
    "boeing-747-200b": [
        {"name":"Wing root: BAC 463 to BAC 468","file":"","evidence":"UIUC guide lists BAC 463–468 for the Boeing 747-200 wing root.","url":"https://m-selig.web.engr.illinois.edu/ads/aircraft.html"},
        {"name":"Wing tip: BAC 469 to BAC 474","file":"","evidence":"UIUC guide lists BAC 469–474 for the Boeing 747-200 wing tip.","url":"https://m-selig.web.engr.illinois.edu/ads/aircraft.html"}
    ],
    "boeing-747-300": [
        {"name":"Wing root: BAC 463 to BAC 468","file":"","evidence":"UIUC guide lists BAC 463–468 for the Boeing 747-300 wing root.","url":"https://m-selig.web.engr.illinois.edu/ads/aircraft.html"},
        {"name":"Wing tip: BAC 469 to BAC 474","file":"","evidence":"UIUC guide lists BAC 469–474 for the Boeing 747-300 wing tip.","url":"https://m-selig.web.engr.illinois.edu/ads/aircraft.html"}
    ],
    "boeing-747-400": [
        {"name":"Wing root: BAC 463 to BAC 468","file":"","evidence":"UIUC guide lists BAC 463–468 for the Boeing 747-400 wing root.","url":"https://m-selig.web.engr.illinois.edu/ads/aircraft.html"},
        {"name":"Wing tip: BAC 469 to BAC 474","file":"","evidence":"UIUC guide lists BAC 469–474 for the Boeing 747-400 wing tip.","url":"https://m-selig.web.engr.illinois.edu/ads/aircraft.html"}
    ],
    "airbus-a380-800": [
        {"name":"Wing-root thickness ratio: 13%","file":"","evidence":"UIUC guide lists a 13% thickness ratio at the Airbus A380 wing root; it does not give an exact profile designation in this entry.","url":"https://m-selig.web.engr.illinois.edu/ads/aircraft.html"},
        {"name":"Wing-tip thickness ratio: 9.1%","file":"","evidence":"UIUC guide lists a 9.1% thickness ratio at the Airbus A380 wing tip; this is a thickness ratio, not an airfoil designation.","url":"https://m-selig.web.engr.illinois.edu/ads/aircraft.html"}
    ],
    "northrop-grumman-rq-4-global-hawk": [
        {"name":"LRN 1015 (wing section)","file":"lrn1015.dat","evidence":"UIUC directory identifies LRN 1015 as the airfoil used on the Northrop Grumman RQ-4 Global Hawk.","url":"https://m-selig.ae.illinois.edu/ads/coord/lrn1015.dat"}
    ]
}


def wikipedia_airfoil_profiles(identity):
    """
    The airfoil stated in the aircraft's Wikipedia specification table (these
    are usually cited to Lednicer's Incomplete Guide to Airfoil Usage).
    Nothing is returned when the article states none — no guessing.
    """
    if not identity:
        return []
    stated = ident.spec_value(ident.wikipedia_specs(identity["title"]), "airfoil", "aerofoil")
    if not stated:
        return []
    return [{
        "name": stated,
        "file": "",
        "evidence": f"Stated in the “Specifications” section of the Wikipedia article "
                    f"“{identity['title']}”. Check the reference cited there before formal use.",
        "url": identity["url"] + "#Specifications"
    }]


def lookup_aircraft(query):
    """Curated dataset record (if any) plus the Wikipedia identity (if any)."""
    identity = ident.resolve_aircraft(query)
    record = find_aircraft_record(query)
    if not record and identity:
        for alias in identity["aliases"]:
            record = find_aircraft_record(alias)
            if record:
                break
    return record, identity


@app.get("/api/aircraft/lookup")
def api_aircraft_lookup():
    query = request.args.get("query", "").strip()
    record, identity = lookup_aircraft(query)
    if not record and not identity:
        return jsonify({
            "available": False,
            "message": "No aircraft matching this search was found in the curated "
                       "dataset or on Wikipedia. Check the spelling or use the full "
                       "model name (for example “Cessna 172” or “F-16”)."
        }), 404
    wiki = None
    if identity:
        specs = ident.wikipedia_specs(identity["title"])
        wiki = {**{k: identity[k] for k in ("title", "url", "description", "extract", "thumbnail")},
                "specs": specs}
    return jsonify({"available": True, "aircraft": record, "wikipedia": wiki})


@app.get("/api/airfoil")
def api_airfoil_lookup():
    query = request.args.get("query", "").strip()
    record, identity = lookup_aircraft(query)
    if not record and not identity:
        return jsonify({
            "available": False,
            "message": "This aircraft could not be identified in the curated dataset or on Wikipedia."
        }), 404
    sources = [
        {"name": "UIUC Airfoil Coordinates Database (~1,650 profiles)", "url": "https://m-selig.web.engr.illinois.edu/ads/coord_database.html"},
        {"name": "UIUC Incomplete Guide to Airfoil Usage (aircraft-to-airfoil references)", "url": "https://m-selig.web.engr.illinois.edu/ads/aircraft.html"}
    ]
    if not record:
        profiles = wikipedia_airfoil_profiles(identity)
        return jsonify({
            "available": True,
            "aircraft": identity["title"],
            "profiles": profiles,
            "message": (
                "An airfoil designation is stated in the aircraft's published specifications."
                if profiles else
                "No airfoil designation is published for this aircraft in its specifications or "
                "in Aeroscraping's curated mapping. We will not guess or assign a generic NACA profile."
            ),
            "sources": sources
        })
    profiles = AIRFOIL_AIRCRAFT_MAP.get(record["id"], [])
    if not profiles and record["id"].startswith("boeing-747-"):
        if record["id"] in {"boeing-747-8", "boeing-747-8-intercontinental", "boeing-747-8-freighter"}:
            profiles = [{
                "name": "747-8 redesigned wing airfoil system",
                "file": "",
                "evidence": "Boeing states that the 747-8 has a new wing design including new airfoils. The source does not provide individual root/tip designations here, so Aeroscraping does not assume the 747 Classics BAC sections apply.",
                "url": "https://www.boeing.com/content/dam/boeing/v2/airports/acaps/747-8_Rev_E.pdf"
            }]
        else:
            profiles = AIRFOIL_AIRCRAFT_MAP.get("boeing-747-400", [])
    if not profiles and record["id"].startswith("airbus-a380-"):
        profiles = AIRFOIL_AIRCRAFT_MAP.get("airbus-a380-800", [])
    if not profiles:
        profiles = wikipedia_airfoil_profiles(identity)
    return jsonify({
        "available": True,
        "aircraft": record["name"],
        "profiles": profiles,
        "message": (
            "A documented aircraft-to-airfoil association is available below."
            if profiles else
            "No verified airfoil designation for this aircraft is recorded in Aeroscraping's curated dataset or its published specifications. We will not guess or assign a generic NACA profile. Check the UIUC airfoil directory and aircraft technical publications for a documented section."
        ),
        "sources": sources
    })

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
