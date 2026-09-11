const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);

function dateRange(window) {
  const options = { day: 'numeric', month: 'long', year: 'numeric', timeZone: window.timezone };
  const start = new Date(`${window.start_date}T12:00:00Z`).toLocaleDateString('en-GB', options);
  const end = new Date(`${window.end_date}T12:00:00Z`).toLocaleDateString('en-GB', options);
  return start === end ? start : `${start} — ${end}`;
}

function reviewLabel() {
  return window.BBB_CONFIG?.review_status === 'reviewed'
    ? '<span class="status-dot reviewed"></span> Human review complete'
    : '<span class="status-dot pending"></span> Awaiting human review';
}

function render(data) {
  const articles = new Map(data.articles.map((article) => [article.article_ref, article]));
  const claims = new Map(data.articles.flatMap((article) => article.claims.map((claim) => [claim.ref, { ...claim, article }])));
  const institutions = new Set(data.articles.map((article) => article.institution));
  byId('headline').textContent = data.synthesis.headline;
  byId('overview').textContent = data.synthesis.overview;
  byId('window-label').textContent = dateRange(data.window).toUpperCase();
  byId('publication-count').textContent = data.articles.length;
  byId('institution-count').textContent = institutions.size;
  byId('claim-count').textContent = claims.size;
  byId('test-count').textContent = '249';
  byId('review-state').innerHTML = reviewLabel();

  const themeLinks = byId('theme-links');
  const themes = byId('themes');
  data.synthesis.themes.forEach((theme, themeIndex) => {
    const themeId = `theme-${themeIndex + 1}`;
    const navItem = document.createElement('li');
    navItem.innerHTML = `<a href="#${themeId}"><span>0${themeIndex + 1}</span>${escapeHtml(theme.title)}</a>`;
    themeLinks.append(navItem);
    const section = document.createElement('section');
    section.className = 'theme'; section.id = themeId;
    section.innerHTML = `<div class="theme-title"><span>0${themeIndex + 1}</span><h2>${escapeHtml(theme.title)}</h2></div>`;
    const pointList = document.createElement('div'); pointList.className = 'point-list';
    theme.points.forEach((point, pointIndex) => {
      const refs = point.references.map((ref) => claims.get(ref)).filter(Boolean);
      const sourceNames = [...new Set(refs.map(({ article }) => article.institution))];
      const button = document.createElement('button'); button.type = 'button'; button.className = 'point';
      button.innerHTML = `<span class="point-index">${String(pointIndex + 1).padStart(2, '0')}</span><span class="point-copy">${escapeHtml(point.text)}</span><span class="point-meta">${sourceNames.map(escapeHtml).join(' · ')} <b aria-hidden="true">+</b></span>`;
      button.addEventListener('click', () => openEvidence(point, refs));
      pointList.append(button);
    });
    section.append(pointList); themes.append(section);
  });
  byId('evidence-drawer').addEventListener('click', (event) => { if (event.target === event.currentTarget) event.currentTarget.close(); });
  document.querySelector('.close').addEventListener('click', () => byId('evidence-drawer').close());
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') byId('evidence-drawer').close(); });
  window.BBB_EVIDENCE = { articles, claims };
}

function openEvidence(point, refs) {
  const drawer = byId('evidence-drawer');
  byId('drawer-title').textContent = point.text;
  const content = byId('drawer-content'); content.replaceChildren();
  const articleRefs = [...new Set(refs.map(({ article }) => article.article_ref))];
  articleRefs.forEach((articleRef) => {
    const article = window.BBB_EVIDENCE.articles.get(articleRef);
    const linkedClaims = refs.filter(({ article: source }) => source.article_ref === articleRef);
    const card = byId('article-template').content.cloneNode(true);
    card.querySelector('.institution').textContent = article.institution;
    card.querySelector('time').dateTime = article.publication_date;
    card.querySelector('time').textContent = new Date(`${article.publication_date}T12:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
    card.querySelector('h3').textContent = article.title;
    linkedClaims.forEach((claim) => { const item = document.createElement('li'); item.textContent = claim.text; card.querySelector('.claim-list').append(item); });
    card.querySelector('.source-link').href = article.url;
    content.append(card);
  });
  drawer.showModal();
}

fetch('./synthesis-result.json')
  .then((response) => { if (!response.ok) throw new Error('Briefing data unavailable'); return response.json(); })
  .then(render)
  .catch(() => { byId('headline').textContent = 'The latest briefing is temporarily unavailable.'; });
