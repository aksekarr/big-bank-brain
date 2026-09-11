const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
const correctionMarker = () => '<span class="correction-marker">Corrected in human review</span>';

function applyCorrections(text, target, corrections) {
  const relevant = corrections.filter((correction) => correction.target_id === target || correction.target_id.startsWith(`${target}-`));
  let corrected = text;
  let changed = false;
  relevant.forEach((correction) => {
    if (corrected.includes(correction.original_text)) {
      corrected = corrected.replace(correction.original_text, correction.corrected_text);
      changed = true;
    }
  });
  return { text: corrected, changed };
}

function dateRange(window) {
  const options = { day: 'numeric', month: 'long', year: 'numeric', timeZone: window.timezone };
  const start = new Date(`${window.start_date}T12:00:00Z`).toLocaleDateString('en-GB', options);
  const end = new Date(`${window.end_date}T12:00:00Z`).toLocaleDateString('en-GB', options);
  return start === end ? start : `${start} — ${end}`;
}

function reviewLabel() {
  return window.BBB_CONFIG?.review_status === 'reviewed'
    ? '<strong>Reviewed</strong><span>human review</span>'
    : '<strong>Pending</strong><span>human review</span>';
}

function runMetadata(data) {
  const generated = data.generated_at
    ? new Date(data.generated_at).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: data.window.timezone, timeZoneName: 'short' })
    : null;
  return [data.model, generated].filter(Boolean).join(' · ');
}

function render(data, corrections) {
  const articles = new Map(data.articles.map((article) => [article.article_ref, article]));
  const claims = new Map(data.articles.flatMap((article) => article.claims.map((claim) => {
    const corrected = applyCorrections(claim.text, `claim-${claim.ref.replace(':', '-')}`, corrections);
    return [claim.ref, { ...claim, text: corrected.text, corrected: corrected.changed, article }];
  })));
  const institutions = new Set(data.articles.map((article) => article.institution));
  byId('headline').textContent = data.synthesis.headline;
  byId('overview').textContent = data.synthesis.overview;
  byId('window-label').textContent = dateRange(data.window).toUpperCase();
  byId('publication-count').textContent = data.articles.length;
  byId('claim-count').textContent = claims.size;
  byId('theme-count').textContent = data.synthesis.themes.length;
  byId('test-count').textContent = '249';
  byId('review-state').innerHTML = reviewLabel();
  byId('run-meta').textContent = runMetadata(data);
  byId('human-review-summary').textContent = `Human review found and corrected ${corrections.length} issues before publication.`;
  window.BBB_EVIDENCE = { articles, claims };

  const coverage = byId('coverage-list');
  [...institutions].forEach((institution) => {
    const group = document.createElement('section');
    const titles = data.articles.filter((article) => article.institution === institution);
    group.innerHTML = `<h2>${escapeHtml(institution)}</h2><ul>${titles.map((article) => `<li>${escapeHtml(article.title)}</li>`).join('')}</ul>`;
    coverage.append(group);
  });

  const themeLinks = byId('theme-links');
  const themes = byId('themes');
  const themeSections = [];
  data.synthesis.themes.forEach((theme, themeIndex) => {
    const themeId = `theme-${themeIndex + 1}`;
    const navItem = document.createElement('li');
    navItem.innerHTML = `<a href="#${themeId}"><span>0${themeIndex + 1}</span>${escapeHtml(theme.title)}</a>`;
    themeLinks.append(navItem);
    const section = document.createElement('section');
    section.className = 'theme'; section.id = themeId; section.dataset.themeId = `t${themeIndex + 1}`;
    section.innerHTML = `<div class="theme-title"><span>0${themeIndex + 1}</span><h2>${escapeHtml(theme.title)}</h2></div>`;
    const pointList = document.createElement('div'); pointList.className = 'point-list';
    theme.points.forEach((point, pointIndex) => {
      const corrected = applyCorrections(point.text, `theme-${themeIndex + 1}-point-${pointIndex + 1}`, corrections);
      const refs = point.references.map((ref) => claims.get(ref)).filter(Boolean);
      const sourceNames = [...new Set(refs.map(({ article }) => article.institution))];
      const button = document.createElement('button'); button.type = 'button'; button.className = 'point';
      const claimLabel = `${refs.length} source claim${refs.length === 1 ? '' : 's'}`;
      button.setAttribute('aria-expanded', 'false');
      button.innerHTML = `<span class="point-index">${String(pointIndex + 1).padStart(2, '0')}</span><span class="point-copy">${escapeHtml(corrected.text)}${corrected.changed ? correctionMarker() : ''}</span><span class="point-meta">${sourceNames.map(escapeHtml).join(' · ')}</span><span class="evidence-control">${claimLabel} <b aria-hidden="true">↓</b></span>`;
      button.addEventListener('click', () => openEvidence(point, refs, button));
      pointList.append(button);
    });
    const detail = document.createElement('details'); detail.className = 'theme-detail';
    detail.innerHTML = '<summary>See the detail <span>AI-written briefing points</span></summary>';
    detail.append(pointList);
    section.append(detail); themes.append(section); themeSections.push(section);
  });
  loadInterpretation(themeSections, corrections);
}

function loadInterpretation(themeSections, corrections) {
  fetch('./interpretation.json')
    .then((response) => response.ok ? response.json() : null)
    .then((interpretation) => {
      if (!interpretation || typeof interpretation.bottom_line !== 'string' || !Array.isArray(interpretation.themes)) return;
      const bottomLine = applyCorrections(interpretation.bottom_line.replace(/^BBB[’']s read:\s*/i, ''), 'bottom-line', corrections);
      byId('bottom-line').innerHTML = `${escapeHtml(bottomLine.text)}${bottomLine.changed ? correctionMarker() : ''}`;
      byId('interpretation-legend').hidden = false;
      interpretation.themes.forEach((read, index) => {
        const section = themeSections.find((item) => item.dataset.themeId === read.theme_id);
        if (!section || typeof read.takeaway !== 'string' || typeof read.read !== 'string') return;
        const takeaway = applyCorrections(read.takeaway, `theme-${index + 1}-takeaway`, corrections);
        const interpretationRead = applyCorrections(read.read, `theme-${index + 1}-read`, corrections);
        const panel = document.createElement('section'); panel.className = 'interpretation theme-interpretation';
        panel.innerHTML = `<p class="eyebrow">BBB'S READ</p><h3 class="takeaway">${escapeHtml(takeaway.text)}${takeaway.changed ? correctionMarker() : ''}</h3><p class="interpretation-copy">${escapeHtml(interpretationRead.text)}${interpretationRead.changed ? correctionMarker() : ''}</p>`;
        section.querySelector('.theme-title').insertAdjacentElement('afterend', panel);
      });
    })
    .catch(() => {});
}

function openEvidence(point, refs, button) {
  document.querySelectorAll('.point[aria-expanded="true"]').forEach((item) => item.setAttribute('aria-expanded', 'false'));
  document.querySelectorAll('.evidence-panel').forEach((item) => item.remove());
  button.setAttribute('aria-expanded', 'true');
  const panel = document.createElement('section'); panel.className = 'evidence-panel';
  panel.innerHTML = '<p class="eyebrow">AI-WRITTEN POINT</p><p class="evidence-chain">Synthesised from these extracted claims</p>';
  const articleRefs = [...new Set(refs.map(({ article }) => article.article_ref))];
  articleRefs.forEach((articleRef) => {
    const article = window.BBB_EVIDENCE.articles.get(articleRef);
    const linkedClaims = refs.filter(({ article: source }) => source.article_ref === articleRef);
    const card = byId('article-template').content.cloneNode(true);
    card.querySelector('.institution').textContent = article.institution;
    card.querySelector('time').dateTime = article.publication_date;
    card.querySelector('time').textContent = new Date(`${article.publication_date}T12:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
    card.querySelector('h3').textContent = article.title;
    linkedClaims.forEach((claim) => {
      const item = document.createElement('li'); item.textContent = claim.text;
      if (claim.corrected) item.insertAdjacentHTML('beforeend', correctionMarker());
      card.querySelector('.claim-list').append(item);
    });
    card.querySelector('.source-link').href = article.url;
    panel.append(card);
  });
  button.insertAdjacentElement('afterend', panel);
}

Promise.all([
  fetch('./synthesis-result.json').then((response) => { if (!response.ok) throw new Error('Briefing data unavailable'); return response.json(); }),
  fetch('./corrections.json').then((response) => response.ok ? response.json() : { corrections: [] }).catch(() => ({ corrections: [] }))
])
  .then(([data, correctionData]) => render(data, Array.isArray(correctionData.corrections) ? correctionData.corrections : []))
  .catch(() => { byId('headline').textContent = 'The latest briefing is temporarily unavailable.'; });
