const $ = (id) => document.getElementById(id);
const profileToggle = $('profileToggle');
const profileCard = $('profileCard');
profileToggle.addEventListener('click', () => {
  const opening = profileCard.hidden;
  profileCard.hidden = !opening;
  profileToggle.setAttribute('aria-expanded', String(opening));
  profileToggle.querySelector('.chevron').textContent = opening ? '⌃' : '⌄';
});
document.addEventListener('click', (event) => {
  if (!profileCard.hidden && !profileCard.contains(event.target) && !profileToggle.contains(event.target)) {
    profileCard.hidden = true;
    profileToggle.setAttribute('aria-expanded', 'false');
    profileToggle.querySelector('.chevron').textContent = '⌄';
  }
});

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function safeUrl(value) {
  try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; }
  catch { return ''; }
}
function resultCard(item, index, category) {
  const card = el('article', 'result-card');
  const type = item.category || item.drawing_type || category;
  if (type) card.append(el('div', 'category-label', type));
  const title = item.title || `Result ${index + 1}`;
  const url = safeUrl(item.href || item.url || item.source || '');
  const h = el('h3');
  if (url) { const a = el('a', '', title); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; h.append(a); }
  else h.textContent = title;
  card.append(h);
  if (item.year || item.authors || item.source) {
    const meta = [item.authors, item.year, item.source].filter(Boolean).join(' · ');
    if (meta) card.append(el('div', 'result-url', meta));
  }
  if (url) card.append(el('div', 'result-url', url));
  if (category === 'Technical drawings' && safeUrl(item.thumbnail || item.url)) {
    const preview = document.createElement('img'); preview.className = 'drawing-preview'; preview.src = safeUrl(item.thumbnail || item.url); preview.alt = title; preview.loading = 'lazy'; preview.onerror = () => preview.remove(); card.append(preview);
  }
  card.append(el('p', 'result-summary', item.body || item.snippet || 'The search provider did not provide a summary for this result.')); 
  if (url) {
    const actions = el('div', 'result-actions');
    const button = el('button', 'text-button', 'Extract more page text');
    button.type = 'button';
    const output = el('div', 'extracted'); output.hidden = true;
    button.addEventListener('click', async () => {
      if (!output.hidden) { output.hidden = true; button.textContent = 'Show extracted page text'; return; }
      button.disabled = true; button.textContent = 'Extracting…';
      try {
        const response = await fetch('/api/extract', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url})});
        const data = await response.json();
        output.textContent = data.text || data.error || 'No readable text was returned.';
      } catch (error) { output.textContent = `Extraction failed: ${error.message}`; }
      output.hidden = false; button.disabled = false; button.textContent = 'Hide extracted page text';
    });
    actions.append(button); card.append(actions, output);
  }
  return card;
}
function imageCard(item, index) {
  const card = el('article', 'image-card');
  const image = safeUrl(item.image || item.thumbnail || item.url || '');
  const target = safeUrl(item.url || item.image || '');
  if (image) {
    const link = el('a');
    if (target) { link.href = target; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
    const img = document.createElement('img'); img.src = image; img.alt = item.title || `Image result ${index + 1}`; img.loading = 'lazy';
    img.onerror = () => { img.style.display = 'none'; };
    link.append(img); card.append(link);
  }
  card.append(el('p', '', item.title || `Image result ${index + 1}`));
  return card;
}
function fillPanel(id, items, kind) {
  const panel = $(id); panel.replaceChildren();
  if (!items || !items.length) { panel.append(el('div','empty','No results were returned for this category. Try a broader search phrase.')); return; }
  if (kind === 'images') {
    const grid = el('div','image-grid'); items.forEach((item,i) => grid.append(imageCard(item,i))); panel.append(grid);
  } else items.forEach((item,i) => panel.append(resultCard(item,i,kind)));
}
function setTab(name) {
  document.querySelectorAll('.tab').forEach(tab => tab.classList.toggle('active', tab.dataset.tab === name));
  ['web','research','images','drawings'].forEach(key => { $(`panel-${key}`).hidden = key !== name; });
}
document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => setTab(tab.dataset.tab)));

$('searchForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const query = $('query').value.trim();
  if (!query) return;
  const button = $('searchButton');
  const status = $('status');
  const results = $('resultsSection');
  button.disabled = true;
  button.textContent = 'Searching…';
  status.className = 'status';
  status.replaceChildren();
  const loading = el('span','loading'); loading.append(el('span','spinner'), document.createTextNode('Searching the web, academic publications, technical reports, images, and drawing references…'));
  status.append(loading);
  results.hidden = true;
  try {
    const payload = {
      query,
      web_limit: Math.max(1, Math.min(1000, Number($('webLimit').value) || 150)),
      image_limit: Math.max(1, Math.min(1000, Number($('imageLimit').value) || 150)),
      region: $('region').value,
      safesearch: $('safeSearch').value,
    };
    const response = await fetch('/api/search', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Search request failed.');
    const web = data.web_results || [], research = data.research_results || [], images = data.image_results || [], drawings = data.drawing_results || [];
    $('resultsTitle').textContent = `Search results for: ${data.query}`;
    $('resultsCounts').textContent = `${web.length} aerospace web sources · ${research.length} research/report leads · ${images.length} images · ${drawings.length} verified drawing references`;
    $('webCount').textContent = web.length; $('researchCount').textContent = research.length; $('imageCount').textContent = images.length; $('drawingCount').textContent = drawings.length;
    fillPanel('panel-web', web, 'General web');
    fillPanel('panel-research', research, 'Research & reports');
    fillPanel('panel-images', images, 'images');
    fillPanel('panel-drawings', drawings, 'Technical drawings');
    results.hidden = false; setTab('web');
    status.replaceChildren();
    if (data.errors && data.errors.length) status.textContent = `Some providers were unavailable: ${data.errors.join(' · ')}`;
    results.scrollIntoView({behavior:'smooth', block:'start'});
  } catch (error) {
    status.className = 'status error'; status.textContent = error.message;
  } finally {
    button.disabled = false; button.textContent = 'Search';
  }
});
