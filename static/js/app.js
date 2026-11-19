const $ = id => document.getElementById(id);
const appShell = $('appShell');
const sidebar = $('sidebar');

$('sidebarToggle').addEventListener('click', () => {
  const collapsed = appShell.classList.toggle('sidebar-collapsed');
  $('sidebarToggle').setAttribute('aria-expanded', String(!collapsed));
  $('sidebarToggle').setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
  $('sidebarToggle').textContent = collapsed ? '→' : '←';
});

const profileToggle = $('profileToggle');
const profileCard = $('profileCard');
profileToggle.addEventListener('click', () => {
  const opening = profileCard.hidden;
  profileCard.hidden = !opening;
  profileToggle.setAttribute('aria-expanded', String(opening));
  profileToggle.querySelector('.chevron').textContent = opening ? '⌃' : '⌄';
});

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
function addLink(parent, label, href, className='source-link') {
  const url = safeUrl(href);
  if (!url) return;
  const a = el('a', className, label); a.href=url; a.target='_blank'; a.rel='noopener noreferrer'; parent.append(a);
}
function resultCard(item, index, category) {
  const card = el('article', 'result-card');
  const type = item.drawing_type || item.category || category;
  if (type) card.append(el('div', 'category-label', type));
  const title = item.title || `Result ${index + 1}`;
  const url = safeUrl(item.href || item.url || '');
  const h = el('h3');
  if (url) { const a=el('a','',title); a.href=url; a.target='_blank'; a.rel='noopener noreferrer'; h.append(a); }
  else h.textContent=title;
  card.append(h);
  const meta=[item.authors,item.year,item.source,item.license].filter(Boolean).join(' · ');
  if(meta) card.append(el('div','result-url',meta));
  if(url) card.append(el('div','result-url',url));
  if(category==='Technical drawings') {
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
    const button=el('button','text-button','Extract page text'); button.type='button';
    const output=el('div','extracted'); output.hidden=true;
    button.addEventListener('click',async()=>{
      button.disabled=true;
      try{
        const response=await fetch('/api/extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
        const data=await response.json(); output.textContent=data.text||data.error||'No text available.';
      }catch(e){output.textContent=e.message;}
      output.hidden=false; button.disabled=false;
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
  if(item.source) card.append(el('small','image-source',item.source));
  return card;
}
function fillPanel(id,items,kind){
  const panel=$(id);panel.replaceChildren();
  if(!items||!items.length){
    panel.append(el('div','empty','No verified results were returned for this category.'));
    const box=el('section','recommended-sources');
    box.append(el('h3','', 'Reliable sources to explore'));
    box.append(el('p','', 'Try these primary or specialist aerospace references while Aeroscraping expands its indexed coverage.'));
    const links=[
      ['NASA Technical Reports Server (NTRS)','https://ntrs.nasa.gov/'],
      ['NASA Image and Video Library','https://images.nasa.gov/'],
      ['FAA Aircraft Characteristics Database','https://www.faa.gov/airports/engineering/aircraft_char_database'],
      ['EASA Type Certificates and Data Sheets','https://www.easa.europa.eu/en/document-library/type-certificates'],
      ['UIUC Airfoil Coordinates Database','https://m-selig.ae.illinois.edu/ads/coord_database.html'],
      ['UIUC Aircraft-to-Airfoil Guide','https://m-selig.web.engr.illinois.edu/ads/aircraft.html'],
      ['Boeing Airport Planning Documents','https://www.boeing.com/commercial/airports/plan-manuals'],
      ['Airbus Aircraft Characteristics','https://www.airbus.com/en/products-services/commercial-aircraft']
    ];
    links.forEach(([label,url])=>addLink(box,label,url));
    panel.append(box);return;
  }
  if(kind==='images'){const grid=el('div','image-grid');items.forEach((it,i)=>grid.append(imageCard(it,i)));panel.append(grid);}
  else items.forEach((it,i)=>panel.append(resultCard(it,i,kind)));
}
function setTab(name){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===name));
  ['web','research','images','drawings','airfoil','plane'].forEach(k=>{ $(`panel-${k}`).hidden=k!==name;});
}
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>setTab(t.dataset.tab)));

let lastQuery='';
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
async function renderPlaneInfo(query){
  const panel=$('panel-plane');panel.replaceChildren(el('div','loading','Loading aircraft dataset record…'));
  try{
    const response=await fetch(`/api/aircraft/lookup?query=${encodeURIComponent(query)}`);
    const result=await response.json();panel.replaceChildren();
    if(!response.ok||!result.available){
      panel.append(el('div','dataset-unavailable',`“${query}” is not available in the current Aeroscraping aircraft dataset. Choose a listed aircraft from the search suggestions. The web and drawing search can still search other aircraft, but curated aircraft specifications are only shown for listed models.`));
      return;
    }
    const a=result.aircraft;
    const card=el('section','plane-info-card');
    card.append(el('div','category-label',`${a.manufacturer} · ${a.category}`));
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
    const card=el('section','plane-info-card');card.append(el('div','category-label','AIRFOIL REFERENCE'),el('h2','',data.aircraft),el('p','result-summary',data.message));
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
  button.disabled=true;button.textContent='Searching…';status.className='status';status.textContent='Searching the aircraft, research publications, images, technical drawings, airfoil references, and aircraft dataset…';results.hidden=true;
  try{
    const payload={query,web_limit:Math.max(1,Math.min(1000,Number($('webLimit').value)||150)),image_limit:Math.max(1,Math.min(1000,Number($('imageLimit').value)||150)),region:$('region').value,safesearch:$('safeSearch').value};
    const response=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'Search request failed.');
    const web=data.web_results||[],research=data.research_results||[],images=data.image_results||[],drawings=data.drawing_results||[];
    $('resultsTitle').textContent=`Search results for: ${data.query}`;
    $('resultsCounts').textContent=`${web.length} web sources · ${research.length} research/report leads · ${images.length} images · ${drawings.length} technical drawing references`;
    $('webCount').textContent=web.length;$('researchCount').textContent=research.length;$('imageCount').textContent=images.length;$('drawingCount').textContent=drawings.length;
    fillPanel('panel-web',web,'General web');fillPanel('panel-research',research,'Research & reports');fillPanel('panel-images',images,'images');fillPanel('panel-drawings',drawings,'Technical drawings');
    await Promise.all([renderAirfoil(query),renderPlaneInfo(query)]);
    results.hidden=false;setTab('web');status.replaceChildren();
    if(data.errors&&data.errors.length)status.textContent=`Some search providers timed out or were unavailable (${data.errors.length} notices). Results from the other sources are still shown.`;
    results.scrollIntoView({behavior:'smooth',block:'start'});
  }catch(error){status.className='status error';status.textContent=error.message;}
  finally{button.disabled=false;button.textContent='Search';}
});
