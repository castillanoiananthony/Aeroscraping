# Aeroscraping — Python Edition

Aeroscraping is a responsive Flask + HTML/CSS/JavaScript aerospace research discovery website. This edition replaces the PHP shared-hosting build and tightens search relevance so results are about the aircraft or aerospace topic the user entered.

## How results stay on-topic (aircraft identification)

Every search first identifies **which aircraft** was meant, using the English Wikipedia article that carries an aircraft infobox (`aircraft_identity.py`). `F-16` becomes *General Dynamics F-16 Fighting Falcon*, `747` becomes *Boeing 747*, and `Warthog` becomes *Fairchild Republic A-10 Thunderbolt II*. List, variant, operator and accident articles are skipped. Every tab is then filtered against that aircraft's names and designations:

- **Matching ignores spacing and hyphens but rejects other numbers.** `F-16`, `F16` and `F-16C` match, but `F-160`, `CF-16` and text that merely contains "16" do not.
- **Web results** must name the aircraft. Shops, classified ads, pin boards and other-language Wikipedia copies are dropped. Reference, government, manufacturer and established aviation sources rank first, and the aircraft's Wikipedia article always leads.
- **Images** come from the photos in the aircraft's Wikipedia article (only those whose caption names it), then its own Wikimedia Commons category, then NASA images whose *title* names it, then image search whose *title* names it. NASA links now open the NASA details page, not the raw `collection.json`.
- **Research** papers must name the aircraft in the title or abstract. Title matches are listed first, and the providers' relevance order is kept rather than re-sorting by year.
- **Technical drawings** come from Wikimedia Commons (four grouped queries covering all report topics), NASA NTRS and image search. Image-search candidates need a real drawing term (three-view, blueprint, cutaway, planform…), and "wiring diagram" content farms are rejected.
- **Plane Information** works for any identified aircraft: a Wikipedia overview, lead photo and the full *Specifications* table. Curated dataset values are also shown for the 90 aircraft in `aircraft_dataset.json`.
- **Airfoil** uses the curated UIUC mapping first, then the airfoil stated in the aircraft's published specification table (e.g. F-16 → NACA 64A204). It never guesses.

If a search is not an aircraft (e.g. `wing spar design`), every significant word of the query must appear in a result instead.

### API contact (recommended for deployment)

Wikimedia, OpenAlex and Crossref rate-limit anonymous clients more aggressively. Set an environment variable with a contact email or URL on the host (Render → *Environment*):

```
AEROSCRAPING_CONTACT=you@example.com
```

It is added to the app's User-Agent. Without it the app still works, but it is more likely to hit HTTP 429 rate limits.

## Earlier build notes

- Aerospace-context query expansion for general web search, followed by relevance filtering. Retail/support results unrelated to the requested aircraft are rejected.
- Research discovery from **OpenAlex** and **Crossref**, including titles, authors, years, DOI links, and abstracts when available.
- Image discovery through DuckDuckGo Images with aircraft/aerospace context and relevance checks.
- Technical drawings from the **Wikimedia Commons file namespace only**. Results must match the requested topic and include drawing/diagram evidence such as three-view, orthographic, blueprint, airfoil, planform, cross-section, schematic, spar/rib, or empennage terms. Ordinary support pages and unrelated web pages are not accepted as drawings.
- The six drawing groups are searched separately: three-view/general arrangement, airfoil profile, wing planform, wing internal structure, fuselage cross-section, and empennage/control surfaces.
- Responsive interface, developer profile, aircraft favicon, 150-result defaults, and public-page text extraction retained.

## Local setup on Arch Linux

Open a terminal and run:

```bash
cd ~/Downloads
unzip -o Aeroscraping_Python.zip
cd Aeroscraping_Python
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000**. Keep the terminal running while you use the website. Stop it with `Ctrl+C`.

If you already extracted the folder, start at `cd ~/Downloads/Aeroscraping_Python` and do not unzip it again.

## Deploying publicly while keeping the Hostinger domain

Your shown Hostinger plan is shared hosting (10 GB disk, 1 GB RAM, 1 CPU), not a VPS. It can serve PHP/static files but does not provide a persistent Flask application runtime. Uploading this Flask folder into `public_html` will not run it.

You have two practical choices:

1. **Use a Python application host** such as Render for the Flask app, then add `aeroscraping.online` as a custom domain in that host and follow the exact DNS records it supplies. Keep Hostinger as the domain registrar/DNS manager. This project includes `render.yaml` and `Procfile` for deployment. A free web-service tier may sleep or have usage limits; check the provider's current plan details before relying on it.
2. **Upgrade to a Hostinger VPS** and deploy Flask with Gunicorn + Nginx. This gives you server control but costs more and requires server administration.

Do not point the live domain to a guessed IP or DNS record. Copy the exact CNAME/A records shown by the Python host's custom-domain setup.

## Search quality notes

Search providers do not guarantee a requested number of results. A 150-result limit is a maximum, not a promise. Relevance filtering intentionally returns fewer results rather than filling the page with unrelated material. A technical-drawing tab may be empty for a topic if Commons has no matching diagram files; this is preferable to falsely labelling ordinary images or unrelated pages as engineering drawings.

OpenAlex and Crossref provide scholarly metadata, not guaranteed full-text access. Abstracts are shown only when supplied by the provider. Page extraction is best-effort and does not bypass paywalls, logins, or anti-bot protections.


## Aeroscraping aircraft research workspace update

- Unified aircraft research search expands one query across the ASE 111 report areas.
- Technical drawings and images are shown as visual previews when the provider returns image URLs.
- Search suggestions are populated from `aircraft_dataset.json`.
- Plane Information shows the curated record when the aircraft is in the dataset, and the Wikipedia overview and specifications for any identified aircraft.
- Airfoil tab only reports aircraft-to-airfoil associations that have a source-backed mapping. It does not invent a generic airfoil assignment.
- The curated aircraft catalogue contains 90 aircraft across Airbus, Boeing, experimental X-aircraft, US and European fighters, Russian and Chinese aircraft, JF-17, and unmanned aircraft.

Data notes:
- Aircraft dimensions and performance values are approximate reference figures and can vary by variant/configuration. Verify with manufacturer documents and applicable type-certificate/authority data before formal use.
- UIUC Airfoil Coordinates Database: https://m-selig.web.engr.illinois.edu/ads/coord_database.html
- FAA Aircraft Characteristics Database: https://www.faa.gov/airports/engineering/aircraft_char_database
