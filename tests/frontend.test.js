import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import { formatDuration, timeInKorea, localKoreaInput, arrivalDayLabel, samePlace, distanceLabel, roundUpToMinute, buildJourneyGraph } from '../frontend/utils.js';

test('사이트 이름·대상·서울권 안내와 정보 한계를 함께 표시한다', () => {
  const html = readFileSync(new URL('../frontend/index.html', import.meta.url), 'utf8');
  assert.match(html, /<title>집엔 가야지 — 서울권 대학생을 위한 밤 귀가 가이드<\/title>/);
  assert.match(html, /aria-label="집엔 가야지 홈"/);
  const notice = html.match(/<p id="coverage-note"[^>]*>([\s\S]*?)<\/p>/)?.[1];
  assert.ok(notice);
  assert.match(notice, /현재 서울권만 안내해요/);
  assert.match(notice, /일부 노선·막차 시각은 확인되지 않을 수/);
  assert.match(html, /<form[^>]*aria-describedby="coverage-note"/);
  assert.doesNotMatch(html, /집갈시간/);
});

test('분 단위 올림과 시간 표시', () => {
  assert.equal(formatDuration(null), '확인 필요');
  assert.equal(formatDuration(0), '0분');
  assert.equal(formatDuration(61), '2분');
  assert.equal(formatDuration(2940), '49분');
  assert.equal(formatDuration(3600), '1시간');
  assert.equal(formatDuration(3660), '1시간 1분');
});

test('새 테마에서도 출발 마감 우선·검색·진행 안내의 연결을 유지한다', () => {
  const html = readFileSync(new URL('../frontend/index.html', import.meta.url), 'utf8');
  const theme = readFileSync(new URL('../frontend/theme.css', import.meta.url), 'utf8');
  assert.match(html, /href="\/theme\.css"/);
  assert.match(html, /class="traveler-avatar"[^>]*>[\s\S]*?src="\/companion\.svg"/);
  assert.ok(html.indexOf('id="deadline-time"') < html.indexOf('id="route-line"'));
  assert.ok(html.indexOf('id="route-line"') < html.indexOf('id="route-options"'));
  assert.match(html, /GPS가 아닌 시간 기반 예상 위치/);
  assert.match(theme, /prefers-reduced-motion:\s*reduce/);
  assert.match(theme, /max-width:\s*390px/);
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length);
});

test('직선 경로에서 출발·승차·하차·환승을 각각 클릭 가능하게 분리', () => {
  const graph = buildJourneyGraph({accessMinutes: 10, bufferMinutes: 5, steps: [
    {type: 'SUBWAY', title: '2호선', stops: ['홍대입구', '합정'], durationSeconds: 180},
    {type: 'BUS', title: '271', stops: ['합정역 정류장', '종점'], durationSeconds: null},
  ]}, '카페');
  assert.deepEqual(graph.filter(x => x.type === 'node').map(x => x.name), ['카페', '홍대입구', '합정', '합정역 정류장', '종점']);
  assert.deepEqual(graph.filter(x => x.type === 'edge').map(x => x.kind), ['access', 'ride', 'transfer', 'ride']);
  assert.equal(graph.at(-1).final, true);
  assert.equal(graph.at(-2).detail, '확인 필요');
  assert.equal(graph[1].detail, '10분 · 직접 입력');
});

test('막차 마감 시각은 늦은 분으로 올리지 않음', () => {
  assert.equal(timeInKorea('2026-09-12T22:55:25+09:00'), '22:55');
});
test('브라우저 시간대와 무관하게 한국 시간 표시', () => {
  assert.equal(timeInKorea('2026-09-12T15:29:00Z'), '00:29');
  assert.equal(localKoreaInput(new Date('2026-09-12T15:29:00Z')), '2026-09-13T00:29');
});
test('자정과 연말을 넘어가는 도착일', () => {
  assert.equal(arrivalDayLabel('2026-09-12T22:00:00+09:00', '2026-09-12T23:00:00+09:00'), '당일');
  assert.equal(arrivalDayLabel('2026-12-31T23:40:00+09:00', '2027-01-01T00:29:00+09:00'), '다음 날');
  assert.equal(arrivalDayLabel('2026-12-31T23:40:00+09:00', '2027-01-02T00:29:00+09:00'), '2일 뒤');
});
test('동일 좌표와 미선택', () => {
  assert.equal(samePlace(null, { x: 127, y: 37 }), false);
  assert.equal(samePlace({ x: 127, y: 37 }, { x: 127, y: 37 }), true);
  assert.equal(samePlace({ x: 127, y: 37 }, { x: 126, y: 37 }), false);
});
test('거리 단위 표시', () => {
  assert.equal(distanceLabel(900), '900m');
  assert.equal(distanceLabel(1750), '1.8km');
});
test('분 올림한 소요시간과 도착 시각이 일치하고 자정도 처리', () => {
  assert.equal(timeInKorea(roundUpToMinute('2026-09-12T22:01:01+09:00')), '22:02');
  const rounded = roundUpToMinute('2026-09-12T23:59:30+09:00');
  assert.equal(timeInKorea(rounded), '00:00');
  assert.equal(arrivalDayLabel('2026-09-12T23:00:00+09:00', rounded), '다음 날');
});
