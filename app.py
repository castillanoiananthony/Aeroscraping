from flask import Flask, render_template, request, jsonify
from ddgs import DDGS
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import requests
import re
import json
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Aeroscraping/3.0 (aerospace research discovery; contact: castillano.iananthony.h@gmail.com)'
})
TIMEOUT = 25

AERO_TERMS = {
    'aircraft', 'aerospace', 'aviation', 'airplane', 'aerodynamics',
    'airfoil', 'wing', 'fuselage', 'propulsion', 'engine', 'avionics',
    'flight', 'airframe', 'helicopter', 'rotorcraft', 'jet', 'turbofan',
    'turboprop', 'supersonic', 'subsonic', 'transonic', 'fighter',
    'bomber', 'airliner', 'glider', 'uav', 'drone', 'landing gear',
    'empennage', 'flap', 'slat', 'spoiler', 'aileron', 'rudder',
    'elevator', 'stability', 'control', 'airfoil coordinates',
    'aircraft structure', 'aircraft design'
}

DRAWING_TERMS = (
    'three view', 'three-view', '3 view', '3-view', 'orthographic',
    'blueprint', 'technical drawing', 'engineering drawing',
    'airfoil', 'aerofoil', 'airfoil profile', 'airfoil section',
    'wing planform', 'planform', 'cross section', 'cross-section',
    'schematic', 'diagram', 'structural layout', 'spar', 'rib',
    'fuselage frame', 'empennage', 'control surface',
    'general arrangement', 'aircraft dimensions', 'blue print'
)

DRAWING_QUERIES = [
    'aircraft original engineering blueprint',
    'aircraft detailed construction blueprint',
    'aircraft three view orthographic drawing',
    'aircraft general arrangement drawing',
    'aircraft wing planform drawing',
    'aircraft airfoil profile and coordinates',
    'aircraft fuselage cross section drawing',
    'aircraft wing spar rib structural blueprint',
    'aircraft landing gear engineering drawing',
    'aircraft engine installation schematic',
    'aircraft flight control system drawing',
    'aircraft empennage technical drawing',
    'aircraft exploded assembly drawing',
    'aircraft hydraulic system schematic',
    'aircraft electrical wiring diagram',
    'aircraft mechanical engineering drawing'
]


def norm(value):
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


def tokens(value):
    return [
        token for token in re.findall(r'[a-z0-9]+', str(value or '').lower())
        if len(token) > 1
    ]


def query_tokens(query):
    excluded = {
        'the', 'and', 'for', 'with', 'about', 'aircraft',
        'airplane', 'aerospace', 'aviation', 'information', 'research'
    }
    return [token for token in tokens(query) if token not in excluded]


def query_match_score(query, text):
    query_words = query_tokens(query)
    text_words = set(tokens(text))
    if not query_words:
        return 0.0
    compact_query = norm(query)
    compact_text = norm(text)
    if len(compact_query) >= 3 and compact_query in compact_text:
        return 1.0
    hits = sum(
        1 for word in query_words
        if word in text_words or word in compact_text
    )
    return hits / len(query_words)


def specific_query_terms(query):
    generic = {
        'aircraft', 'airplane', 'aerospace', 'aviation', 'technical',
        'drawing', 'drawings', 'diagram', 'diagrams', 'engineering',
        'design', 'image', 'images', 'photo', 'photos', 'the', 'and',
        'for', 'with', 'of', 'view', 'views', 'three'
    }
    return [t for t in tokens(query) if t not in generic]


def matches_requested_subject(query, title, description=''):
    """
    Strictly require the requested subject in the file title/caption.
    This prevents a generic aircraft document from appearing for Concorde.
    """
    terms = specific_query_terms(query)
    if not terms:
        return False
    text = f'{title} {description}'.lower()
    text_norm = norm(text)
    matched = sum(
        1 for term in terms
        if term in tokens(text) or norm(term) in text_norm
    )
    # One-word aircraft names (e.g. Concorde) must match exactly.
    if len(terms) == 1:
        return matched == 1
    return matched >= max(1, (len(terms) + 1) // 2)


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


def is_aerospace_relevant(query, title, body='', url=''):
    blob = f'{title} {body} {url}'.lower()
    qscore = query_match_score(query, blob)
    aero_hits = sum(1 for term in AERO_TERMS if term in blob)
    # A strong match to the requested aircraft/topic is sufficient.
    return qscore >= 0.34 or (qscore >= 0.20 and aero_hits >= 1)


def get_json(url, params=None, timeout=TIMEOUT):
    response = SESSION.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def ddg_text(query, region, safe, limit, backend="duckduckgo"):
    """Query one supported DDGS text backend; caller merges provider results."""
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


def dedupe_web(items, query, limit):
    """Keep broad search-engine matches; discard only empty, duplicate and obvious junk."""
    seen, output = set(), []
    blocked = (
        "phonics song", "lyrics", "music video", "letter a song",
        "word search", "crossword", "pinterest login"
    )
    for item in items:
        title = str(item.get("title") or "").strip()
        url = str(item.get("href") or item.get("url") or "").strip()
        body = str(item.get("body") or item.get("snippet") or "").strip()
        if not title or not url or not url.startswith(("http://", "https://")):
            continue
        blob = f"{title} {body} {url}".lower()
        if any(term in blob for term in blocked):
            continue
        key = url.split("#")[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        output.append({**item, "title": title, "href": url, "body": body})
        if len(output) >= limit:
            break
    return output


def search_web(query, region, safe, limit):
    """Broad general web discovery; no aerospace keyword whitelist."""
    variants = [
        query, f'"{query}"', f'{query} aircraft', f'{query} airplane',
        f'{query} specifications', f'{query} technical data',
        f'{query} dimensions', f'{query} manual OR report OR thesis'
    ]
    backends = ("google", "bing", "brave", "duckduckgo", "yahoo")
    per_job = max(20, min(60, (limit + len(variants) - 1) // len(variants)))
    jobs = [(term, backend) for term in variants for backend in backends]
    collected, errors = [], []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {
            pool.submit(ddg_text, term, region, safe, per_job, backend): (term, backend)
            for term, backend in jobs
        }
        for future in as_completed(futures):
            term, backend = futures[future]
            try:
                found, error = future.result()
                collected.extend(found)
                if error:
                    errors.append(f"{backend} ({term}): {error}")
            except Exception as exc:
                errors.append(f"{backend} ({term}): {exc}")
    return dedupe_web(collected, query, limit), errors


def search_nasa_images(query, limit):
    """Search NASA's public Image and Video Library; no API key required."""
    results, errors, seen = [], [], set()
    queries = [
        f'{query} aircraft',
        f'{query} airplane',
        f'{query} aviation'
    ]
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
                if not matches_requested_subject(query, title, description):
                    continue
                key = nasa_id or image_url
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    'title': title,
                    'image': image_url,
                    'thumbnail': image_url,
                    'url': item.get('href') or
                           (f'https://images.nasa.gov/details/{nasa_id}' if nasa_id else image_url),
                    'href': item.get('href') or image_url,
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


def search_images(query, region, safe, limit):
    """Aircraft image discovery from NASA and general image search."""
    nasa_results, errors = search_nasa_images(query, limit)
    variants = [query, f"{query} aircraft", f"{query} airplane", f"{query} aviation"]
    per_query = max(25, min(100, (limit + len(variants) - 1) // len(variants)))
    collected = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(ddg_images, term, region, safe, per_query) for term in variants]
        for future in as_completed(futures):
            try:
                rows, error = future.result()
                collected.extend(rows)
                if error:
                    errors.append(error)
            except Exception as exc:
                errors.append(f"Image search: {exc}")

    output, seen = [], set()
    for item in nasa_results:
        key = item.get("image") or item.get("url")
        if key and key not in seen:
            seen.add(key)
            output.append(item)
    for item in collected:
        title = str(item.get("title") or "Aircraft image").strip()
        image_url = item.get("image") or item.get("thumbnail") or item.get("url")
        page_url = item.get("url") or image_url
        if not image_url:
            continue
        if not matches_requested_subject(query, title, f"{page_url} {item.get('source','')}"):
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


def search_ddg_technical_images(query, limit, errors):
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
                    if not image_url:
                        continue
                    evidence = f"{title} {page_url or ''} {item.get('source') or ''}"
                    if not matches_requested_subject(query, title, evidence):
                        continue
                    if not has_drawing_evidence(title, evidence):
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


def search_commons_drawings(query, limit):
    """
    Search Wikimedia Commons for actual aircraft technical illustrations.
    Subject-only and topic-specific searches prevent over-constraining captions.
    """
    results, errors, seen = [], [], set()
    search_jobs = [
        ("Aircraft configuration / three-view", f"{query} aircraft three-view drawing"),
        ("Aircraft configuration / three-view", f"{query} aircraft orthographic line drawing"),
        ("Aircraft configuration / three-view", f"{query} aircraft general arrangement drawing"),
    ]
    search_jobs.extend(technical_search_terms(query))
    per_query = max(25, min(60, (limit + len(search_jobs) - 1) // len(search_jobs)))

    for label, search_term in search_jobs:
        try:
            data = get_json(
                "https://commons.wikimedia.org/w/api.php",
                {
                    "action": "query",
                    "format": "json",
                    "generator": "search",
                    "gsrsearch": search_term,
                    "gsrnamespace": 6,
                    "gsrlimit": per_query,
                    "prop": "imageinfo",
                    "iiprop": "url|mime|extmetadata",
                    "iiurlwidth": 1400
                }
            )
            pages = (data.get("query") or {}).get("pages") or {}
            for page in pages.values():
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
                if not matches_requested_subject(query, raw_title, description):
                    continue
                if not has_drawing_evidence(raw_title, description):
                    continue

                key = page_url.split("#")[0].lower()
                if key in seen:
                    continue
                seen.add(key)

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


def search_ntrs_drawings(query, limit):
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
                if not matches_requested_subject(query, title, abstract):
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


def search_technical_drawings(query, limit):
    """
    Single-input drawing aggregator: automatically searches all assignment topics
    through Wikimedia Commons and image search, then merges verified candidates.
    """
    commons_results, errors = search_commons_drawings(query, limit)
    nasa_results, nasa_errors = search_ntrs_drawings(query, limit)
    errors.extend(nasa_errors)
    image_results = search_ddg_technical_images(query, limit, errors)

    merged, seen = [], set()
    # Prefer actual archival/NASA engineering drawings, then Commons and image-search results.
    for item in nasa_results + commons_results + image_results:
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
            {'q': f'{query} aircraft aerospace', 'page.size': min(100, limit)},
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
                'search': f'{query} aerospace aircraft',
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
                'query.bibliographic': f'{query} aircraft aerospace engineering',
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
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            'web': pool.submit(search_web, query, region, safe, web_limit),
            'images': pool.submit(search_images, query, region, safe, image_limit),
            'drawings': pool.submit(search_technical_drawings, query, 150),
            'openalex': pool.submit(research_openalex, query, min(100, web_limit)),
            'crossref': pool.submit(research_crossref, query, min(100, web_limit)),
            'ntrs': pool.submit(search_ntrs, query, min(100, web_limit))
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

    research_results, research_seen = [], set()
    for item in ntrs_results + oa_results + cr_results:
        if not matches_requested_subject(query, item.get('title',''), item.get('body','')):
            continue
        key = item.get('doi') or item.get('href')
        if key and key not in research_seen:
            research_seen.add(key)
            research_results.append(item)
    research_results.sort(
        key=lambda item: str(item.get('year') or ''),
        reverse=True
    )

    for group in (web_errors, image_errors, drawing_errors):
        if isinstance(group, list):
            errors.extend(group)
    for error in (oa_error, cr_error, ntrs_error):
        if error:
            errors.append(error)

    return jsonify({
        'query': query,
        'web_results': web_results,
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
            'web': 'DuckDuckGo web search',
            'research': 'NASA NTRS + OpenAlex + Crossref',
            'images': 'NASA Image and Video Library + DuckDuckGo Images',
            'drawings': 'Aircraft-specific technical searches across Wikimedia Commons and DuckDuckGo Images; automatically expanded across all ASE 111 report topics'
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


@app.get("/api/aircraft/lookup")
def api_aircraft_lookup():
    query = request.args.get("query", "").strip()
    record = find_aircraft_record(query)
    if not record:
        return jsonify({"available": False, "message": "This aircraft is not available in the current Aeroscraping aircraft dataset."}), 404
    return jsonify({"available": True, "aircraft": record})

@app.get("/api/airfoil")
def api_airfoil_lookup():
    query = request.args.get("query", "").strip()
    record = find_aircraft_record(query)
    if not record:
        return jsonify({
            "available": False,
            "message": "This aircraft is not available in the current Aeroscraping aircraft dataset. Choose a listed aircraft or search an airfoil designation directly."
        }), 404
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
    return jsonify({
        "available": True,
        "aircraft": record["name"],
        "profiles": profiles,
        "message": (
            "A documented aircraft-to-airfoil association is available below."
            if profiles else
            "No verified airfoil designation for this aircraft is currently recorded in Aeroscraping's curated dataset. We will not guess or assign a generic NACA profile. Check the UIUC airfoil directory and aircraft technical publications for a documented section."
        ),
        "sources": [
            {"name": "UIUC Airfoil Coordinates Database (~1,650 profiles)", "url": "https://m-selig.web.engr.illinois.edu/ads/coord_database.html"},
            {"name": "UIUC Incomplete Guide to Airfoil Usage (aircraft-to-airfoil references)", "url": "https://m-selig.web.engr.illinois.edu/ads/coord_database.html"}
        ]
    })

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
