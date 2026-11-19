# Aeroscraping — Python Edition

Aeroscraping is a responsive Flask + HTML/CSS/JavaScript aerospace research discovery website. This edition replaces the PHP shared-hosting build and tightens search relevance so results are about the aircraft or aerospace topic the user entered.

## What changed in this build

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
- Plane Information is limited to aircraft in that curated dataset. Unknown aircraft explicitly return an unavailable-in-dataset message.
- Airfoil tab only reports aircraft-to-airfoil associations that have a source-backed mapping. It does not invent a generic airfoil assignment.
- The initial curated aircraft catalogue contains 65 aircraft across Airbus, Boeing, experimental X-aircraft, US and European fighters, Russian and Chinese aircraft, JF-17, and unmanned aircraft.

Data notes:
- Aircraft dimensions and performance values are approximate reference figures and can vary by variant/configuration. Verify with manufacturer documents and applicable type-certificate/authority data before formal use.
- UIUC Airfoil Coordinates Database: https://m-selig.web.engr.illinois.edu/ads/coord_database.html
- FAA Aircraft Characteristics Database: https://www.faa.gov/airports/engineering/aircraft_char_database
