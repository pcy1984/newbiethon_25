// Only presentation changes: folding never changes a query or a running journey.
export const MOBILE_QUERY = '(max-width: 900px)';

export function createMobileSearch(doc = document, media = window.matchMedia(MOBILE_QUERY)) {
  const get = id => doc.getElementById(id);
  let hasResult = false, folded = false;
  function render() {
    const compact = media.matches && hasResult && folded;
    const fields = get('search-fields');
    // Don't leave keyboard focus inside a newly hidden form (e.g. on rotation).
    if (compact && fields.contains(doc.activeElement)) doc.activeElement.blur();
    fields.hidden = compact;
    get('search-summary').hidden = !compact;
    get('search-toggle').hidden = !media.matches || !hasResult;
    get('search-toggle').setAttribute('aria-expanded', String(!compact));
    get('search-toggle').textContent = compact ? '검색 수정' : '접기';
    get('search-heading').textContent = compact ? '이번 귀가 경로' : '어디에서 집에 가나요?';
    get('search-panel').classList.toggle('is-compact', compact);
    doc.body.classList.toggle('has-results', hasResult);
  }
  get('search-toggle').addEventListener('click', () => {
    folded = !folded;
    render();
    get('search-panel').scrollIntoView({block: 'start', behavior: 'auto'});
  });
  media.addEventListener('change', render);
  render();
  return {
    setRoute(response, body) {
      hasResult = true; folded = true;
      get('search-origin').textContent = response.origin.name;
      get('search-destination').textContent = response.destination.name;
      get('search-time').textContent = body.departure.replace('T', ' · ') + ' 출발 예정';
      render();
    },
    reset() { hasResult = false; folded = false; render(); },
  };
}
