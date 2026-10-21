/* Refresh read-only regions; never replace forms, commands or newly issued keys. */
(() => {
  const button = document.querySelector('[data-live-refresh]');
  const message = document.querySelector('[data-live-message]');
  if (!button || !message) return;
  button.hidden = false;
  let busy = false;
  let last = document.querySelector('[data-live-as-of]')?.dataset.liveAsOf;
  function show(stale = false) {
    const parsed = Date.parse(last);
    const old = !Number.isFinite(parsed) || Date.now() - parsed > 180000;
    message.textContent = `${stale || old ? 'Updates unavailable; showing saved figures. ' : ''}Last updated ${Number.isFinite(parsed) ? new Date(parsed).toISOString().replace('T', ' ').slice(0, 19) + ' UTC' : 'unavailable'}.`;
    message.classList.toggle('pending', stale || old);
  }
  async function refresh() {
    if (busy) return;
    busy = true;
    button.disabled = true;
    try {
      const response = await fetch(location.pathname + location.search, {
        cache: 'no-store', credentials: 'same-origin', redirect: 'error',
        signal: AbortSignal.timeout(15000)
      });
      if (!response.ok) throw new Error('refresh failed');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const regions = [...document.querySelectorAll('[data-live-region]')];
      const replacements = regions.map(region => page.getElementById(region.id));
      if (!regions.length || replacements.some(node => !node?.hasAttribute('data-live-region')))
        throw new Error('missing live region');
      last = page.querySelector('[data-live-as-of]')?.dataset.liveAsOf;
      regions.forEach((region, i) => region.replaceWith(replacements[i]));
      show();
    } catch (_) {
      show(true);
    } finally {
      busy = false;
      button.disabled = false;
    }
  }
  button.addEventListener('click', refresh);
  setInterval(() => { if (!document.hidden) refresh(); }, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  show();
})();
