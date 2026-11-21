"""
Aircraft identity resolution for Aeroscraping.

Every search first works out *which aircraft* the user means by finding the
matching English Wikipedia article (one that carries an "Infobox aircraft").
That gives a canonical name, safe aliases (e.g. "F-16"), a summary, the
specification table, and editor-curated photographs. All other providers are
then filtered against those aliases, so results are about the requested
aircraft instead of anything that happens to contain the same number.
"""
import re
import time
from functools import lru_cache
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# app.py replaces this with its own configured session at import time.
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'Aeroscraping/3.0 (aerospace research discovery)'})

WIKI_API = 'https://en.wikipedia.org/w/api.php'
WIKI_REST = 'https://en.wikipedia.org/api/rest_v1'
COMMONS_API = 'https://commons.wikimedia.org/w/api.php'
WIKIDATA_API = 'https://www.wikidata.org/w/api.php'

# Designations such as F-16, A320, MQ-9, Su-57, JF-17, C-130J, B-2, An-225.
DESIGNATION_RE = re.compile(r'\b([A-Z][A-Za-z]{0,3}-?\d{1,4}[A-Z]{0,2})\b')
NON_PHOTO_FILE = re.compile(
    r'flag|logo|icon|roundel|insignia|emblem|badge|seal|coat[_ ]of[_ ]arms|'
    r'map|locator|commons-|wiki|symbol|question|edit-clear|padlock|'
    r'disambig|portal|signature|patch',
    re.I
)


def _get(url, params=None, timeout=20, as_json=True):
    """GET with a short, polite retry on Wikimedia rate limits / server errors."""
    for attempt in range(3):
        response = SESSION.get(url, params=params, timeout=timeout)
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            try:
                wait = float(response.headers.get('Retry-After', 1))
            except ValueError:
                wait = 1
            time.sleep(min(max(wait, 0.5), 4))
            continue
        response.raise_for_status()
        return response.json() if as_json else response.text
    response.raise_for_status()


# --------------------------------------------------------------------------
# Subject matching
# --------------------------------------------------------------------------
def _alias_pattern(alias):
    """
    Match an aircraft name/designation regardless of spacing or hyphenation
    ("F-16", "F16", "f 16") but never inside a longer number ("F-160") or a
    different designation ("CF-16"). Variant suffixes ("F-16C", "747-400",
    "A320neo") are accepted.
    """
    parts = re.findall(r'[a-z]+|\d+', alias.lower())
    if not parts:
        return None
    body = r'[\s\-_/.]*'.join(re.escape(p) for p in parts)
    tail = r'(?!\d)' if parts[-1].isdigit() else r'(?![a-z])'
    return re.compile(r'(?<![a-z0-9])' + body + tail, re.I)


class SubjectMatcher:
    """Decides whether a piece of text is about the requested aircraft/topic."""

    def __init__(self, aliases, required_words=()):
        self.aliases = list(dict.fromkeys(a for a in aliases if a))
        self.patterns = [p for p in (_alias_pattern(a) for a in self.aliases) if p]
        # Topic mode (no aircraft identified): every significant word must occur.
        self.word_patterns = [
            re.compile(r'(?<![a-z0-9])' + re.escape(w), re.I) for w in required_words
        ]

    def matches(self, *texts):
        text = ' '.join(str(t or '') for t in texts)
        if not text.strip():
            return False
        if self.word_patterns:
            return all(p.search(text) for p in self.word_patterns)
        return any(p.search(text) for p in self.patterns)

    def strength(self, title, body=''):
        """2 = named in the title, 1 = only in the body, 0 = not mentioned."""
        if self.matches(title):
            return 2
        if self.matches(body):
            return 1
        return 0


def _clean_title(title):
    return re.sub(r'\s*\([^)]*\)\s*$', '', title or '').strip()


def build_aliases(query, title=None):
    """
    Safe aliases only: the user's query, the article title, the title minus
    the manufacturer, and letter+digit designations found in the title.
    Bare numbers ("747") are never used alone because they match anything.
    """
    candidates = [query]
    if title:
        clean = _clean_title(title)
        candidates.append(clean)
        candidates.append(re.sub(r'\s+family$', '', clean, flags=re.I))
        words = clean.split()
        # "General Dynamics F-16 Fighting Falcon" -> "F-16 Fighting Falcon"
        # and "Boeing 787 Dreamliner" -> "Boeing 787"
        for i in range(1, len(words)):
            if re.search(r'\d', words[i]):
                candidates.append(' '.join(words[i:]))
                candidates.append(' '.join(words[:i + 1]))
                break
        candidates.extend(m.group(1) for m in DESIGNATION_RE.finditer(clean))
    aliases = []
    for cand in candidates:
        cand = _clean_title(str(cand or '')).strip(' "\'')
        if len(cand) < 2 or not re.search(r'[A-Za-z]', cand):
            continue
        # A short plain word ("Su", "MQ", "jet") is too ambiguous on its own.
        if not re.search(r'\d', cand) and len(cand) < 5:
            continue
        if cand.lower() not in (a.lower() for a in aliases):
            aliases.append(cand)
    return aliases


# --------------------------------------------------------------------------
# Wikipedia resolution
# --------------------------------------------------------------------------
def _is_aircraft_article(title, wikitext):
    if re.match(r'(List|Lists|Timeline|Comparison) of', title, re.I):
        return False
    if re.search(r'\b(variants|operators|operational history|accidents|survivors)\b',
                 title, re.I):
        return False
    m = re.search(r'\{\{\s*Infobox[ _]aircraft([^\n|}]*)', wikitext, re.I)
    if not m:
        return False
    kind = m.group(1).lower()
    # "Infobox aircraft occurrence" = a crash article; "begin"/"type" are fine.
    return 'occurrence' not in kind and 'accident' not in kind


@lru_cache(maxsize=256)
def _resolve_cached(query):
    """Raises on network failure so failures are not cached."""
    data = _get(WIKI_API, {
        'action': 'query', 'format': 'json', 'formatversion': 2,
        'generator': 'search', 'gsrsearch': f'{query} aircraft',
        'gsrlimit': 8, 'prop': 'revisions|pageprops',
        'rvprop': 'content', 'rvslots': 'main', 'rvsection': 0,
        'ppprop': 'wikibase_item', 'redirects': 1
    })
    pages = sorted((data.get('query') or {}).get('pages') or [],
                   key=lambda p: p.get('index', 99))
    query_matcher = SubjectMatcher(build_aliases(query))
    chosen = None
    for page in pages:
        try:
            text = page['revisions'][0]['slots']['main']['content']
        except (KeyError, IndexError):
            continue
        if not _is_aircraft_article(page.get('title', ''), text):
            continue
        # The article must actually be about what was typed: the query has to
        # appear in its title or lead section ("747" is checked via the title).
        if query_matcher.patterns:
            looks_like_name = bool(re.search(r'\d', query)) or query[:1].isupper()
            in_title = query_matcher.matches(page['title'])
            in_lead = looks_like_name and query_matcher.matches(text[:8000])
            if not (in_title or in_lead):
                continue
        elif not re.search(r'(?<!\d)' + re.escape(query) + r'(?!\d)', page['title']):
            continue
        chosen = page
        break
    if not chosen:
        return None

    title = chosen['title']
    try:
        summary = _get(f"{WIKI_REST}/page/summary/{quote(title.replace(' ', '_'), safe='')}")
    except Exception:
        summary = {}
    return {
        'title': title,
        'url': f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
        'qid': (chosen.get('pageprops') or {}).get('wikibase_item'),
        'description': summary.get('description', ''),
        'extract': summary.get('extract', ''),
        'thumbnail': ((summary.get('originalimage') or summary.get('thumbnail') or {})
                      .get('source', '')),
        'aliases': build_aliases(query, title),
    }


def resolve_aircraft(query):
    """Return the Wikipedia identity of the aircraft, or None for non-aircraft topics."""
    query = re.sub(r'\s+', ' ', (query or '').strip())
    if not query:
        return None
    try:
        return _resolve_cached(query)
    except Exception:
        return None


TOPIC_STOPWORDS = {
    'the', 'and', 'for', 'with', 'about', 'of', 'on', 'in', 'to', 'an',
    'aircraft', 'airplane', 'aeroplane', 'plane', 'aerospace', 'aviation',
    'information', 'research', 'design', 'analysis', 'study'
}


def subject_matcher(query, identity=None):
    """
    Aircraft identified -> match any of its aliases.
    Designation-like query not identified (e.g. Wikipedia unreachable) -> the
    query itself as an alias. Plain topic ("wing spar design") -> every
    significant word must appear.
    """
    if identity:
        return SubjectMatcher(identity['aliases'])
    if re.search(r'\d', query):
        return SubjectMatcher(build_aliases(query))
    words = [w for w in re.findall(r'[a-z0-9]+', query.lower())
             if len(w) >= 3 and w not in TOPIC_STOPWORDS]
    if not words:
        words = [w for w in re.findall(r'[a-z0-9]+', query.lower()) if len(w) >= 3]
    return SubjectMatcher([], required_words=words or [query.lower()])


def search_name_for(query, identity):
    """The phrase sent to search engines: what the user typed if it names the
    aircraft, otherwise the article's name ("747" -> "Boeing 747",
    "Warthog" -> "Fairchild Republic A-10 Thunderbolt II")."""
    if not identity:
        return query
    title = _clean_title(identity['title'])
    title = re.sub(r'\s+family$', '', title, flags=re.I)
    if re.search(r'\d', query) and SubjectMatcher(build_aliases(query)).matches(title):
        return query
    return title


# --------------------------------------------------------------------------
# Specifications (rendered Wikipedia "Specifications" section)
# --------------------------------------------------------------------------
@lru_cache(maxsize=128)
def _specs_cached(title):
    html = _get(f"{WIKI_REST}/page/html/{quote(title.replace(' ', '_'), safe='')}",
                timeout=30, as_json=False)
    soup = BeautifulSoup(html, 'html.parser')
    heading = next(
        (h for h in soup.find_all(['h2', 'h3'])
         if 'specification' in h.get_text().lower()),
        None
    )
    if not heading:
        return {'heading': '', 'groups': []}
    section = heading.find_parent('section') or heading.parent
    for sup in section.find_all('sup'):
        sup.decompose()
    groups, current = [], {'name': 'General characteristics', 'rows': []}
    seen = set()
    for node in section.find_all(['p', 'li']):
        if node.name == 'p':
            label = node.get_text(' ', strip=True)
            if label and len(label) < 40 and node.find('b'):
                if current['rows']:
                    groups.append(current)
                current = {'name': label.rstrip(':'), 'rows': []}
            continue
        if node.find_parent('li'):
            continue  # nested weapon sub-lists are already in the parent item
        text = re.sub(r'\s+', ' ', node.get_text(' ', strip=True))
        text = re.sub(r'\s+([,.;:)])', r'\1', text).replace('( ', '(')
        if ':' not in text:
            continue
        label, value = (part.strip() for part in text.split(':', 1))
        if not label or not value or len(label) > 40 or (label, value) in seen:
            continue
        seen.add((label, value))
        current['rows'].append({'label': label, 'value': value[:400]})
    if current['rows']:
        groups.append(current)
    return {'heading': heading.get_text(' ', strip=True), 'groups': groups}


def wikipedia_specs(title):
    """Specification rows from the aircraft's Wikipedia article."""
    try:
        return _specs_cached(title)
    except Exception:
        return {'heading': '', 'groups': []}


def spec_value(specs, *labels):
    labels = {label.lower() for label in labels}
    for group in specs.get('groups', []):
        for row in group['rows']:
            if row['label'].lower() in labels:
                return row['value']
    return ''


# --------------------------------------------------------------------------
# Photographs
# --------------------------------------------------------------------------
IMAGEINFO = {
    'prop': 'imageinfo', 'iiprop': 'url|mime|size|extmetadata',
    'iiurlwidth': 900, 'iiextmetadatafilter': 'ImageDescription|LicenseShortName'
}


def _commons_file_record(page, source):
    info = (page.get('imageinfo') or [{}])[0]
    mime = (info.get('mime') or '').lower()
    name = page.get('title', '').replace('File:', '')
    if mime not in ('image/jpeg', 'image/png', 'image/webp'):
        return None  # SVGs are mostly flags/diagrams; drawings have their own tab
    if NON_PHOTO_FILE.search(name) or (info.get('width') or 0) < 400:
        return None
    meta = info.get('extmetadata') or {}
    caption = BeautifulSoup(
        (meta.get('ImageDescription') or {}).get('value', ''), 'html.parser'
    ).get_text(' ', strip=True)
    title = re.sub(r'\.(jpe?g|png|webp)$', '', name, flags=re.I)
    return {
        'title': (caption or title)[:160],
        'image': info.get('thumburl') or info.get('url'),
        'thumbnail': info.get('thumburl') or info.get('url'),
        'url': info.get('descriptionurl'),
        'href': info.get('descriptionurl'),
        'source': source,
        'license': (meta.get('LicenseShortName') or {}).get('value', ''),
        'description': caption[:600],
    }


@lru_cache(maxsize=128)
def _article_photos_cached(title):
    data = _get(WIKI_API, {
        'action': 'query', 'format': 'json', 'formatversion': 2,
        'generator': 'images', 'titles': title, 'gimlimit': 'max', **IMAGEINFO
    }, timeout=25)
    out = []
    for page in (data.get('query') or {}).get('pages') or []:
        rec = _commons_file_record(page, 'Wikipedia article')
        if rec:
            out.append(rec)
    return tuple(out)


def article_photos(title):
    """Photographs placed in the aircraft's Wikipedia article by its editors."""
    try:
        return list(_article_photos_cached(title))
    except Exception:
        return []


@lru_cache(maxsize=128)
def _category_photos_cached(qid):
    claims = _get(WIKIDATA_API, {
        'action': 'wbgetclaims', 'format': 'json', 'entity': qid, 'property': 'P373'
    }, timeout=15).get('claims', {}).get('P373') or []
    if not claims:
        return ()
    category = claims[0]['mainsnak']['datavalue']['value']
    data = _get(COMMONS_API, {
        'action': 'query', 'format': 'json', 'formatversion': 2,
        'generator': 'search', 'gsrnamespace': 6, 'gsrlimit': 60,
        'gsrsearch': f'incategory:"{category}" filetype:bitmap', **IMAGEINFO
    }, timeout=25)
    pages = sorted((data.get('query') or {}).get('pages') or [],
                   key=lambda p: p.get('index', 999))
    out = []
    for page in pages:
        rec = _commons_file_record(page, f'Wikimedia Commons · {category}')
        if rec:
            out.append(rec)
    return tuple(out)


def category_photos(qid):
    """Photos from the aircraft's own Wikimedia Commons category."""
    if not qid:
        return []
    try:
        return list(_category_photos_cached(qid))
    except Exception:
        return []
