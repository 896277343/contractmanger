(function () {
  let pending = null;
  document.documentElement.classList.add('dashboard-js');

  function applyForm(form) {
    const url = new URL(form.action || window.location.href, window.location.href);
    url.search = new URLSearchParams(new FormData(form)).toString();
    url.hash = '';
    update(url, true, true);
  }

  async function update(url, push, keepPosition) {
    const panel = document.querySelector('.dashboard');
    if (!panel) return;
    if (pending) pending.abort();
    pending = new AbortController();
    const activeRequest = pending;
    const top = window.scrollY;
    const tabs = panel.querySelector('.dimension-tabs');
    const tabsLeft = tabs ? tabs.scrollLeft : 0;
    panel.classList.add('is-updating');
    panel.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(url.pathname + url.search, {
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        signal: activeRequest.signal
      });
      if (!response.ok || response.redirected) throw new Error('dashboard request failed');
      const html = await response.text();
      const next = new DOMParser().parseFromString(html, 'text/html').querySelector('.dashboard');
      if (!next) throw new Error('dashboard response missing');
      panel.replaceWith(next);
      if (push) history.pushState({ dashboard: true }, '', url.pathname + url.search + url.hash);
      const nextTabs = next.querySelector('.dimension-tabs');
      if (nextTabs) nextTabs.scrollLeft = tabsLeft;
      if (url.hash === '#contract-list' && !keepPosition) {
        document.querySelector('#contract-list')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else {
        window.scrollTo({ top, behavior: 'instant' });
      }
    } catch (error) {
      if (error.name !== 'AbortError') window.location.assign(url.href);
    } finally {
      if (pending === activeRequest) {
        pending = null;
        document.querySelector('.dashboard')?.classList.remove('is-updating');
        document.querySelector('.dashboard')?.removeAttribute('aria-busy');
      }
    }
  }

  document.addEventListener('click', function (event) {
    const anchor = event.target.closest('.dashboard a[href]');
    if (!anchor || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url = new URL(anchor.href, window.location.href);
    if (url.origin !== window.location.origin || url.pathname !== window.location.pathname) return;
    event.preventDefault();
    update(url, true, !url.hash);
  });

  document.addEventListener('submit', function (event) {
    const form = event.target;
    if (!form.matches('.dashboard form[method="get"]')) return;
    event.preventDefault();
    applyForm(form);
  });

  document.addEventListener('change', function (event) {
    const input = event.target;
    const form = input.closest('.dashboard form[method="get"]');
    if (!form || !input.matches('input[type="radio"], input[type="date"]')) return;
    applyForm(form);
  });

  window.addEventListener('popstate', function () {
    update(new URL(window.location.href), false, true);
  });
})();
