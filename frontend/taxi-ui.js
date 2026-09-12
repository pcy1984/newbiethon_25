import {formatDuration, timeInKorea, localKoreaInput, arrivalDayLabel, distanceLabel, roundUpToMinute} from './utils.js';

export function suggestedTaxiDeparture(value, now = Date.now()) {
  const parsed = Date.parse(value?.length === 16 ? value + ':00+09:00' : value);
  return localKoreaInput(new Date(Math.max(Math.ceil(now / 60000) * 60000, Number.isFinite(parsed) ? parsed : 0)));
}

export function createTaxiUI({post}) {
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls = '') => {const item = document.createElement(tag); item.textContent = text; item.className = cls; return item;};
  const won = value => Number.isFinite(value) ? value.toLocaleString('ko-KR') + '원' : '미확인';
  let context = null, point = null, pending = null, version = 0;
  const available = () => !!(context?.config.taxiConfigured && context.response.routeToken);
  function clear() {
    version++; pending?.abort(); pending = null;
    $('taxi-result').replaceChildren(); $('taxi-button').disabled = !available();
    $('taxi-result').setAttribute('aria-busy', 'false');
  }
  function reset() {context = point = null; clear(); $('taxi-panel').hidden = true;}
  function setRoute(value) {reset(); context = value;}
  function selectPoint(value, when) {
    point = value; clear(); $('taxi-panel').hidden = false;
    $('taxi-origin').textContent = value.name + ' → ' + context.response.destination.name;
    $('taxi-help').textContent = !context.config.taxiConfigured ? '카카오 REST 키를 설정하면 예상 요금을 조회할 수 있어요.' :
      !context.response.routeToken ? '실제 대중교통 경로를 검색한 뒤 사용할 수 있어요.' : '차량 경로 기준 예상 요금이에요. 실제 택시 호출·예약은 하지 않아요.';
    $('taxi-departure').value = suggestedTaxiDeparture(when);
  }
  async function quote() {
    if (!point || !available()) return;
    const input = $('taxi-departure');
    if (!input.value || !input.validity.valid) { $('taxi-result').replaceChildren(el('p', '예상 탑승 날짜·시각을 입력해 주세요.')); return; }
    clear(); const revision = version, departure = input.value, selected = point;
    pending = new AbortController(); $('taxi-button').disabled = true;
    $('taxi-result').setAttribute('aria-busy', 'true');
    $('taxi-result').replaceChildren(el('p', '택시 예상 요금과 이동시간을 확인하고 있어요…'));
    try {
      const data = await post('/api/taxi', {...context.body, routeToken: context.response.routeToken, routeId: context.route.id,
        pointKind: selected.kind, rideIndex: selected.rideIndex, taxiDeparture: departure}, pending.signal);
      if (revision !== version) return;
      const result = $('taxi-result'); result.replaceChildren();
      result.append(el('span', '택시 예상 요금 · 참고값', 'taxi-fare-label'),
        el('strong', data.fareWon == null ? '요금 미확인' : '약 ' + won(data.fareWon), 'taxi-fare'),
        el('p', distanceLabel(data.distanceMeters) + ' · 약 ' + formatDuration(data.durationSeconds)),
        el('p', arrivalDayLabel(data.departure, roundUpToMinute(data.arrival)) + ' ' + timeInKorea(roundUpToMinute(data.arrival)) + ' 도착 예상'),
        el('p', '통행료 안내 ' + won(data.tollWon) + ' · 위 요금에 별도 합산하지 않음', 'taxi-meta'),
        el('p', '심야·시외 할증 및 호출료 포함 여부 미확인', 'taxi-caution'));
      const details = el('details', '', 'taxi-notes');
      details.append(el('summary', '요금·시간 계산 기준'), el('p', data.notice),
        el('p', data.timeBasis === 'future' ? '입력한 탑승 시각의 예측 교통정보 기준입니다.' : '조회 시점의 교통정보 기준입니다.'));
      result.append(details);
    } catch (error) {
      if (revision === version && error.name !== 'AbortError') $('taxi-result').replaceChildren(el('p', error.message, 'taxi-caution'));
    } finally {
      if (revision === version) { $('taxi-button').disabled = !available(); $('taxi-result').setAttribute('aria-busy', 'false'); }
    }
  }
  $('taxi-button').addEventListener('click', quote);
  $('taxi-departure').addEventListener('input', clear);
  return {reset, setRoute, selectPoint};
}
