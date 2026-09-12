import { formatDuration, timeInKorea, localKoreaInput, arrivalDayLabel, samePlace, roundUpToMinute, buildJourneyGraph } from './utils.js';
import {createStressUI, scenarioLabel} from './stress-ui.js';

const $ = id => document.getElementById(id);
const fields = ['origin', 'destination'];
const state = { config: null, origin: null, destination: null, revision: 0, selection: 0, response: null, routeIndex: 0, nodeIndex: 0, reports: new Map() };
const searches = Object.fromEntries(fields.map(field => [field, {generation: 0, timer: null, controller: null, items: [], active: -1, composing: false}]));
const route = () => state.response?.routes[state.routeIndex];
const report = () => state.reports.get(route()?.id);
const clock = value => value ? timeInKorea(value) : '미확인';
const pointClock = value => {
  if (!value) return '미확인';
  const day = arrivalDayLabel(state.body.departure + ':00+09:00', value);
  return (day === '당일' ? '' : day + ' ') + clock(value);
};
const node = (tag, text = '', className = '') => { const el = document.createElement(tag); el.textContent = text; el.className = className; return el; };

async function request(url, options = {}) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, options.timeout || 30000);
  const abort = () => controller.abort();
  options.signal?.addEventListener('abort', abort, {once: true});
  if (options.signal?.aborted) controller.abort();
  try {
    const response = await fetch(url, {...options, signal: controller.signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '요청을 처리하지 못했어요.');
    return data;
  } catch (error) {
    if (timedOut) throw new Error('응답이 늦어지고 있어요. 잠시 후 다시 시도해 주세요.');
    if (error instanceof TypeError) throw new Error('서버와 연결되지 않았어요. 실행 상태를 확인해 주세요.');
    throw error;
  } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort); }
}
function errorMessage(text = '') { $('form-error').textContent = text; $('form-error').hidden = !text; }
function busy(yes) {
  $('submit-button').disabled = yes || !state.config;
  $('submit-button').firstElementChild.textContent = yes ? '경로를 모으고 있어요…' : '경로 & 지체 영향 비교';
  $('results-region').setAttribute('aria-busy', String(yes));
  $('loading-state').hidden = !yes;
}
function invalidate() {
  stressUI.reset();
  state.revision++; state.selection++;
  state.request?.abort(); state.deadlineRequest?.abort();
  state.response = null; state.reports.clear();
  $('result-content').hidden = true; $('empty-state').hidden = false;
  $('result-status').textContent = '경로를 기다리는 중';
  busy(false); errorMessage();
}
function closeSuggestions(field) {
  $(field + '-results').hidden = true;
  $(field + '-input').setAttribute('aria-expanded', 'false');
  $(field + '-input').removeAttribute('aria-activedescendant');
  searches[field].active = -1;
}
function cancelSearch(field) {
  const search = searches[field]; clearTimeout(search.timer); search.controller?.abort(); search.generation++;
  search.items = []; closeSuggestions(field);
}
function selectPlace(field, place) {
  cancelSearch(field); state[field] = place; $(field + '-input').value = place?.name || ''; invalidate();
}
function suggestions(field, items, message = '') {
  const list = $(field + '-results'); list.replaceChildren();
  const search = searches[field]; search.items = items; search.active = -1;
  if (message) list.append(node('p', message, 'suggestion-message'));
  for (const [index, place] of items.entries()) {
    const option = node('button', '', 'suggestion'); option.type = 'button'; option.tabIndex = -1;
    option.id = field + '-option-' + index; option.setAttribute('role', 'option'); option.setAttribute('aria-selected', 'false');
    const kind = place.resultType === 'transit' ? '대중교통 지점' : '장소';
    option.append(node('strong', place.name), node('small', kind + ' · ' + (place.address || '주소 정보 없음')));
    option.addEventListener('click', () => { selectPlace(field, place); $(field + '-input').focus(); });
    list.append(option);
  }
  $(field + '-input').removeAttribute('aria-activedescendant');
  $(field + '-input').setAttribute('aria-expanded', 'true'); list.hidden = false;
}
async function searchPlaces(field) {
  const search = searches[field], query = $(field + '-input').value.trim();
  if (!query || !state.config) return closeSuggestions(field);
  const generation = search.generation;
  search.controller = new AbortController(); suggestions(field, [], '지점을 찾고 있어요…');
  try {
    const data = await request('/api/places?q=' + encodeURIComponent(query) + '&kind=keyword', {signal: search.controller.signal});
    if (generation !== search.generation) return;
    suggestions(field, data.places, data.places.length ? '' : state.config.mode === 'demo' ? '예제 버튼을 선택해 주세요.' : '검색 결과가 없어요. 역·정류장 이름으로 다시 검색해 주세요.');
  } catch (error) {
    if (generation === search.generation && error.name !== 'AbortError') suggestions(field, [], error.message);
  }
}
for (const field of fields) {
  const input = $(field + '-input'), search = searches[field];
  const changed = () => {
    cancelSearch(field); state[field] = null; invalidate();
    if (!search.composing) search.timer = setTimeout(() => searchPlaces(field), 350);
  };
  input.addEventListener('input', changed);
  input.addEventListener('compositionstart', () => { search.composing = true; cancelSearch(field); });
  input.addEventListener('compositionend', () => { search.composing = false; changed(); });
  input.addEventListener('focus', () => { if (!state[field] && input.value.trim() && !search.composing) { cancelSearch(field); search.timer = setTimeout(() => searchPlaces(field), 150); } });
  input.addEventListener('keydown', event => {
    if (event.isComposing) return;
    if (event.key === 'Escape') return cancelSearch(field);
    if ($(field + '-results').hidden || !search.items.length) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); search.active = (search.active + (event.key === 'ArrowDown' ? 1 : -1) + search.items.length) % search.items.length;
      for (const [index, option] of [...$(field + '-results').querySelectorAll('[role="option"]')].entries()) {
        option.setAttribute('aria-selected', String(index === search.active));
        if (index === search.active) { input.setAttribute('aria-activedescendant', option.id); option.scrollIntoView({block: 'nearest'}); }
      }
    } else if (event.key === 'Enter') { event.preventDefault(); selectPlace(field, search.items[Math.max(0, search.active)]); }
    else if (event.key === 'Tab') cancelSearch(field);
  });
}
document.addEventListener('pointerdown', event => { for (const field of fields) if (!$(field + '-input').closest('.location-field').contains(event.target)) cancelSearch(field); });
$('swap').addEventListener('click', () => {
  const old = {origin: state.origin, destination: state.destination, a: $('origin-input').value, b: $('destination-input').value};
  selectPlace('origin', old.destination); selectPlace('destination', old.origin);
  if (!old.destination) $('origin-input').value = old.b;
  if (!old.origin) $('destination-input').value = old.a;
});
function setNow() {
  const [day, time] = localKoreaInput().split('T');
  $('departure-date').value = day; $('departure-time').value = time; invalidate();
}
$('depart-now').addEventListener('click', setNow);
$('depart-night').addEventListener('click', () => { $('departure-date').value = localKoreaInput().slice(0,10); $('departure-time').value = '23:00'; invalidate(); });
for (const id of ['departure-date', 'departure-time', 'access-minutes', 'buffer-minutes']) $(id).addEventListener('input', invalidate);
function readRequest() {
  if (!state.config) throw new Error('서버 연결을 확인해 주세요.');
  for (const field of fields) {
    if (!state[field] || $(field + '-input').value.trim() !== state[field].name) {
      $(field + '-input').focus(); throw new Error('검색 결과에서 ' + (field === 'origin' ? '출발지' : '목적지') + '를 선택해 주세요.');
    }
  }
  if (samePlace(state.origin, state.destination)) throw new Error('출발지와 목적지가 같아요. 다른 지점을 선택해 주세요.');
  for (const id of ['departure-date', 'departure-time', 'access-minutes']) {
    if (!$(id).value || !$(id).validity.valid) { $(id).focus(); throw new Error('출발 일시와 이동시간(0~120분)을 확인해 주세요.'); }
  }
  return {origin: state.origin, destination: state.destination, departure: $('departure-date').value + 'T' + $('departure-time').value,
    journeyMode: 'auto', bufferMinutes: Number($('buffer-minutes').value), accessMinutes: Number($('access-minutes').value)};
}
$('route-form').addEventListener('submit', async event => {
  event.preventDefault(); errorMessage();
  let body;
  try { body = readRequest(); } catch (error) { return errorMessage(error.message); }
  invalidate(); fields.forEach(cancelSearch);
  const revision = ++state.revision; state.request = new AbortController(); state.body = body;
  busy(true); $('empty-state').hidden = true; $('result-status').textContent = '모든 교통 경로 확인 중';
  try {
    const data = await request('/api/routes', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: state.request.signal, timeout: 60000});
    if (revision !== state.revision) return;
    state.response = data; state.routeIndex = 0; state.reports.clear(); busy(false);
    $('result-content').hidden = false; $('demo-banner').hidden = data.source !== 'demo';
    $('provider-warning').hidden = !data.providerWarnings?.length;
    $('provider-warning').textContent = data.providerWarnings?.length ? '일부 경로 제공처 응답을 확인하지 못했어요. 확인된 경로만 표시합니다.' : '';
    const counts = routeCounts(data.routes);
    $('result-status').textContent = '총 ' + data.routes.length + '개 · 지하철 ' + counts.SUBWAY + ' · 버스 ' + counts.BUS + (counts.TRANSIT ? ' · 혼합 ' + counts.TRANSIT : '');
    renderOptions();
    const recommended = data.routes.findIndex(r => r.id === data.stress?.recommendedRouteId);
    const evaluated = data.routes.findIndex(r => data.stress?.routes.find(s => s.routeId === r.id)?.status === 'evaluated');
    chooseRoute(recommended >= 0 ? recommended : evaluated >= 0 ? evaluated : 0);
    if (matchMedia('(max-width: 760px)').matches) $('results-region').scrollIntoView({block: 'start', behavior: 'auto'});
  } catch (error) {
    if (revision !== state.revision || error.name === 'AbortError') return;
    $('empty-state').hidden = false; $('result-status').textContent = '다시 검색해 주세요'; errorMessage(error.message);
  } finally { if (revision === state.revision) busy(false); }
});
function renderOptions() {
  const root = $('route-options'); root.replaceChildren();
  const definitions = [
    ['SUBWAY', '지하철 경로', '열차 시간표가 확인되면 지연 시나리오를 제공합니다.'],
    ['BUS', '버스 경로', '승차 노선과 정류장 순서, 막차 예정시간을 제공합니다.'],
    ['TRANSIT', '버스 + 지하철', '수단이 섞인 환승 경로를 순서대로 안내합니다.'],
  ];
  for (const [type, title, description] of definitions) {
    const entries = state.response.routes.map((item, index) => ({item, index})).filter(entry => entry.item.type === type);
    if (!entries.length) continue;
    const section = node('section', '', 'route-group ' + type.toLowerCase());
    const heading = node('div', '', 'route-group-heading');
    const headingCopy = node('div'); headingCopy.append(node('h3', title), node('p', description));
    heading.append(headingCopy, node('span', entries.length + '개', 'route-count')); section.append(heading);
    const list = node('div', '', 'route-group-list');
    for (const {item, index} of entries) {
      const button = node('button', '', 'route-option ' + type.toLowerCase()); button.type = 'button'; button.dataset.index = index;
      button.setAttribute('aria-pressed', 'false');
      const top = node('div', '', 'option-top');
      top.append(node('span', '경로 ' + String(index + 1).padStart(2, '0'), 'option-label'), node('span', transportLabel(item), 'transport-badge'));
      const rides = item.steps.filter(step => ['SUBWAY', 'BUS'].includes(step.type));
      button.append(top, node('strong', formatDuration(item.durationSeconds)),
        node('div', item.label || rides.map(step => step.vehicles[0] || step.title).join(' → '), 'option-lines'),
        node('small', '환승 ' + (item.transfers ?? '미확인') + '회 · ' + rides.length + '개 승차 구간', 'option-stats'),
        node('small', '선택하면 전체 이동 순서와 출발 마감 표시', 'option-deadline'));
      if (type === 'SUBWAY') {
        const stress = state.response.stress?.routes.find(report => report.routeId === item.id);
        const chips = node('div', '', 'route-stress-chips');
        if (stress?.status === 'evaluated') {
          for (const scenario of stress.scenarios) chips.append(node('span', (scenario.delayMinutes ? '+' + scenario.delayMinutes + '분 ' : '정상 ') + scenarioLabel(scenario.status), scenario.status));
        } else chips.append(node('span', '지연 분석 · 시간표 부족', 'unknown'));
        button.append(chips);
      } else {
        button.append(node('span', type === 'BUS' ? '버스 이동 순서 제공' : '환승 이동 순서 제공', 'route-support'));
      }
      button.addEventListener('click', () => chooseRoute(index)); list.append(button);
    }
    section.append(list); root.append(section);
  }
}
function routeCounts(routes) {
  return routes.reduce((counts, item) => { counts[item.type] = (counts[item.type] || 0) + 1; return counts; }, {SUBWAY: 0, BUS: 0, TRANSIT: 0});
}
function transportLabel(item) {
  return item.type === 'SUBWAY' ? '지하철' : item.type === 'BUS' ? '버스' : '버스 + 지하철';
}
function routeButtons() { return [...$('route-options').querySelectorAll('.route-option')]; }
function chooseRoute(index) {
  state.deadlineRequest?.abort(); state.selection++; state.routeIndex = index; state.nodeIndex = 0;
  for (const el of routeButtons()) el.setAttribute('aria-pressed', String(Number(el.dataset.index) === index));
  const item = route();
  $('journey-title').textContent = state.response.origin.name + ' → ' + state.response.destination.name;
  $('route-tag').textContent = transportLabel(item) + ' · 경로 ' + String(index + 1).padStart(2, '0');
  $('duration').textContent = formatDuration(item.durationSeconds);
  $('arrival-time').textContent = clock(roundUpToMinute(item.arrival));
  $('arrival-day').textContent = arrivalDayLabel(item.departure, roundUpToMinute(item.arrival));
  $('transfer-count').textContent = (item.transfers ?? '미확인') + '회';
  $('time-note').textContent = (item.warnings || []).join(' ') || state.response.notice;
  renderLine(); renderGuide(); renderDeadline(); loadDeadline();
  stressUI.setRoute({response: state.response, route: item, body: state.body, config: state.config});
}
function lineColor(step) {
  if (step.type === 'BUS') return '#d7bc84';
  const name = (step.vehicles || [])[0] || step.title || '';
  if (name.includes('2호선')) return '#94cf80';
  if (name.includes('4호선')) return '#76c3df';
  if (name.includes('5호선')) return '#b79be6';
  if (name.includes('9호선')) return '#c8b891';
  if (name.includes('공항')) return '#85bcd0';
  return '#92b8e6';
}
function renderLine() {
  state.graph = buildJourneyGraph(route(), state.response.origin.name);
  state.nodes = state.graph.filter(part => part.type === 'node');
  const line = $('route-line'); line.replaceChildren(); let nodeIndex = 0;
  for (const part of state.graph) {
    if (part.type === 'edge') {
      const edge = node('div', '', 'route-edge' + (part.kind === 'ride' ? '' : ' dashed'));
      edge.style.setProperty('--line-color', part.step ? lineColor(part.step) : '#66777f');
      edge.append(node('strong', part.label), node('small', part.detail)); line.append(edge); continue;
    }
    const index = nodeIndex++, button = node('button', '', 'route-node'); button.type = 'button';
    button.setAttribute('aria-pressed', String(index === state.nodeIndex));
    button.setAttribute('aria-label', part.name + ' ' + part.label + ' 정보 보기');
    if (part.kind === 'boarding') button.dataset.rideIndex = part.rideIndex;
    button.style.setProperty('--line-color', part.step ? lineColor(part.step) : '#c9e79a');
    button.append(node('span', index === state.nodeIndex ? '•' : '', 'node-dot'), node('span', part.name, 'node-label'), node('span', part.label, 'node-type'));
    button.addEventListener('click', () => selectNode(index));
    button.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = Math.min(state.nodes.length - 1, index + 1);
      if (event.key === 'ArrowLeft') next = Math.max(0, index - 1);
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = state.nodes.length - 1;
      if (next !== undefined) { event.preventDefault(); selectNode(next); line.querySelectorAll('.route-node')[next].focus(); }
    });
    line.append(button);
  }
  renderNode();
}
function renderGuide() {
  const guide = $('route-guide'); guide.replaceChildren();
  const rides = route().steps.filter(step => ['SUBWAY', 'BUS'].includes(step.type));
  const checkpoints = report()?.checkpoints || [];
  rides.forEach((step, rideIndex) => {
    const point = checkpoints[rideIndex] || {};
    const row = node('button', '', 'guide-step ' + step.type.toLowerCase()); row.type = 'button';
    const number = node('span', String(rideIndex + 1).padStart(2, '0'), 'guide-number');
    const copy = node('span', '', 'guide-copy');
    copy.append(node('span', (step.vehicles || [step.title])[0], 'guide-line'),
      node('strong', step.stops[0] + ' → ' + step.stops[step.stops.length - 1]),
      node('small', step.type === 'BUS' ? '버스 승차·하차 정류장' : '지하철 승차·하차역'));
    const seconds = step.durationSeconds ?? point.durationSeconds;
    const meta = node('span', '', 'guide-meta');
    meta.append(node('span', seconds == null ? '전체 시간에 포함' : formatDuration(seconds)),
      node('span', point.lastBusAt ? '막차 예정 ' + pointClock(point.lastBusAt) : step.departure ? '승차 ' + pointClock(step.departure) : '승차 시각 미제공'));
    row.append(number, copy, meta);
    const nodeIndex = state.nodes.findIndex(part => part.kind === 'boarding' && part.rideIndex === rideIndex);
    row.addEventListener('click', () => { if (nodeIndex >= 0) selectNode(nodeIndex); });
    guide.append(row);
  });
}
function selectNode(index) {
  state.nodeIndex = index;
  for (const [i, button] of [...$('route-line').querySelectorAll('.route-node')].entries()) {
    button.setAttribute('aria-pressed', String(i === index)); button.firstElementChild.textContent = i === index ? '•' : '';
    if (i === index) button.scrollIntoView({block: 'nearest', inline: 'nearest', behavior: 'auto'});
  }
  renderNode();
  stressUI.selectPoint(state.nodes[index]);
}
function matchingPoint(part) {
  const normalize = text => (text || '').replace(/역$/, '').trim();
  const points = report()?.checkpoints || [];
  return points.find(p => normalize(p.station) === normalize(part.name) && (!part.step || p.line.includes((part.step.vehicles || [part.step.title])[0])));
}
function renderNode() {
  const part = state.nodes?.[state.nodeIndex]; if (!part) return;
  const info = $('node-info'); info.replaceChildren();
  const heading = node('div', '', 'node-info-heading'), title = node('h4', part.name); title.id = 'node-heading';
  heading.append(title, node('span', part.label, 'detail-kind')); info.append(heading);
  const grid = node('dl', '', 'info-grid');
  const add = (label, value) => { const box = node('div'); box.append(node('dt', label), node('dd', value)); grid.append(box); };
  if (part.kind === 'origin') {
    add('늦어도 출발할 시각 · 예상', report()?.originLatestDeparture ? clock(report().originLatestDeparture) : report() ? '확인 필요' : '조회 중');
    add('첫 승차 지점까지 · 직접 입력', formatDuration((route().accessMinutes ?? state.body.accessMinutes) * 60));
    info.append(grid, node('p', '첫 승차 지점: ' + (route().boardingStation || state.nodes[1]?.name || '미확인') + '. 입력한 이동시간이 충분한지 확인하세요.'));
  } else if (part.kind === 'boarding') {
    const point = matchingPoint(part);
    add('탑승 노선', (part.step.vehicles || [part.step.title])[0]);
    add(point?.lastBusAt ? '해당 정류장 막차 예정' : '확인된 심야 열차', pointClock(point?.boardAt));
    add('이 지점 도착 마감 · 참고', pointClock(point?.arriveBy));
    add('입력 시각 기준 승차', part.step.departure ? pointClock(part.step.departure) : '시간표 미제공');
    info.append(grid, node('p', point?.notice || (report() ? '막차 예정·심야 표본을 이용한 참고값입니다. 빈 항목은 확인된 시각이 없습니다.' : '막차 정보를 확인하고 있어요. 완료되면 이 지점의 시각도 표시됩니다.')));
  } else {
    add('이용 노선', (part.step.vehicles || [part.step.title])[0]);
    add('입력 시각 기준 하차', pointClock(part.step.arrival));
    add('구간 소요시간', formatDuration(part.step.durationSeconds));
    add('다음 단계', part.final ? '목적지까지 별도 이동 확인' : '다음 승차 지점으로 환승');
    info.append(grid, node('p', part.final ? '최종 하차 지점입니다. 여기서 실제 목적지까지의 도보 경로는 조회하지 않습니다.' : '다음 지점까지 실제 이동시간은 현장에서 확인해 주세요. 여유시간 설정만으로 환승 성공을 보장하지는 않습니다.'));
  }
}
async function loadDeadline(force = false) {
  if (!route()) return;
  if (report() && !force) return;
  if (state.response.source !== 'seoul') {
    state.reports.set(route().id, {status: 'unknown', notice: '예제 데이터로 실제 막차 시각을 만들지 않습니다.'}); renderDeadline(); renderNode(); return;
  }
  const revision = state.revision, selection = state.selection, id = route().id;
  state.deadlineRequest?.abort(); state.deadlineRequest = new AbortController();
  if (force) state.reports.delete(id);
  renderDeadline(); renderNode();
  try {
    const data = await request('/api/deadline', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...state.body, routeToken: state.response.routeToken, routeId: id}), signal: state.deadlineRequest.signal, timeout: 120000});
    if (revision !== state.revision || selection !== state.selection) return;
    state.reports.set(id, data);
    const card = routeButtons().find(button => Number(button.dataset.index) === state.routeIndex)?.querySelector('.option-deadline');
    if (card) card.textContent = data.originLatestDeparture ? '출발 마감 ' + clock(data.originLatestDeparture) + ' · 예상' : '출발 마감 미확인';
    renderDeadline(); renderNode(); renderGuide();
  } catch (error) {
    if (revision !== state.revision || selection !== state.selection || error.name === 'AbortError') return;
    state.reports.set(id, {status: 'unknown', error: true, notice: error.message}); renderDeadline(); renderNode();
  }
}
$('deadline-retry').addEventListener('click', () => loadDeadline(true));
function renderDeadline() {
  const data = report(), hero = document.querySelector('.deadline-hero');
  $('deadline-retry').hidden = !data?.error; $('deadline-formula').replaceChildren();
  hero.classList.toggle('is-unknown', !!data && !data.originLatestDeparture);
  hero.classList.toggle('is-past', !!data?.past);
  $('deadline-suffix').textContent = ''; $('deadline-note').textContent = data?.notice || '';
  if (!data) {
    $('deadline-label').textContent = '늦어도 출발할 시각을 찾는 중';
    $('deadline-time').textContent = '—'; $('deadline-badge').textContent = '막차 정보 조회 중';
    $('deadline-summary').textContent = '선택한 경로의 승차·환승 지점별 시간을 확인하고 있어요.'; return;
  }
  if (!data.originLatestDeparture) {
    $('deadline-label').textContent = '출발 마감을 확인하지 못했어요'; $('deadline-time').textContent = '—';
    $('deadline-badge').textContent = '정보 부족'; $('deadline-summary').textContent = data.notice || '확인되지 않은 시간은 임의로 계산하지 않아요.'; return;
  }
  $('deadline-label').textContent = data.status === 'sampled' ? '확인된 심야 경로 기준, 늦어도' : '선택한 경로 기준, 늦어도';
  $('deadline-time').textContent = clock(data.originLatestDeparture); $('deadline-suffix').textContent = '까지 출발';
  $('deadline-badge').textContent = data.past ? '입력 시각이 마감 이후' : data.status === 'sampled' ? '심야 표본 · 참고값' : '막차 예정 기반 · 예상';
  const remaining = (new Date(data.originLatestDeparture).getTime() - Date.now()) / 1000;
  $('deadline-summary').textContent = data.originLatestDeparture.slice(0, 10) + ' · 출발지 기준. ' + (data.past ? '입력한 출발 시각은 마감 이후예요. 다른 경로도 확인해 주세요.' : remaining > 0 ? '지금부터 약 ' + formatDuration(Math.floor(remaining / 60) * 60) + ' 남았어요.' : '현재는 이 시각이 지났어요.');
  const first = data.boardingDeadline || data.latestDeparture;
  $('deadline-formula').append(node('span', '첫 승차 지점 도착 ' + clock(first), 'formula-chip'), node('span', '−'),
    node('span', '이동 ' + data.accessMinutes + '분', 'formula-chip'), node('span', '='),
    node('span', '출발 ' + clock(data.originLatestDeparture), 'formula-chip'),
    node('span', '여유 ' + data.bufferMinutes + '분 반영', 'formula-chip'));
}
function updateClock() {
  $('local-clock').textContent = timeInKorea(new Date());
  if (route() && report()) renderDeadline();
}
async function initialize() {
  setNow(); updateClock();
  try {
    state.config = await request('/api/config');
    $('mode-badge').textContent = state.config.mode === 'demo' ? '가상 예제' : '서울 교통 연결';
    $('config-notice').hidden = state.config.mode !== 'demo';
    for (const sample of state.config.examples || []) {
      const button = node('button', sample.origin.name + ' → ' + sample.destination.name, 'example-button'); button.type = 'button';
      button.addEventListener('click', () => { selectPlace('origin', sample.origin); selectPlace('destination', sample.destination); $('submit-button').focus(); });
      $('example-buttons').append(button);
    }
    busy(false);
  } catch (error) { $('mode-badge').textContent = '연결 확인 필요'; errorMessage(error.message); }
}
const stressUI = createStressUI({
  post: (url, body, signal) => request(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), signal, timeout:45000}),
  chooseRoute,
  highlight: (indices, status) => {
    for (const [index, button] of [...$('route-line').querySelectorAll('.route-node')].entries()) {
      const affected = button.dataset.rideIndex != null && indices.includes(Number(button.dataset.rideIndex));
      button.classList.toggle('is-broken', affected && status === 'missed');
      button.classList.toggle('is-tight', affected && status === 'tight');
      const part = state.nodes[index];
      button.querySelector('.node-type').textContent = part.label + (affected ? ' · ' + scenarioLabel(status) : '');
      button.setAttribute('aria-label', part.name + ' ' + part.label + ' 정보 보기' + (affected ? ' · ' + scenarioLabel(status) : ''));
    }
  },
});
initialize(); setInterval(updateClock, 30000);
