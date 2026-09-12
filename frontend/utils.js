export function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '확인 필요';
  const minutes = Math.ceil(Math.max(0, seconds) / 60);
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}시간${minutes % 60 ? ` ${minutes % 60}분` : ''}` : `${minutes}분`;
}

export function timeInKorea(value) {
  return new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(value));
}

export function roundUpToMinute(value) {
  return new Date(Math.ceil(new Date(value).getTime() / 60000) * 60000).toISOString();
}

export function localKoreaInput(date = new Date()) {
  return new Date(date.getTime() + 9 * 60 * 60 * 1000).toISOString().slice(0, 16);
}

export function arrivalDayLabel(departure, arrival) {
  const day = (date) => Math.floor((new Date(date).getTime() + 9 * 3600000) / 86400000);
  const difference = day(arrival) - day(departure);
  return difference === 0 ? '당일' : difference === 1 ? '다음 날' : `${difference}일 뒤`;
}

export function samePlace(a, b) {
  if (a && b && a.x == null && b.x == null) return a.name.trim().replace(/역$/, '') === b.name.trim().replace(/역$/, '');
  return Boolean(a && b && Math.abs(a.x - b.x) < 0.000001 && Math.abs(a.y - b.y) < 0.000001);
}

export function distanceLabel(meters) {
  if (meters == null || !Number.isFinite(meters)) return '';
  return `${Math.round(meters).toLocaleString('ko-KR')}m`;
}

export function buildJourneyGraph(route, originName, view = null) {
  const rides = route.steps.filter(step => step.type === 'SUBWAY' || step.type === 'BUS');
  const graph = [{type: 'node', kind: 'origin', name: originName, label: '출발지'}];
  for (const [index, step] of rides.entries()) {
    const gaps = view?.segments.filter(s => s.kind !== 'ride' && s.nextRide === index) || [];
    graph.push({type: 'edge', kind: index === 0 ? 'access' : 'transfer',
      label: index === 0 ? '첫 승차 지점까지' : '환승 이동·대기',
      detail: view ? gaps.map(s => (s.kind === 'waiting' ? '대기 ' : '도보 ') + segmentMetrics(s)).join(' / ') || '추가 이동 없음' : index === 0 ? (route.accessMinutes ?? 0) + '분 · 직접 입력' : (route.bufferMinutes ?? 5) + '분 · 여유 설정'});
    graph.push({type: 'node', kind: 'boarding', name: step.stops[0], label: index === 0 ? '첫 승차' : '환승 후 승차', step, rideIndex: index});
    graph.push({type: 'edge', kind: 'ride', label: (step.vehicles || [])[0] || step.title, detail: view ? segmentMetrics(view.segments.find(s => s.kind === 'ride' && s.rideIndex === index)) : formatDuration(step.durationSeconds), step});
    graph.push({type: 'node', kind: 'alighting', name: step.stops.at(-1), label: index === rides.length - 1 ? '최종 하차' : '하차 · 환승', final: index === rides.length - 1, step, rideIndex: index});
  }
  if (view?.hasTail) {
    graph.push({type: 'edge', kind: 'transfer', label: '목적지까지 도보', detail: view.segments.filter(s => s.nextRide === rides.length).map(segmentMetrics).join(' / ')});
    graph.push({type: 'node', kind: 'destination', name: view.finalName, label: '목적지', final: true});
  }
  return graph;
}

const nonnegative = n => typeof n === 'number' && Number.isFinite(n) && n >= 0;
const sameName = (a, b) => (a || '').replace(/역(?:\s.*)?$/, '').trim() === (b || '').replace(/역(?:\s.*)?$/, '').trim();
const speed = type => type === 'SUBWAY' ? 30000 / 3600 : type === 'BUS' ? 18000 / 3600 : 4000 / 3600;
export function coordinateDistance(a, b) {
  if (![a?.x, a?.y, b?.x, b?.y].every(Number.isFinite) || [a, b].some(p => p.x < 124 || p.x > 132 || p.y < 33 || p.y > 40)) return null;
  const radians = n => n * Math.PI / 180;
  const dlat = radians(b.y - a.y), dlon = radians(b.x - a.x);
  const v = Math.sin(dlat / 2) ** 2 + Math.cos(radians(a.y)) * Math.cos(radians(b.y)) * Math.sin(dlon / 2) ** 2;
  return 6371000 * 2 * Math.atan2(Math.sqrt(v), Math.sqrt(Math.max(0, 1 - v)));
}
export function detailedDuration(seconds) {
  if (!nonnegative(seconds)) return '시간 미확인';
  const n = Math.round(seconds);
  return n < 60 ? `${n}초` : `${Math.floor(n / 60)}분${n % 60 ? ` ${n % 60}초` : ''}`;
}
export function segmentMetrics(step) {
  if (!step) return '확인 필요';
  const time = step.timeSource === 'estimated' ? '약 ' + formatDuration(step.durationSeconds) : detailedDuration(step.durationSeconds);
  const distance = step.type === 'WAITING' ? '' : (step.distanceSource === 'estimated' ? '약 ' : '') + distanceLabel(step.distanceMeters);
  return [distance, time].filter(Boolean).join(' · ');
}

// Presentation-only estimates never change the server snapshot or last-train calculation.
export function buildRouteView(route, origin = {}, destination = {}) {
  const rides = route.steps.filter(s => ['BUS', 'SUBWAY'].includes(s.type));
  if (!rides.length) return {segments: [], totalSeconds: 0, finalNode: 0, finalName: destination.name, hasTail: false};
  const segments = [];
  const point = (s, prefix) => ({x: s[prefix + 'X'], y: s[prefix + 'Y']});
  const add = s => segments.push(s);
  const walk = (title, a, b, seconds, basis, kind = 'walk', fallbackMeters = 150) => {
    const straight = coordinateDistance(a, b);
    const distance = straight == null ? (nonnegative(seconds) ? seconds * speed('WALKING') : fallbackMeters) : straight * 1.3;
    return {type: 'WALKING', kind, title, durationSeconds: nonnegative(seconds) ? seconds : Math.ceil(distance / speed('WALKING')),
      distanceMeters: Math.round(distance), timeSource: nonnegative(seconds) ? 'user' : 'estimated', distanceSource: 'estimated', estimated: true,
      basis: basis + (straight == null ? ' 좌표 미제공 시 입력시간×4km/h 또는 임시 150m(마지막 도보 300m) 가정.' : ' 거리는 양끝 좌표의 직선거리×1.3, 자동 도보시간은 4km/h 가정.')};
  };
  add(walk(`${origin.name || '출발지'} → ${rides[0].stops[0]}`, origin, point(rides[0], 'from'), (route.accessMinutes ?? 0) * 60, '접근시간은 이동 설정의 직접 입력값입니다.', 'access'));
  let rideIndex = 0, sinceRide = [], previousRide = null;
  for (const raw of route.steps) {
    const isRide = ['BUS', 'SUBWAY'].includes(raw.type);
    if (isRide) {
      if (previousRide && !sinceRide.some(s => s.type === 'WALKING') && route.scheduleStatus !== 'complete') {
        const sameStop = raw.type === 'BUS' && previousRide.type === 'BUS' && sameName(previousRide.stops.at(-1), raw.stops[0]);
        if (!sameStop) add(walk(`${previousRide.stops.at(-1)} → ${raw.stops[0]} 환승`, point(previousRide, 'to'), point(raw, 'from'), null, '미제공 환승 도보 추정.'));
      }
      const consumed = segments.every(s => nonnegative(s.durationSeconds)) ? segments.reduce((n, s) => n + s.durationSeconds, 0) : null;
      const gap = consumed == null ? NaN : (Date.parse(raw.departure) - Date.parse(route.departure)) / 1000 - consumed;
      if (Number.isFinite(gap) && gap > 0) {
        const assumed = segments.some(s => s.timeSource === 'estimated');
        add({type: 'WAITING', kind: 'waiting', title: raw.stops[0] + ' 승차 대기', durationSeconds: gap,
          timeSource: assumed ? 'estimated' : 'api', distanceMeters: null, estimated: assumed,
          basis: assumed ? '열차 출발시각에서 추정 도보시간을 뺀 남은 대기시간입니다.' : 'API 열차 출발시각까지 남은 대기시간입니다.'});
      } else if (!sinceRide.some(s => s.type === 'WAITING') && !raw.departure) {
        add({type: 'WAITING', kind: 'waiting', title: raw.stops[0] + ' 승차 대기', durationSeconds: (route.bufferMinutes ?? 5) * 60,
          timeSource: 'estimated', distanceMeters: null, estimated: true, basis: '실제 배차 대기가 아니라 선택한 5·10분 여유를 대기로 가정합니다.'});
      }
    }
    const s = {...raw, kind: isRide ? 'ride' : raw.type === 'WAITING' ? 'waiting' : 'walk',
      timeSource: nonnegative(raw.durationSeconds) ? 'api' : 'estimated', distanceSource: nonnegative(raw.distanceMeters) && (raw.distanceMeters > 0 || raw.durationSeconds === 0) ? 'api' : 'estimated',
      basis: nonnegative(raw.durationSeconds) ? 'API가 반환한 구간시간을 유지합니다.' : '구간시간 미제공: 전체 소요시간의 남은 부분을 거리 비율로 배분합니다.'};
    if (isRide) { s.rideIndex = rideIndex++; previousRide = raw; sinceRide = []; }
    else sinceRide.push(raw);
    if (s.type !== 'WAITING' && s.distanceSource === 'estimated') {
      const straight = coordinateDistance(point(raw, 'from'), point(raw, 'to'));
      s.distanceMeters = straight == null ? null : Math.round(straight * (s.type === 'WALKING' ? 1.3 : 1.2));
      s.basis += straight == null ? ' 거리가 없으면 구간시간×가정속도(도보 4·버스 18·지하철 30km/h)로 추정합니다.' : ' 거리는 좌표 직선거리×우회계수(도보 1.3·차량 1.2) 추정입니다.';
    }
    add(s);
  }
  const missing = segments.filter(s => !nonnegative(s.durationSeconds));
  const known = segments.reduce((sum, s) => sum + (nonnegative(s.durationSeconds) ? s.durationSeconds : 0), 0);
  const remaining = Math.max(0, (route.durationSeconds || 0) - known);
  const weights = missing.map(s => s.distanceMeters > 0 ? s.distanceMeters / speed(s.type) : 1);
  const totalWeight = weights.reduce((a, b) => a + b, 0);
  missing.forEach((s, i) => {
    s.durationSeconds = remaining > 0 ? remaining * weights[i] / totalWeight : Math.max(60, s.distanceMeters ? s.distanceMeters / speed(s.type) : 600);
    if (!remaining) s.basis += ' 총시간 배분이 불가능해 거리÷가정속도, 거리도 없으면 임시 10분을 사용했습니다.';
  });
  for (const s of segments) {
    if (s.type !== 'WAITING' && !nonnegative(s.distanceMeters)) s.distanceMeters = Math.round(s.durationSeconds * speed(s.type));
    s.estimated = s.timeSource === 'estimated' || (s.type !== 'WAITING' && s.distanceSource === 'estimated');
  }
  const tailExists = segments.at(-1)?.type === 'WALKING';
  const finalRide = rides.at(-1), finalDistance = coordinateDistance(point(finalRide, 'to'), destination);
  const hasTail = tailExists || Boolean(destination.name && !sameName(finalRide.stops.at(-1), destination.name) && (finalDistance == null || finalDistance > 10));
  if (hasTail && !tailExists) add(walk(`${finalRide.stops.at(-1)} → ${destination.name}`, point(finalRide, 'to'), destination, null, '최종 하차 후 도보는 총시간에 별도로 더한 추정값입니다.', 'tail', 300));
  let nextRide = rides.length;
  for (let i = segments.length - 1; i >= 0; i--) {
    const s = segments[i];
    if (s.kind === 'ride') nextRide = s.rideIndex;
    s.nextRide = nextRide;
    if (s.kind === 'ride') { s.fromNode = s.rideIndex * 2 + 1; s.toNode = s.fromNode + 1; }
    else if (s.kind === 'waiting') { s.fromNode = s.toNode = Math.min(nextRide * 2 + 1, rides.length * 2); }
    else { s.fromNode = nextRide * 2; s.toNode = Math.min(s.fromNode + 1, rides.length * 2 + (hasTail ? 1 : 0)); }
  }
  return {segments, totalSeconds: segments.reduce((sum, s) => sum + s.durationSeconds, 0), hasTail,
    finalNode: rides.length * 2 + (hasTail ? 1 : 0), finalName: hasTail ? destination.name : finalRide.stops.at(-1),
    estimated: segments.some(s => s.estimated), timeEstimated: segments.some(s => s.timeSource === 'estimated')};
}
