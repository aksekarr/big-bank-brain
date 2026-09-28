/*
 * Motion layer — presentation only.
 * Watches the DOM that app.js renders and animates it. It never fetches data,
 * never changes copy, and always leaves each number at exactly the value
 * app.js wrote. If this file fails, the inline guard in <head> removes the
 * js-motion class and the page shows everything statically.
 */
(() => {
  const root = document.documentElement;
  if (!root.classList.contains('js-motion')) return;
  window.BBB_MOTION = true;

  const $ = (sel, scope = document) => scope.querySelector(sel);
  const $$ = (sel, scope = document) => [...scope.querySelectorAll(sel)];
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);

  /* ---------- Count a number up to the value app.js rendered ---------- */
  function countTo(el, target, duration) {
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / duration);
      el.textContent = String(Math.round(target * easeOut(t)));
      if (t < 1) requestAnimationFrame(step);
      else el.textContent = String(target); // always land on the real value
    };
    requestAnimationFrame(step);
  }
  const numeric = (el) => el && /^\d+$/.test(el.textContent.trim());

  /* ---------- Arrival: the pipeline runs, then the headline lands ---------- */
  const ticker = $('.ticker');
  const copy = $('.arrival-copy');
  const art = $('.arrival-art');
  let arrivalStarted = false;

  function revealCopy() {
    if (copy.classList.contains('is-in')) return;
    [...copy.children].forEach((child, i) => child.style.setProperty('--i', i));
    copy.classList.add('is-in');
    art.classList.add('is-in');
    art.addEventListener('transitionend', () => { art.style.clipPath = 'none'; }, { once: true });
  }

  function runArrival() {
    if (arrivalStarted) return;
    arrivalStarted = true;
    const items = [...ticker.children];
    const targets = new Map();
    items.forEach((item) => {
      const num = $('.ticker-num', item);
      if (numeric(num)) { targets.set(item, Number(num.textContent)); num.textContent = '0'; }
    });
    items.forEach((item, i) => {
      setTimeout(() => {
        item.classList.add('is-lit');
        if (targets.has(item)) countTo($('.ticker-num', item), targets.get(item), 900);
      }, 150 + i * 190);
    });
    // Headline follows once the interpretation has arrived (or after a short wait).
    const bottomLine = $('#bottom-line');
    if (bottomLine.textContent.trim()) setTimeout(revealCopy, 350);
    else {
      const mo = new MutationObserver(() => { if (bottomLine.textContent.trim()) { mo.disconnect(); setTimeout(revealCopy, 120); } });
      mo.observe(bottomLine, { childList: true, characterData: true, subtree: true });
      setTimeout(() => { mo.disconnect(); revealCopy(); }, 1100);
    }
  }

  const firstCount = $('#publication-count');
  if (numeric(firstCount)) runArrival();
  else {
    const mo = new MutationObserver(() => { if (numeric(firstCount)) { mo.disconnect(); runArrival(); } });
    mo.observe(firstCount, { childList: true, characterData: true, subtree: true });
    // Data failed or is slow: light everything so nothing stays dimmed.
    setTimeout(() => {
      if (arrivalStarted) return;
      mo.disconnect();
      [...ticker.children].forEach((item) => item.classList.add('is-lit'));
      revealCopy();
    }, 2500);
  }

  /* ---------- Scroll reveal ---------- */
  const io = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-in');
      io.unobserve(entry.target);
      const counter = entry.target.querySelector('#test-count');
      if (numeric(counter)) countTo(counter, Number(counter.textContent), 1100);
    });
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0 });

  function reveal(elements, stagger = true) {
    elements.forEach((el, i) => {
      if (el.classList.contains('reveal')) return;
      el.classList.add('reveal');
      if (stagger) el.style.setProperty('--i', i);
      io.observe(el);
    });
  }

  reveal($$('.evidence-intro > *'));
  reveal([$('.method-heading')]);
  reveal($$('.pipeline li'));
  reveal([$('.interpretation-method'), $('.human-review-summary')].filter(Boolean));
  reveal($$('.credibility-grid article'));
  reveal([$('.case-study')]);
  reveal([$('.footer-illustration')]);

  /* ---------- Theme nav highlights the theme you're reading ---------- */
  const spy = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      $$('#theme-links a').forEach((a) => a.classList.toggle('is-active', a.getAttribute('href') === `#${entry.target.id}`));
    });
  }, { rootMargin: '-35% 0px -55% 0px' });

  // Content app.js renders after its fetch resolves
  const watchChildren = (container, onAdd) => {
    if (!container) return;
    onAdd([...container.children]);
    new MutationObserver((records) => {
      const added = records.flatMap((r) => [...r.addedNodes]).filter((n) => n.nodeType === 1);
      if (added.length) onAdd([...container.children]);
    }).observe(container, { childList: true });
  };
  watchChildren($('#coverage-list'), (kids) => reveal(kids));
  watchChildren($('#themes'), (kids) => { reveal(kids, false); kids.forEach((k) => spy.observe(k)); });

})();
