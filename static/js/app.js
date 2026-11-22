const $ = id => document.getElementById(id);

// ---------- Settings sheet ----------
const settingsDialog = $('settingsDialog');
$('settingsOpen').addEventListener('click', () => settingsDialog.showModal());
settingsDialog.addEventListener('click', e => { if (e.target === settingsDialog) settingsDialog.close(); });

// ---------- Engine spool-up loader ----------
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const loader = (() => {
  const root = $('searchLoader');
  const fan = root.querySelector('.engine-fan');
  const engine = root.querySelector('.engine');
  const n1 = $('n1Value'), stage = $('loaderStage'), bar = $('loaderBar');
  const stages = [
    'Identifying the aircraft…',
    'Searching web sources…',
    'Querying NASA NTRS, OpenAlex and Crossref…',
    'Collecting aircraft images…',
    'Finding technical drawings…',
    'Checking airfoil references…',
    'Reading the specification tables…'
  ];
  let spin = null, raf = 0, t0 = 0, level = 0;
  const maxRate = () => (reduceMotion.matches ? 0.12 : 1);
  function render(value) {
    n1.textContent = Math.round(value);
    bar.style.transform = `scaleX(${value / 100})`;
    engine.style.setProperty('--glow', (0.25 + value / 130).toFixed(2));
  }
  function tick(now) {
    const t = now - t0;
    // Spool up quickly, then settle towards ~92% N1 while the search runs.
    level = 92 * (1 - Math.exp(-t / 2600));
    spin.playbackRate = Math.max(0.04, maxRate() * (1 - Math.exp(-t / 1100)));
    render(level);
    const i = Math.min(stages.length - 1, Math.floor(t / 1800));
    if (stage.textContent !== stages[i]) stage.textContent = stages[i];
    raf = requestAnimationFrame(tick);
  }
  return {
    start() {
      cancelAnimationFrame(raf);
      root.classList.remove('is-leaving');
      root.hidden = false;
      stage.textContent = stages[0];
      render(0);
      spin?.cancel();
      // One revolution every 0.42s at full power.
      spin = fan.animate([{ transform: 'rotate(0deg)' }, { transform: 'rotate(360deg)' }], { duration: 420, iterations: Infinity });
      spin.playbackRate = 0.04;
      t0 = performance.now();
      raf = requestAnimationFrame(tick);
    },
    stop(ok = true) {
      cancelAnimationFrame(raf);
      if (ok) { render(100); stage.textContent = 'Results ready'; }
      const from = spin ? spin.playbackRate : 0, s0 = performance.now();
      const wind = now => {
        const k = Math.min(1, (now - s0) / 260);
        if (spin) spin.playbackRate = Math.max(0.02, from * (1 - k));
        if (k < 1) requestAnimationFrame(wind);
      };
      requestAnimationFrame(wind);
      setTimeout(() => root.classList.add('is-leaving'), ok ? 120 : 0);
      setTimeout(() => { root.hidden = true; root.classList.remove('is-leaving'); spin?.cancel(); spin = null; }, ok ? 360 : 240);
    }
  };
})();

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}
function safeUrl(value) {
  try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; }
  catch { return ''; }
}
const SVG_NS = 'http://www.w3.org/2000/svg';
function icon(name) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', 'icon'); svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(SVG_NS, 'use'); use.setAttribute('href', `#i-${name}`);
  svg.append(use); return svg;
}
function addLink(parent, label, href, className='source-link') {
  const url = safeUrl(href);
  if (!url) return;
  const a = el('a', className, label); a.href=url; a.target='_blank'; a.rel='noopener noreferrer';
  a.append(icon('external')); parent.append(a);
}
function metaRow(tag, parts) {
  const row = el('div', 'result-meta');
  if (tag) row.append(el('span', 'tag', tag));
  parts.filter(Boolean).forEach(p => row.append(el('span', '', p)));
  return row;
}
function resultCard(item, index, category) {
  const card = el('article', 'result-card');
  const type = item.drawing_type || item.category || category;
  const title = item.title || `Result ${index + 1}`;
  const url = safeUrl(item.href || item.url || '');
  const h = el('h3');
  if (url) { const a=el('a','',title); a.href=url; a.target='_blank'; a.rel='noopener noreferrer'; h.append(a); }
  else h.textContent=title;
  card.append(metaRow(type,[item.authors,item.year,item.source,item.license]),h);
  if(url) card.append(el('div','result-url',url));
  if(category==='Technical drawings' || category==='Technical drawings / engineering reference') {
    const previewUrl=safeUrl(item.image || item.thumbnail || '');
    if(previewUrl) {
      const img=document.createElement('img'); img.className='drawing-preview'; img.src=previewUrl; img.alt=title; img.loading='lazy';
      img.onerror=()=>{img.remove();}; card.append(img);
    }
  }
  card.append(el('p','result-summary',item.body || item.description || item.snippet || 'No description was supplied by the source.'));
  if(url) {
    const actions=el('div','result-actions');
    addLink(actions,'Open original source',url);
    const button=el('button','text-button'); button.type='button';
    const label=el('span','','Extract page text'); button.append(icon('text'),label);
    const output=el('div','extracted'); output.hidden=true;
    let loaded=false;
    button.addEventListener('click',async()=>{
      if(loaded){ output.hidden=!output.hidden; label.textContent=output.hidden?'Show page text':'Hide page text'; return; }
      button.disabled=true; label.textContent='Extracting…';
      try{
        const response=await fetch('/api/extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
        const data=await response.json(); output.textContent=data.text||data.error||'No text available.';
        loaded=!!data.text;
      }catch(e){output.textContent=`Could not extract this page: ${e.message}`;}
      output.hidden=false; button.disabled=false; label.textContent=loaded?'Hide page text':'Try extracting again';
    });
    actions.append(button); card.append(actions,output);
  }
  return card;
}
function imageCard(item,index) {
  const card=el('article','image-card');
  const image=safeUrl(item.image||item.thumbnail||'');
  const target=safeUrl(item.url||item.href||'');
  if(image){
    const link=el('a'); if(target){link.href=target;link.target='_blank';link.rel='noopener noreferrer';}
    const img=document.createElement('img');img.src=image;img.alt=item.title||`Image ${index+1}`;img.loading='lazy';img.onerror=()=>img.remove();link.append(img);card.append(link);
  }
  card.append(el('p','',item.title||`Image result ${index+1}`));
  const credit=[item.source,item.license].filter(Boolean).join(' · ');
  if(credit) card.append(el('small','image-source',credit));
  return card;
}
function fillPanel(id,items,kind){
  const panel=$(id);panel.replaceChildren();
  if(!items||!items.length){
    panel.append(el('div','empty','No verified results were returned for this category.'));
    if(!searchHasAnyResults){
      const box=el('section','recommended-sources');
      box.append(el('h3','', 'Reliable aerospace sources'));
      box.append(el('p','', 'No searchable results were returned across the available providers. Explore these trusted collections directly.'));
      const links=[
        ['NASA Technical Reports Server (NTRS)','https://ntrs.nasa.gov/'],
        ['NASA Image and Video Library','https://images.nasa.gov/'],
        ['FAA Aircraft Characteristics Database','https://www.faa.gov/airports/engineering/aircraft_char_database'],
        ['EASA Type Certificates and Data Sheets','https://www.easa.europa.eu/en/document-library/type-certificates'],
        ['Smithsonian National Air and Space Museum Technical Drawings','https://airandspace.si.edu/archives/archival-collections/technical-scale-drawings'],
        ['AAHS 3-View Library','https://aahs-online.org/resources/search_3vu_lib.php'],
        ['UIUC Airfoil Coordinates Database','https://m-selig.ae.illinois.edu/ads/coord_database.html'],
        ['UIUC Aircraft-to-Airfoil Guide','https://m-selig.web.engr.illinois.edu/ads/aircraft.html']
      ];
      links.forEach(([label,url])=>addLink(box,label,url));
      panel.append(box);
    }
    return;
  }
  if(kind==='images'){const grid=el('div','image-grid');items.forEach((it,i)=>grid.append(imageCard(it,i)));panel.append(grid);}
  else items.forEach((it,i)=>panel.append(resultCard(it,i,kind)));
}
const tabs=[...document.querySelectorAll('.tab')];
function setTab(name,focus=false){
  tabs.forEach(t=>{
    const on=t.dataset.tab===name;
    t.setAttribute('aria-selected',String(on)); t.tabIndex=on?0:-1;
    if(on&&focus)t.focus();
    if(on)t.scrollIntoView({block:'nearest',inline:'nearest'});
  });
  tabs.forEach(t=>{ $(`panel-${t.dataset.tab}`).hidden=t.dataset.tab!==name;});
}
tabs.forEach((t,i)=>{
  t.addEventListener('click',()=>setTab(t.dataset.tab));
  t.addEventListener('keydown',e=>{
    const step={ArrowRight:1,ArrowLeft:-1}[e.key];
    if(step){e.preventDefault();setTab(tabs[(i+step+tabs.length)%tabs.length].dataset.tab,true);}
    else if(e.key==='Home'||e.key==='End'){e.preventDefault();setTab(tabs[e.key==='Home'?0:tabs.length-1].dataset.tab,true);}
  });
});

let lastQuery='';
let searchHasAnyResults=false;
let aircraftCatalog=[];
async function loadCatalog(){
  try{
    const response=await fetch('/api/aircraft');const data=await response.json();
    aircraftCatalog=data.aircraft||[];
    const select=$('aircraftSelect');
    const grouped=new Map();
    aircraftCatalog.sort((a,b)=>a.manufacturer.localeCompare(b.manufacturer)||a.name.localeCompare(b.name));
    aircraftCatalog.forEach(a=>{
      if(!grouped.has(a.manufacturer))grouped.set(a.manufacturer,[]);
      grouped.get(a.manufacturer).push(a);
    });
    grouped.forEach((items,manufacturer)=>{
      const group=document.createElement('optgroup');group.label=manufacturer;
      items.forEach(a=>{
        const option=document.createElement('option');option.value=a.name;
        option.textContent=`${a.name}${a.icao?' — '+a.icao:''}`;
        group.append(option);
      });
      select.append(group);
    });
  }catch(e){console.warn('Aircraft dataset could not be loaded',e);}
}
loadCatalog();
$('aircraftSelect').addEventListener('change',event=>{
  if(event.target.value)$('query').value=event.target.value;
});

function infoValue(value,unit=''){
  return value===null||value===undefined||value===''?'Not listed in current dataset':`${value}${unit}`;
}
function wikipediaCard(w){
  const card=el('section','plane-info-card wiki-card');
  card.append(metaRow('Encyclopedia overview',[w.description]));
  const head=el('div','wiki-head');
  const thumb=safeUrl(w.thumbnail||'');
  if(thumb){const img=document.createElement('img');img.className='wiki-thumb';img.src=thumb;img.alt=w.title;img.loading='lazy';img.onerror=()=>img.remove();head.append(img);}
  const text=el('div','wiki-text');
  text.append(el('h2','',w.title));
  if(w.extract)text.append(el('p','result-summary',w.extract));
  const links=el('div','source-links');addLink(links,'Read the full Wikipedia article',w.url);text.append(links);
  head.append(text);card.append(head);
  const groups=(w.specs&&w.specs.groups)||[];
  if(groups.length){
    card.append(el('h3','spec-heading',w.specs.heading||'Specifications'));
    groups.forEach(g=>{
      card.append(el('h4','spec-group',g.name));
      const table=el('dl','spec-table');
      g.rows.forEach(r=>{table.append(el('dt','',r.label),el('dd','',r.value));});
      card.append(table);
    });
    card.append(el('p','source-disclaimer','Specifications as published in the article’s Specifications section (usually one variant). Follow the article’s cited references before formal use.'));
  }
  return card;
}
async function renderPlaneInfo(query){
  const panel=$('panel-plane');panel.replaceChildren(el('div','loading','Loading aircraft information…'));
  try{
    const response=await fetch(`/api/aircraft/lookup?query=${encodeURIComponent(query)}`);
    const result=await response.json();panel.replaceChildren();
    if(!response.ok||!result.available){
      panel.append(el('div','dataset-unavailable',result.message||`“${query}” could not be identified as an aircraft.`));
      return;
    }
    if(result.wikipedia)panel.append(wikipediaCard(result.wikipedia));
    const a=result.aircraft;
    if(!a)return;
    const card=el('section','plane-info-card');
    card.append(metaRow('Curated record',[a.manufacturer,a.category]));
    card.append(el('h2','',a.name));
    card.append(el('p','result-summary','Curated reference record. Values are approximate and variant-dependent; verify against the manufacturer’s current documentation and applicable type certificate.'));
    const grid=el('div','spec-grid');
    const specs=[
      ['ICAO type designator',infoValue(a.icao)],
      ['Wingspan',infoValue(a.wingspan_m,' m')],
      ['Overall length',infoValue(a.length_m,' m')],
      ['Overall height',infoValue(a.height_m,' m')],
      ['Maximum takeoff mass',infoValue(a.mtow_t,' t')],
      ['Typical cruise speed',infoValue(a.cruise_kmh,' km/h')],
      ['Published range reference',infoValue(a.range_km,' km')]
    ];
    specs.forEach(([label,value])=>{const cell=el('div','spec-cell');cell.append(el('small','',label),el('strong','',value));grid.append(cell);});
    card.append(grid);
    card.append(el('p','source-disclaimer',a.source_note||'Confirm all values from primary technical documentation.'));
    const sources=el('div','source-links');
    addLink(sources,'FAA Aircraft Characteristics Database','https://www.faa.gov/airports/engineering/aircraft_char_database');
    addLink(sources,'Manufacturer aircraft information',manufacturerUrl(a.manufacturer));
    card.append(sources);panel.append(card);
  }catch(e){panel.replaceChildren(el('div','empty',`Could not load the aircraft record: ${e.message}`));}
}
function manufacturerUrl(m){
  if(/Airbus/i.test(m))return 'https://www.airbus.com/en/products-services/commercial-aircraft';
  if(/Boeing/i.test(m))return 'https://www.boeing.com/commercial';
  if(/Lockheed/i.test(m))return 'https://www.lockheedmartin.com/';
  if(/Dassault/i.test(m))return 'https://www.dassault-aviation.com/';
  if(/Saab/i.test(m))return 'https://www.saab.com/';
  if(/Embraer/i.test(m))return 'https://www.embraer.com/';
  return 'https://www.faa.gov/airports/engineering/aircraft_char_database';
}
async function renderAirfoil(query){
  const panel=$('panel-airfoil');panel.replaceChildren(el('div','loading','Checking documented aircraft-to-airfoil references…'));
  try{
    const response=await fetch(`/api/airfoil?query=${encodeURIComponent(query)}`);
    const data=await response.json();panel.replaceChildren();
    if(!response.ok||!data.available){panel.append(el('div','dataset-unavailable',data.message||'This aircraft is not available in the dataset.'));return;}
    const card=el('section','plane-info-card');card.append(metaRow('Airfoil reference',[]),el('h2','',data.aircraft),el('p','result-summary',data.message));
    if(data.profiles&&data.profiles.length){
      data.profiles.forEach(profile=>{
        const item=el('article','result-card');item.append(el('h3','',profile.name),el('p','result-summary',profile.evidence));
        addLink(item,profile.file?'Open airfoil coordinate/source file':'Open aircraft-to-airfoil source record',profile.url);card.append(item);
      });
    }else{
      card.append(el('div','empty','No aircraft-specific airfoil has been verified in the current mapping. Aeroscraping will not substitute a generic NACA profile and falsely label it as this aircraft’s wing section.'));
    }
    const sources=el('div','source-links');
    (data.sources||[]).forEach(s=>addLink(sources,s.name,s.url));
    card.append(sources);panel.append(card);
  }catch(e){panel.replaceChildren(el('div','empty',`Airfoil lookup failed: ${e.message}`));}
}

$('searchForm').addEventListener('submit',async event=>{
  event.preventDefault();
  const query=$('query').value.trim();if(!query)return;lastQuery=query;
  const button=$('searchButton'),status=$('status'),results=$('resultsSection');
  const buttonLabel=button.querySelector('span');
  button.disabled=true;buttonLabel.textContent='Searching…';loader.start();status.className='status';status.replaceChildren();results.hidden=true;
  let ok=false;
  try{
    const payload={query,web_limit:Math.max(1,Math.min(1000,Number($('webLimit').value)||150)),image_limit:Math.max(1,Math.min(1000,Number($('imageLimit').value)||150)),region:$('region').value,safesearch:$('safeSearch').value};
    const response=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'Search request failed.');
    const web=data.web_results||[],research=data.research_results||[],images=data.image_results||[],drawings=data.drawing_results||[];
    searchHasAnyResults=[web,research,images,drawings].some(group=>group.length>0);
    $('resultsTitle').textContent=data.aircraft
      ?`Search results for: ${data.aircraft.title}`
      :`Search results for: ${data.query}`;
    $('resultsCounts').textContent=(data.aircraft&&data.aircraft.title.toLowerCase()!==data.query.toLowerCase()?`Identified “${data.query}” as ${data.aircraft.title}. `:'')
      +(data.aircraft?'':'No specific aircraft was identified, so results are matched on your search words. ')
      +`${web.length} web sources · ${research.length} research/report leads · ${images.length} images · ${drawings.length} technical drawing references`;
    $('webCount').textContent=web.length;$('researchCount').textContent=research.length;$('imageCount').textContent=images.length;$('drawingCount').textContent=drawings.length;
    fillPanel('panel-web',web,'General web');fillPanel('panel-research',research,'Research & reports');fillPanel('panel-images',images,'images');fillPanel('panel-drawings',drawings,'Technical drawings');
    await Promise.all([renderAirfoil(query),renderPlaneInfo(query)]);
    results.classList.remove('is-revealed');results.hidden=false;void results.offsetWidth;results.classList.add('is-revealed');
    setTab('web');status.replaceChildren();
    if(data.errors&&data.errors.length)status.textContent=`Some search providers timed out or were unavailable (${data.errors.length} notices). Results from the other sources are still shown.`;
    ok=true;
  }catch(error){status.className='status error';status.textContent=error.message;}
  finally{
    button.disabled=false;buttonLabel.textContent='Search';loader.stop(ok);
    if(ok)setTimeout(()=>results.scrollIntoView({behavior:reduceMotion.matches?'auto':'smooth',block:'start'}),260);
  }
});
