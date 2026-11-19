from flask import Flask, render_template, request, jsonify
from ddgs import DDGS
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from functools import lru_cache
import requests, re, html, ipaddress, socket, time
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'Aeroscraping/2.0 (aerospace research discovery; contact: castillano.iananthony.h@gmail.com)'})
TIMEOUT = 14
AERO_TERMS = {
    'aircraft','aerospace','aviation','airplane','aircraft design','aerodynamics','airfoil','wing','fuselage',
    'propulsion','engine','avionics','flight','pilot','airframe','helicopter','rotorcraft','jet','turbofan',
    'turboprop','supersonic','subsonic','transonic','fighter','bomber','airliner','glider','missile','uav','drone',
    'landing gear','empennage','flap','slat','spoiler','aileron','rudder','elevator','stability','control'
}
IRRELEVANT = ['store finder','shopping','online shop','lenovo support','driver download','repair warranty','coupon','retail store']
DRAWING_TERMS = ['three view','three-view','3 view','3-view','orthographic','blueprint','technical drawing','engineering drawing','airfoil','airfoil profile','airfoil section','wing planform','planform','cross section','cross-section','schematic','diagram','structural layout','spar','rib','fuselage frame','empennage','control surface','general arrangement']
DRAWING_SPECS = [
    ('Three-view / general arrangement', 'three view orthographic general arrangement drawing'),
    ('Airfoil profile / section', 'airfoil profile airfoil section aerofoil coordinates drawing'),
    ('Wing planform / geometry', 'aircraft wing planform geometry technical drawing'),
    ('Wing internal structure', 'aircraft wing spar rib internal structure diagram'),
    ('Fuselage cross-section / structure', 'aircraft fuselage cross section frames stringers structural drawing'),
    ('Empennage / control surfaces', 'aircraft empennage tailplane rudder elevator control surfaces diagram'),
]

def norm(s):
    return re.sub(r'[^a-z0-9]+', '', str(s or '').lower())

def tokens(s):
    return [x for x in re.findall(r'[a-z0-9]+', str(s or '').lower()) if len(x) > 1]

def query_tokens(query):
    return [x for x in tokens(query) if x not in {'the','and','for','with','about','aircraft','airplane','aerospace','aviation','information','research'}]

def query_match_score(query, text):
    qtokens = query_tokens(query)
    text_tokens = set(tokens(text))
    if not qtokens:
        return 0
    compact_query, compact_text = norm(query), norm(text)
    if len(compact_query) >= 3 and compact_query in compact_text:
        return 1.0
    hits = sum(1 for t in qtokens if t in text_tokens or t in compact_text)
    return hits / len(qtokens)

def is_aerospace_relevant(query, title, body='', url=''):
    blob = f'{title} {body} {url}'.lower()
    if any(term in blob for term in IRRELEVANT):
        return False
    qscore = query_match_score(query, blob)
    aero_hits = sum(1 for term in AERO_TERMS if term in blob)
    # A result must mention the requested aircraft/topic, or have strong aerospace context.
    return (qscore >= 0.34 and aero_hits >= 1) or (qscore >= 0.2 and aero_hits >= 2)

def get_json(url, params=None):
    r = SESSION.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def ddg_text(query, region, safe, limit):
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, region=region, safesearch=safe, max_results=limit))
    except Exception as exc:
        return [], f'DuckDuckGo web search: {exc}'

def ddg_images(query, region, safe, limit):
    try:
        with DDGS() as ddgs:
            return list(ddgs.images(query, region=region, safesearch=safe, max_results=limit))
    except Exception as exc:
        return [], f'DuckDuckGo image search: {exc}'

def dedupe(items, query, limit):
    seen, out = set(), []
    for item in items:
        title = str(item.get('title') or '').strip()
        url = str(item.get('href') or item.get('url') or '').strip()
        body = str(item.get('body') or item.get('snippet') or '').strip()
        if not title or not url or not is_aerospace_relevant(query, title, body, url):
            continue
        key = url.split('#')[0].rstrip('/').lower()
        if key in seen:
            continue
        seen.add(key)
        item['title'] = title
        item['href'] = url
        item['body'] = body
        out.append(item)
        if len(out) >= limit:
            break
    return out

def search_web(query, region, safe, limit):
    variants = [
        f'"{query}" aircraft aerospace engineering',
        f'{query} aircraft design aerodynamics structure propulsion',
        f'{query} aviation technical information manufacturer NASA FAA',
    ]
    collected, errors = [], []
    per = max(10, min(50, (limit + len(variants)-1)//len(variants)))
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(ddg_text, term, region, safe, per) for term in variants]
        for future in as_completed(futures):
            found, err = future.result()
            collected.extend(found)
            if err: errors.append(err)
    return dedupe(collected, query, limit), errors

def abstract_from_openalex(inv):
    if not isinstance(inv, dict): return ''
    words = []
    for word, positions in inv.items():
        for pos in positions:
            words.append((pos, word))
    words.sort()
    return ' '.join(w for _, w in words)

def research_openalex(query, limit):
    results = []
    try:
        data = get_json('https://api.openalex.org/works', {'search': f'{query} aerospace aircraft', 'per-page': min(100, limit), 'sort': 'relevance_score:desc'})
        for work in data.get('results', []):
            title = work.get('title') or ''
            abstract = abstract_from_openalex(work.get('abstract_inverted_index'))
            doi = work.get('doi')
            url = doi or (work.get('primary_location') or {}).get('landing_page_url') or work.get('id')
            if not title or not url or not is_aerospace_relevant(query, title, abstract, url): continue
            authors = []
            for a in (work.get('authorships') or [])[:4]:
                name = ((a.get('author') or {}).get('display_name'))
                if name: authors.append(name)
            results.append({'title': title, 'href': url, 'body': abstract[:1800] or 'No abstract supplied by the OpenAlex record.', 'category': 'OpenAlex research', 'authors': ', '.join(authors), 'year': work.get('publication_year'), 'source': 'OpenAlex', 'doi': doi or ''})
    except Exception as exc:
        return [], f'OpenAlex: {exc}'
    return results, None

def research_crossref(query, limit):
    results = []
    try:
        data = get_json('https://api.crossref.org/works', {'query.bibliographic': f'{query} aircraft aerospace engineering', 'rows': min(100, limit), 'select': 'title,author,abstract,DOI,published,URL,type,publisher'})
        for work in (data.get('message') or {}).get('items', []):
            title = (work.get('title') or [''])[0]
            abstract = BeautifulSoup(work.get('abstract') or '', 'html.parser').get_text(' ', strip=True)
            doi = work.get('DOI')
            url = ('https://doi.org/' + doi) if doi else work.get('URL')
            if not title or not url or not is_aerospace_relevant(query, title, abstract, url): continue
            authors = [f"{a.get('given','')} {a.get('family','')}".strip() for a in (work.get('author') or [])[:4]]
            date_parts = ((work.get('published') or {}).get('date-parts') or [[None]])[0]
            year = date_parts[0] if date_parts else None
            results.append({'title': title, 'href': url, 'body': abstract[:1800] or f"{work.get('type','Publication').replace('-', ' ').title()} published by {work.get('publisher') or 'Crossref member'}.", 'category': 'Crossref publication', 'authors': ', '.join(authors), 'year': year, 'source': 'Crossref', 'doi': doi or ''})
    except Exception as exc:
        return [], f'Crossref: {exc}'
    return results, None

def commons_one_type(query, label, terms, per_type):
    search_term = f'{query} aircraft {terms}'
    data = get_json('https://commons.wikimedia.org/w/api.php', {
        'action':'query', 'format':'json', 'generator':'search', 'gsrsearch':search_term,
        'gsrnamespace':6, 'gsrlimit':per_type, 'prop':'imageinfo',
        'iiprop':'url|extmetadata', 'iiurlwidth':640,
    })
    found = []
    pages = (data.get('query') or {}).get('pages') or {}
    for page in pages.values():
        title = page.get('title','').replace('File:','')
        info = (page.get('imageinfo') or [{}])[0]
        meta = info.get('extmetadata') or {}
        desc = BeautifulSoup(meta.get('ImageDescription',{}).get('value',''), 'html.parser').get_text(' ', strip=True)
        combined = f'{title} {desc}'
        if not is_aerospace_relevant(query, title, desc, info.get('descriptionurl','')):
            continue
        if not any(term in combined.lower() for term in DRAWING_TERMS):
            continue
        url = info.get('descriptionurl') or ('https://commons.wikimedia.org/wiki/' + page.get('title','').replace(' ','_'))
        found.append({'title':title, 'href':url, 'url':info.get('url',''), 'thumbnail':info.get('thumburl') or info.get('url',''), 'body':re.sub(r'\s+',' ',desc)[:1200] or 'Wikimedia Commons file identified as an aircraft drawing or diagram. Verify the file description and licensing on the source page.', 'drawing_type':label, 'category':'Wikimedia Commons technical drawing', 'source':'Wikimedia Commons'})
    return found

def search_commons_drawings(query, limit):
    results, errors, seen = [], [], set()
    per_type = max(8, min(30, (limit + len(DRAWING_SPECS)-1)//len(DRAWING_SPECS)))
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(commons_one_type, query, label, terms, per_type): label for label, terms in DRAWING_SPECS}
        for future in as_completed(futures):
            label = futures[future]
            try:
                for item in future.result():
                    key = item['href'].lower()
                    if key not in seen:
                        seen.add(key); results.append(item)
            except Exception as exc:
                errors.append(f'Wikimedia Commons ({label}): {exc}')
    return results[:limit], errors

def search_images(query, region, safe, limit):
    found, err = ddg_images(f'{query} aircraft aerospace aviation', region, safe, min(limit, 1000))
    if err: return [], [err]
    out, seen = [], set()
    for item in found:
        title = item.get('title') or 'Aerospace image'
        image = item.get('image') or item.get('thumbnail') or item.get('url')
        url = item.get('url') or image
        if not image or not is_aerospace_relevant(query, title, item.get('source',''), url):
            continue
        key = image.split('?')[0]
        if key in seen: continue
        seen.add(key)
        out.append({'title':title, 'image':image, 'thumbnail':item.get('thumbnail') or image, 'url':url, 'source':item.get('source','')})
        if len(out) >= limit: break
    return out, []

@app.get('/')
def home():
    return render_template('index.html')

@app.post('/api/search')
def api_search():
    data = request.get_json(silent=True) or {}
    query = str(data.get('query','')).strip()
    if not query: return jsonify({'error':'Enter an aircraft, aerospace topic, or engineering subject.'}), 400
    try:
        web_limit = max(1, min(1000, int(data.get('web_limit',150))))
        image_limit = max(1, min(1000, int(data.get('image_limit',150))))
    except (TypeError, ValueError):
        return jsonify({'error':'Result limits must be whole numbers.'}), 400
    region = str(data.get('region','wt-wt'))
    if region not in {'wt-wt','ph-en','us-en','uk-en'}: region='wt-wt'
    safe = str(data.get('safesearch','moderate'))
    if safe not in {'moderate','strict','off'}: safe='moderate'
    web, web_errors = search_web(query, region, safe, web_limit)
    research_limit = max(20, min(100, web_limit))
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_oa = pool.submit(research_openalex, query, research_limit)
        f_cr = pool.submit(research_crossref, query, research_limit)
        f_img = pool.submit(search_images, query, region, safe, image_limit)
        oa, oa_error = f_oa.result()
        cr, cr_error = f_cr.result()
        images, image_errors = f_img.result()
    research = []
    seen = set()
    for item in oa + cr:
        key = item.get('doi') or item.get('href')
        if key and key not in seen:
            seen.add(key); research.append(item)
    research.sort(key=lambda x: (x.get('year') or 0), reverse=True)
    drawings, drawing_errors = search_commons_drawings(query, min(120, max(30, image_limit)))
    errors = web_errors + image_errors + drawing_errors
    if oa_error: errors.append(oa_error)
    if cr_error: errors.append(cr_error)
    return jsonify({'query':query, 'web_results':web, 'research_results':research[:research_limit], 'image_results':images, 'drawing_results':drawings, 'errors':errors[:6], 'provider_summary':{'web':'DuckDuckGo with aerospace-specific query expansion and relevance filtering','research':'OpenAlex + Crossref','images':'DuckDuckGo Images','drawings':'Wikimedia Commons file namespace with drawing/diagram filtering'}})

@app.post('/api/extract')
def api_extract():
    data = request.get_json(silent=True) or {}
    url = str(data.get('url','')).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {'http','https'} or not parsed.hostname:
        return jsonify({'error':'Enter a valid public HTTP(S) URL.'}), 400
    host = parsed.hostname.lower()
    if host in {'localhost','127.0.0.1','::1'} or host.endswith('.local'):
        return jsonify({'error':'Local addresses cannot be fetched.'}), 400
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(host))
        if not ip.is_global: return jsonify({'error':'Private network addresses cannot be fetched.'}), 400
    except ValueError:
        pass
    except Exception:
        pass
    try:
        response = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        for tag in soup(['script','style','nav','footer','header','noscript','form','svg']): tag.decompose()
        main = soup.find('main') or soup.find('article') or soup.body or soup
        parts = [re.sub(r'\s+',' ',node.get_text(' ',strip=True)) for node in main.find_all(['h1','h2','h3','p','li'])]
        text = '\n\n'.join(p for p in parts if len(p)>45)
        if not text: text = re.sub(r'\s+',' ',main.get_text(' ',strip=True))
        return jsonify({'text':text[:24000] or 'No readable page text was found. The source may require JavaScript or block automated access.'})
    except Exception as exc:
        return jsonify({'error':f'Could not extract this page: {exc}. Open the original source directly.'}), 502

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
