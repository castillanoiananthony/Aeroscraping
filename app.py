from flask import Flask, render_template, request, jsonify
from ddgs import DDGS
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import requests
import re
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
    'general arrangement', 'aircraft dimensions'
)

DRAWING_QUERIES = [
    'aircraft three view drawing',
    'airplane three view orthographic drawing',
    'aircraft general arrangement drawing',
    'aircraft technical blueprint',
    'airfoil profile diagram',
    'NACA airfoil section coordinates',
    'aircraft wing planform drawing',
    'aircraft wing spar rib structure',
    'aircraft fuselage cross section drawing',
    'aircraft empennage control surfaces drawing',
    'aircraft engineering schematic',
    'aircraft structural diagram'
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


def ddg_text(query, region, safe, limit):
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(
                query, region=region, safesearch=safe, max_results=limit
            )), None
    except Exception as exc:
        return [], f'DuckDuckGo web search: {exc}'


def ddg_images(query, region, safe, limit):
    try:
        with DDGS() as ddgs:
            return list(ddgs.images(
                query, region=region, safesearch=safe, max_results=limit
            )), None
    except Exception as exc:
        return [], f'DuckDuckGo image search: {exc}'


def dedupe_web(items, query, limit):
    seen, output = set(), []
    for item in items:
        title = str(item.get('title') or '').strip()
        url = str(item.get('href') or item.get('url') or '').strip()
        body = str(item.get('body') or item.get('snippet') or '').strip()
        if not title or not url:
            continue
        if not is_aerospace_relevant(query, title, body, url):
            continue
        key = url.split('#')[0].rstrip('/').lower()
        if key in seen:
            continue
        seen.add(key)
        output.append({
            **item,
            'title': title,
            'href': url,
            'body': body
        })
        if len(output) >= limit:
            break
    return output


def search_web(query, region, safe, limit):
    variants = [
        f'"{query}" aircraft aerospace engineering',
        f'{query} aircraft design aerodynamics structure propulsion',
        f'{query} aviation technical information manufacturer NASA FAA',
        f'{query} aircraft specifications technical data'
    ]
    collected, errors = [], []
    per_query = max(15, min(60, (limit + len(variants) - 1) // len(variants)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(ddg_text, term, region, safe, per_query)
            for term in variants
        ]
        for future in as_completed(futures):
            try:
                found, error = future.result()
                collected.extend(found)
                if error:
                    errors.append(error)
            except Exception as exc:
                errors.append(f'Web search task: {exc}')
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
                if not is_aerospace_relevant(query, title, description, image_url):
                    # NASA aircraft-specific query results are still useful;
                    # do not discard them merely because NASA's caption is short.
                    if not any(word in f'{title} {description}'.lower()
                               for word in ('aircraft', 'airplane', 'aviation',
                                            'wing', 'flight', 'aeronautic',
                                            'concorde', 'jet')):
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
    """Merge NASA and DuckDuckGo images, prioritizing NASA source results."""
    nasa_results, nasa_errors = search_nasa_images(query, limit)
    ddg_results, ddg_error = ddg_images(
        f'{query} aircraft aerospace aviation',
        region, safe, min(1000, limit)
    )
    output, seen = [], set()
    for item in nasa_results:
        key = item.get('image') or item.get('url')
        if key and key not in seen:
            seen.add(key)
            output.append(item)
    if ddg_error:
        nasa_errors.append(ddg_error)
    for item in ddg_results:
        title = item.get('title') or 'Aerospace image'
        image_url = item.get('image') or item.get('thumbnail') or item.get('url')
        page_url = item.get('url') or image_url
        if not image_url:
            continue
        if not is_aerospace_relevant(query, title, item.get('source', ''), page_url):
            continue
        key = image_url.split('?')[0]
        if key in seen:
            continue
        seen.add(key)
        output.append({
            'title': title,
            'image': image_url,
            'thumbnail': item.get('thumbnail') or image_url,
            'url': page_url,
            'href': page_url,
            'source': item.get('source') or 'DuckDuckGo Images'
        })
        if len(output) >= limit:
            break
    return output[:limit], nasa_errors


def search_commons_drawings(query, limit):
    """
    Search Wikimedia Commons using broad technical-drawing queries.
    Keep files with a preview image and a source page; avoid brittle
    keyword-only filtering that previously returned zero drawings.
    """
    results, errors, seen = [], [], set()
    query_list = [f'{query} aircraft technical drawing']
    query_list.extend(DRAWING_QUERIES)
    per_query = max(20, min(60, (limit + len(query_list) - 1) // len(query_list)))

    for search_term in query_list:
        try:
            data = get_json(
                'https://commons.wikimedia.org/w/api.php',
                {
                    'action': 'query',
                    'format': 'json',
                    'generator': 'search',
                    'gsrsearch': search_term,
                    'gsrnamespace': 6,
                    'gsrlimit': per_query,
                    'prop': 'imageinfo',
                    'iiprop': 'url|extmetadata',
                    'iiurlwidth': 1000
                }
            )
            pages = (data.get('query') or {}).get('pages') or {}
            for page in pages.values():
                file_title = page.get('title', '').replace('File:', '')
                info = (page.get('imageinfo') or [{}])[0]
                metadata = info.get('extmetadata') or {}
                description_html = metadata.get('ImageDescription', {}).get('value', '')
                description = BeautifulSoup(
                    description_html, 'html.parser'
                ).get_text(' ', strip=True)
                page_url = info.get('descriptionurl') or (
                    'https://commons.wikimedia.org/wiki/' +
                    page.get('title', '').replace(' ', '_')
                )
                preview = info.get('thumburl') or info.get('url')
                if not preview:
                    continue
                combined = f'{file_title} {description}'.lower()
                # Search terms themselves are technical-drawing focused.
                # Require a relevant aircraft/engineering clue, but not an
                # exact phrase; Commons captions are often sparse.
                drawing_match = any(term in combined for term in DRAWING_TERMS)
                aircraft_match = any(term in combined for term in (
                    'aircraft', 'airplane', 'aeroplane', 'aviation',
                    'airfoil', 'aerofoil', 'wing', 'fuselage',
                    'airbus', 'boeing', 'concorde', 'naca', 'cessna',
                    'fighter', 'jet', 'glider', 'helicopter'
                ))
                if not (drawing_match or aircraft_match):
                    continue
                key = page_url.split('#')[0].lower()
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    'title': file_title,
                    'href': page_url,
                    'url': page_url,
                    'image': preview,
                    'thumbnail': preview,
                    'body': description[:1800] or
                            'Technical image from Wikimedia Commons. Open the source page to inspect its description and licensing.',
                    'description': description[:1800],
                    'drawing_type': classify_drawing(file_title + ' ' + description),
                    'category': 'Technical drawing / engineering reference',
                    'source': 'Wikimedia Commons',
                    'license': metadata.get('LicenseShortName', {}).get('value', 'Check source page')
                })
                if len(results) >= limit:
                    return results[:limit], errors
        except Exception as exc:
            errors.append(f'Wikimedia Commons ({search_term}): {exc}')
    return results[:limit], errors


def classify_drawing(text):
    text = text.lower()
    labels = [
        ('Three-view / orthographic', ('three view', 'three-view', 'orthographic', 'general arrangement')),
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
            'drawings': pool.submit(search_commons_drawings, query, 150),
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
        'provider_summary': {
            'web': 'DuckDuckGo web search',
            'research': 'NASA NTRS + OpenAlex + Crossref',
            'images': 'NASA Image and Video Library + DuckDuckGo Images',
            'drawings': 'Wikimedia Commons technical drawing searches'
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


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
