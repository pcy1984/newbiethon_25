import test from 'node:test';
import assert from 'node:assert/strict';
import {buildProgressPlan, progressAt, createJourneyTracker} from '../frontend/journey-progress.js';

const railRoute = () => ({
  departure: '2026-09-12T23:40:00+09:00', scheduleStatus: 'complete',
  accessMinutes: 10, bufferMinutes: 5, durationSeconds: 3600,
  steps: [
    {type: 'SUBWAY', vehicles: ['2호선'], stops: ['출발역', '환승역'], durationSeconds: 1200,
      departure: '2026-09-12T23:55:00+09:00', arrival: '2026-09-13T00:15:00+09:00'},
    {type: 'SUBWAY', vehicles: ['4호선'], stops: ['환승역', '도착역'], durationSeconds: 1200,
      departure: '2026-09-13T00:20:00+09:00', arrival: '2026-09-13T00:40:00+09:00'},
  ],
});
const busRoute = () => ({
  accessMinutes: 10, bufferMinutes: 5, durationSeconds: 3900,
  steps: [
    {type: 'BUS', vehicles: ['201'], stops: ['출발 정류장', '환승 정류장'], durationSeconds: null},
    {type: 'BUS', vehicles: ['273'], stops: ['환승 정류장', '하차 정류장'], durationSeconds: null},
  ],
});

test('심야 시간표의 접근·대기·승차·환승 간격을 중복 없이 사용한다', () => {
  const plan = buildProgressPlan(railRoute());
  assert.equal(plan.approximate, false);
  assert.equal(plan.totalSeconds, 3600);
  assert.deepEqual(plan.phases.map(p => [p.start, p.end, p.kind]), [
    [0, 600, 'access'], [600, 900, 'waiting'], [900, 2100, 'ride'],
    [2100, 2400, 'transfer'], [2400, 3600, 'ride'],
  ]);
  assert.match(plan.explanation, /실제 열차 시각을 다시 조회하지 않습니다/);
});

test('진행 위치는 대기 중 정지하고 승차·환승 중 선형 이동한다', () => {
  const plan = buildProgressPlan(railRoute());
  assert.equal(progressAt(plan, 300).position, 0.5);
  assert.equal(progressAt(plan, 750).position, 1);
  assert.equal(progressAt(plan, 900).phase.kind, 'ride');
  assert.equal(progressAt(plan, 1500).position, 1.5);
  assert.equal(progressAt(plan, 2250).position, 2.5);
  assert.equal(progressAt(plan, 3000).position, 3.5);
});

test('완료 시 최종 하차 지점에서 멈추고 시간 초과·음수 입력을 제한한다', () => {
  const plan = buildProgressPlan(railRoute());
  const result = progressAt(plan, 90000);
  assert.equal(result.complete, true);
  assert.equal(result.position, 4);
  assert.equal(result.ratio, 1);
  assert.equal(result.remaining, 0);
  assert.equal(progressAt(plan, -10).position, 0);
  assert.equal(progressAt(plan, NaN).position, 0);
});

test('버스 구간 시간이 없으면 전체 시간에서 접근·여유를 빼 균등 배분한다', () => {
  const plan = buildProgressPlan(busRoute());
  assert.equal(plan.approximate, true);
  assert.equal(plan.totalSeconds, 3900);
  assert.deepEqual(plan.phases.filter(p => p.kind === 'ride').map(p => p.end - p.start), [1350, 1350]);
  assert.match(plan.explanation, /균등 배분/);
});

test('확인된 구간 시간을 유지하고 남은 시간만 미확인 구간에 배분한다', () => {
  const route = busRoute(); route.steps[0].durationSeconds = 1200;
  const plan = buildProgressPlan(route);
  assert.deepEqual(plan.phases.filter(p => p.kind === 'ride').map(p => p.end - p.start), [1200, 1500]);
});

test('승차 구간 또는 추정 근거가 없으면 안내 계획을 만들지 않는다', () => {
  assert.equal(buildProgressPlan({steps: []}), null);
  const route = busRoute(); route.durationSeconds = 0;
  assert.equal(buildProgressPlan(route), null);
  route.accessMinutes = 0; route.bufferMinutes = 0;
  route.steps.forEach(step => {step.durationSeconds = 0;});
  assert.equal(buildProgressPlan(route), null);
});

test('순서가 잘못된 시간표는 확정 구간으로 재생하지 않는다', () => {
  const route = railRoute(); route.steps[1].departure = route.departure;
  assert.equal(buildProgressPlan(route).approximate, true);
});

test('출발·중단·재출발·경로 변경과 백그라운드 경과시간을 처리한다', t => {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', hidden: false, disabled: false, style: {},
      setAttribute(name, value) { this[name] = value; }, querySelectorAll: () => [],
      addEventListener(name, fn) { this[name] = fn; },
    });
    return elements.get(id);
  };
  const previousDocument = globalThis.document;
  globalThis.document = {getElementById: get};
  t.after(() => {globalThis.document = previousDocument;});
  let now = Date.parse('2026-09-12T23:40:00+09:00');
  let timerCallback, liveTimers = 0;
  t.mock.method(Date, 'now', () => now);
  t.mock.method(globalThis, 'setInterval', callback => {timerCallback = callback; liveTimers++; return 1;});
  t.mock.method(globalThis, 'clearInterval', () => {liveTimers--;});
  const tracker = createJourneyTracker(); tracker.setRoute(railRoute());
  assert.equal(get('traveler-marker')['data-state'], 'idle');
  get('trip-start').click();
  assert.equal(get('traveler-marker')['data-state'], 'moving');
  assert.equal(get('trip-start').textContent, '안내 종료');
  assert.match(get('trip-eta').textContent, /다음 날 00:40/);
  now += 750 * 1000; timerCallback();
  assert.equal(get('traveler-marker')['data-state'], 'waiting');
  now += 750 * 1000; timerCallback();
  assert.equal(get('traveler-marker')['data-state'], 'moving');
  assert.match(get('trip-elapsed').textContent, /25분 00초/);
  assert.match(get('trip-status').textContent, /2호선 이동/);
  get('trip-start').click();
  assert.equal(liveTimers, 0);
  assert.equal(get('trip-elapsed').textContent, '출발 전');
  assert.equal(get('traveler-marker')['data-state'], 'idle');
  get('trip-start').click();
  tracker.setRoute(busRoute());
  assert.equal(liveTimers, 0);
  assert.equal(get('trip-start').textContent, '출발');
  get('trip-start').click(); now += 5000 * 1000; timerCallback();
  assert.equal(get('trip-progress').value, 100);
  assert.equal(get('trip-start').textContent, '다시 출발');
  assert.match(get('trip-status').textContent, /하차 정류장 도착 예상/);
  assert.equal(get('traveler-marker')['data-state'], 'complete');
  assert.equal(liveTimers, 0);
  get('trip-start').click(); tracker.reset();
  assert.equal(liveTimers, 0);
  assert.equal(get('trip-start').disabled, true);
  assert.equal(get('traveler-marker').hidden, true);
  assert.equal(get('traveler-marker')['data-state'], 'idle');
});
