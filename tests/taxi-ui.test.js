import test from 'node:test';
import assert from 'node:assert/strict';
import {createTaxiUI, suggestedTaxiDeparture} from '../frontend/taxi-ui.js';

const setup = (t, post, configured = true) => {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {textContent: '', children: [], value: '', validity: {valid: true},
      append(...children) {this.children.push(...children);}, replaceChildren(...children) {this.children = children;},
      addEventListener(name, fn) {this[name] = fn;}, setAttribute(name, value) {this[name] = value;}});
    return elements.get(id);
  };
  const old = globalThis.document;
  globalThis.document = {getElementById: get, createElement: () => ({textContent: '', children: [], append(...items) {this.children.push(...items);}})};
  t.after(() => {globalThis.document = old;});
  const context = {config: {taxiConfigured: configured}, body: {departure: '2026-09-12T23:00'},
    response: {routeToken: 'token', destination: {name: '집'}}, route: {id: 'r1'}};
  const ui = createTaxiUI({post}); ui.setRoute(context); ui.selectPoint({kind: 'boarding', rideIndex: 1, name: '환승역'}, '2026-09-12T23:50');
  const text = element => (element.textContent || '') + (element.children || []).map(text).join(' ');
  return {get, ui, context, text};
};
const payload = {fareWon: 10300, tollWon: 0, durationSeconds: 1200, distanceMeters: 5000,
  departure: '2026-09-12T23:50:00+09:00', arrival: '2026-09-13T00:10:00+09:00', notice: '실제 요금과 다를 수 있음', timeBasis: 'future'};

test('택시 기본 탑승 시각은 과거를 피하고 한국 시간으로 표시한다', () => {
  const now = Date.parse('2026-09-12T23:49:30+09:00');
  assert.equal(suggestedTaxiDeparture('2026-09-12T20:00', now), '2026-09-12T23:50');
  assert.equal(suggestedTaxiDeparture('2026-09-13T00:30', now), '2026-09-13T00:30');
});
test('선택한 지점·시각으로 조회하고 예상 요금·날짜·주의사항을 표시한다', async t => {
  const calls = [];
  const {get, text} = setup(t, async (url, body) => {calls.push({url, body}); return payload;});
  assert.equal(calls.length, 0);
  get('taxi-departure').value = '2026-09-12T23:50'; await get('taxi-button').click();
  assert.equal(calls[0].url, '/api/taxi'); assert.equal(calls[0].body.rideIndex, 1);
  assert.match(text(get('taxi-result')), /약 10,300원/);
  assert.match(text(get('taxi-result')), /다음 날 00:10/);
  assert.match(text(get('taxi-result')), /할증 및 호출료 포함 여부 미확인/);
  assert.equal(get('taxi-button').disabled, false);
});
test('입력·경로 변경 후 늦게 도착한 요금은 표시하지 않는다', async t => {
  let resolve, signal;
  const {get, ui} = setup(t, (url, body, currentSignal) => {signal = currentSignal; return new Promise(done => {resolve = done;});});
  const pending = get('taxi-button').click(); get('taxi-departure').input();
  assert.equal(signal.aborted, true); resolve(payload); await pending;
  assert.equal(get('taxi-result').children.length, 0);
  const pendingAgain = get('taxi-button').click(); ui.reset(); resolve(payload); await pendingAgain;
  assert.equal(get('taxi-panel').hidden, true); assert.equal(get('taxi-result').children.length, 0);
});
test('실패한 조회는 재시도할 수 있고 키가 없으면 호출하지 않는다', async t => {
  const {get, text} = setup(t, async () => {throw new Error('권한 확인 필요');});
  await get('taxi-button').click(); assert.match(text(get('taxi-result')), /권한 확인 필요/);
  assert.equal(get('taxi-button').disabled, false);
});
test('키가 없으면 버튼을 비활성화하고 미확인 요금은 0원으로 표시하지 않는다', async t => {
  let called = false;
  const {get, ui, context, text} = setup(t, async () => {called = true; return {...payload, fareWon: null};}, false);
  await get('taxi-button').click(); assert.equal(called, false); assert.equal(get('taxi-button').disabled, true);
  context.config.taxiConfigured = true; ui.setRoute(context); ui.selectPoint({kind: 'origin', name: '학교'});
  await get('taxi-button').click(); assert.match(text(get('taxi-result')), /요금 미확인/);
});
