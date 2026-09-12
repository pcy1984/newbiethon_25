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
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)}km` : `${Math.round(meters)}m`;
}

export function buildJourneyGraph(route, originName) {
  const rides = route.steps.filter(step => step.type === 'SUBWAY' || step.type === 'BUS');
  const graph = [{type: 'node', kind: 'origin', name: originName, label: '출발지'}];
  for (const [index, step] of rides.entries()) {
    graph.push({type: 'edge', kind: index === 0 ? 'access' : 'transfer',
      label: index === 0 ? '첫 승차 지점까지' : '환승 이동·대기',
      detail: index === 0 ? (route.accessMinutes ?? 0) + '분 · 직접 입력' : (route.bufferMinutes ?? 5) + '분 · 여유 설정'});
    graph.push({type: 'node', kind: 'boarding', name: step.stops[0], label: index === 0 ? '첫 승차' : '환승 후 승차', step, rideIndex: index});
    graph.push({type: 'edge', kind: 'ride', label: (step.vehicles || [])[0] || step.title, detail: formatDuration(step.durationSeconds), step});
    graph.push({type: 'node', kind: 'alighting', name: step.stops.at(-1), label: index === rides.length - 1 ? '최종 하차' : '하차 · 환승', final: index === rides.length - 1, step, rideIndex: index});
  }
  return graph;
}
