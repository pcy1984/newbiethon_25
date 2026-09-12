import {formatDuration, timeInKorea, arrivalDayLabel} from './utils.js';

const seconds = value => Number.isFinite(value) && value >= 0 ? value : null;

// Progress is a clock-based illustration of the queried plan, never GPS.
export function buildProgressPlan(route) {
  const rides = route.steps.filter(step => ['SUBWAY', 'BUS'].includes(step.type));
  if (!rides.length) return null;
  const phases = [];
  let cursor = 0;
  const add = (duration, fromNode, toNode, kind, label, nextName) => {
    if (duration > 0) phases.push({start: cursor, end: cursor + duration, fromNode, toNode, kind, label, nextName});
    cursor += Math.max(0, duration);
  };
  const access = (seconds(route.accessMinutes) ?? 0) * 60;
  const originTime = Date.parse(route.departure);
  const schedule = rides.map(step => ({board: (Date.parse(step.departure) - originTime) / 1000, arrive: (Date.parse(step.arrival) - originTime) / 1000}));
  const timed = route.scheduleStatus === 'complete' && schedule.every((point, index) =>
    Number.isFinite(point.board) && Number.isFinite(point.arrive) && point.arrive > point.board &&
    point.board >= (index ? schedule[index - 1].arrive : access));
  let approximate = !timed;
  if (timed) {
    add(access, 0, 1, 'access', '첫 승차 지점으로 이동 중', rides[0].stops[0]);
    rides.forEach((step, index) => {
      const boardNode = index * 2 + 1, line = step.vehicles?.[0] || step.title;
      add(schedule[index].board - cursor, index ? boardNode - 1 : boardNode, boardNode,
        index ? 'transfer' : 'waiting', index ? '환승 이동·대기 중' : line + ' 승차 대기 중', step.stops[0]);
      add(schedule[index].arrive - cursor, boardNode, boardNode + 1, 'ride', line + ' 이동 중', step.stops.at(-1));
    });
  } else {
    const margin = (seconds(route.bufferMinutes) ?? 5) * 60;
    const durations = rides.map(step => seconds(step.durationSeconds));
    const known = durations.reduce((sum, value) => sum + (value ?? 0), 0);
    const missing = durations.filter(value => value == null).length;
    const budget = Math.max(seconds(route.durationSeconds) ?? 0, access + margin * rides.length + known);
    const share = missing ? (budget - access - margin * rides.length - known) / missing : 0;
    // If no positive duration can be assigned to a missing ride, don't invent it.
    if (missing && share <= 0) return null;
    add(access, 0, 1, 'access', '첫 승차 지점으로 이동 중', rides[0].stops[0]);
    rides.forEach((step, index) => {
      const boardNode = index * 2 + 1, line = step.vehicles?.[0] || step.title;
      add(margin, index ? boardNode - 1 : boardNode, boardNode, index ? 'transfer' : 'waiting', index ? '환승 이동·대기 중' : line + ' 승차 대기 중', step.stops[0]);
      add(durations[index] ?? share, boardNode, boardNode + 1, 'ride', line + ' 이동 중', step.stops.at(-1));
    });
    if (cursor < budget) add(budget - cursor, rides.length * 2, rides.length * 2, 'waiting', '최종 하차 대기 중', rides.at(-1).stops.at(-1));
    approximate = true;
  }
  if (!phases.length || cursor <= 0) return null;
  return {phases, totalSeconds: cursor, finalNode: rides.length * 2, finalName: rides.at(-1).stops.at(-1), approximate,
    explanation: timed ? '조회한 열차 경로의 이동·대기 간격을 출발 버튼을 누른 시각부터 재생합니다. 실제 열차 시각을 다시 조회하지 않습니다.' :
      '전체 예상 소요시간과 설정한 여유시간을 사용합니다. 시간이 없는 이동 구간에는 남은 시간을 균등 배분합니다.'};
}

export function progressAt(plan, elapsedSeconds) {
  const elapsed = Math.max(0, Number.isFinite(elapsedSeconds) ? elapsedSeconds : 0);
  const complete = elapsed >= plan.totalSeconds;
  const phase = plan.phases.find(item => elapsed < item.end) || plan.phases.at(-1);
  const fraction = complete ? 1 : Math.max(0, Math.min(1, (elapsed - phase.start) / (phase.end - phase.start)));
  return {complete, phase, position: complete ? plan.finalNode : phase.fromNode + (phase.toNode - phase.fromNode) * fraction,
    ratio: plan.totalSeconds ? Math.min(1, elapsed / plan.totalSeconds) : 1,
    remaining: Math.max(0, plan.totalSeconds - elapsed), nextSeconds: Math.max(0, phase.end - elapsed)};
}

export function createJourneyTracker() {
  const $ = id => document.getElementById(id);
  let plan = null, startedAt = null, interval = null, activePhase = null;
  const stopClock = () => { if (interval != null) clearInterval(interval); interval = null; };
  function placeMarker(position, follow = false) {
    const nodes = [...$('route-line').querySelectorAll('.route-node')];
    const left = nodes[Math.floor(position)], right = nodes[Math.min(nodes.length - 1, Math.ceil(position))];
    if (!left || !right || typeof left.getBoundingClientRect !== 'function') return;
    const base = $('route-track').getBoundingClientRect();
    const a = left.getBoundingClientRect(), b = right.getBoundingClientRect();
    const fraction = position - Math.floor(position);
    const x = a.left + a.width / 2 - base.left + (b.left + b.width / 2 - a.left - a.width / 2) * fraction;
    $('traveler-marker').style.left = x + 'px';
    const scroll = $('route-scroll');
    if (follow && scroll.clientWidth && (x < scroll.scrollLeft + 45 || x > scroll.scrollLeft + scroll.clientWidth - 45)) {
      scroll.scrollTo({left: Math.max(0, x - scroll.clientWidth / 2), behavior: 'auto'});
    }
  }
  function tick() {
    if (startedAt == null || !plan) return;
    const elapsed = Math.max(0, (Date.now() - startedAt) / 1000);
    const current = progressAt(plan, elapsed);
    $('trip-elapsed').textContent = '출발 후 ' + Math.floor(elapsed / 60) + '분 ' + String(Math.floor(elapsed % 60)).padStart(2, '0') + '초';
    $('trip-remaining').textContent = current.complete ? '최종 하차 지점 도착 예상' : '약 ' + formatDuration(current.remaining) + ' 남음';
    $('trip-progress').value = current.ratio * 100;
    $('trip-progress').setAttribute('aria-valuetext', Math.floor(current.ratio * 100) + '% 진행 · 예상');
    const eta = startedAt + plan.totalSeconds * 1000;
    const day = arrivalDayLabel(startedAt, eta);
    $('trip-eta').textContent = '하차 예상 ' + (day === '당일' ? '' : day + ' ') + timeInKorea(eta);
    if (activePhase !== current.phase || current.complete) {
      activePhase = current.phase;
      $('trip-status').textContent = current.complete ? plan.finalName + ' 도착 예상' : current.phase.label;
      $('trip-next').textContent = current.complete ? '목적지까지 남은 도보는 별도로 확인하세요.' : '다음 지점 · ' + current.phase.nextName;
    }
    $('traveler-label').textContent = current.complete ? '하차 예상' : '내 예상 위치';
    $('traveler-marker').setAttribute('data-state', current.complete ? 'complete' : current.phase.kind === 'waiting' ? 'waiting' : 'moving');
    placeMarker(current.position, true);
    if (current.complete) { stopClock(); $('trip-start').textContent = '다시 출발'; }
  }
  function idle() {
    stopClock(); startedAt = null; activePhase = null;
    $('trip-start').textContent = '출발'; $('trip-start').disabled = !plan;
    $('trip-status').textContent = plan ? '출발하면 경로를 따라 안내해요' : '이 경로는 이동시간이 부족해 안내할 수 없어요';
    $('trip-next').textContent = 'GPS가 아닌 시간 기반 예상 위치입니다.';
    $('trip-elapsed').textContent = '출발 전'; $('trip-remaining').textContent = plan ? '예상 ' + formatDuration(plan.totalSeconds) : '';
    $('trip-eta').textContent = ''; $('trip-progress').value = 0;
    $('trip-progress').setAttribute('aria-valuetext', '출발 전');
    $('traveler-label').textContent = '출발지'; $('traveler-marker').hidden = !plan;
    $('traveler-marker').setAttribute('data-state', 'idle');
    $('trip-method').textContent = plan?.explanation || '확인되지 않은 소요시간으로 위치를 만들지 않습니다.';
    placeMarker(0);
  }
  $('trip-start').addEventListener('click', () => {
    if (!plan) return;
    if (interval != null) { idle(); return; }
    startedAt = Date.now(); activePhase = null; $('trip-start').textContent = '안내 종료';
    tick(); interval = setInterval(tick, 1000);
  });
  if (typeof window !== 'undefined') window.addEventListener('resize', () => placeMarker(startedAt == null || !plan ? 0 : progressAt(plan, (Date.now() - startedAt) / 1000).position));
  return {setRoute(route) { plan = buildProgressPlan(route); idle(); }, reset() { plan = null; idle(); }};
}
