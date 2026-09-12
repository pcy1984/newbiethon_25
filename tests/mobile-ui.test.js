import test from 'node:test';
import assert from 'node:assert/strict';
import {createMobileSearch, MOBILE_QUERY} from '../frontend/mobile-ui.js';

function setup(matches = true) {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {
      hidden: false, textContent: '', attrs: {}, classes: {},
      classList: {toggle(name, value) { get(id).classes[name] = value; }},
      setAttribute(name, value) { this.attrs[name] = value; },
      addEventListener(name, callback) { this[name] = callback; },
      contains() { return false; }, scrollIntoView() {},
    });
    return elements.get(id);
  };
  const doc = {getElementById: get, activeElement: null, body: get('body')};
  const media = {matches, addEventListener(name, callback) { this[name] = callback; }};
  const ui = createMobileSearch(doc, media);
  const route = () => ui.setRoute({origin: {name: '홍대입구역'}, destination: {name: '서울역'}}, {departure: '2026-09-12T23:00'});
  return {get, doc, media, ui, route};
}

test('모바일 첫 진입은 검색창을 열고, 조회 뒤 선택한 장소와 시각으로 접는다', () => {
  const {get, route} = setup();
  assert.equal(get('search-fields').hidden, false);
  assert.equal(get('search-toggle').hidden, true);
  route();
  assert.equal(get('search-fields').hidden, true);
  assert.equal(get('search-summary').hidden, false);
  assert.equal(get('search-origin').textContent, '홍대입구역');
  assert.equal(get('search-time').textContent, '2026-09-12 · 23:00 출발 예정');
  assert.equal(get('search-toggle').attrs['aria-expanded'], 'false');
});

test('검색 수정·다시 접기에서는 기존 경로 결과를 유지한다', () => {
  const {get, route} = setup(); route();
  get('search-toggle').click();
  assert.equal(get('search-fields').hidden, false);
  assert.equal(get('search-toggle').textContent, '접기');
  assert.equal(get('body').classes['has-results'], true);
  get('search-toggle').click();
  assert.equal(get('search-fields').hidden, true);
  assert.equal(get('search-destination').textContent, '서울역');
});

test('입력을 변경하면 요약을 숨기고 검색창을 다시 표시한다', () => {
  const {get, route, ui} = setup(); route(); ui.reset();
  assert.equal(get('search-fields').hidden, false);
  assert.equal(get('search-summary').hidden, true);
  assert.equal(get('search-toggle').hidden, true);
  assert.equal(get('body').classes['has-results'], false);
});

test('큰 화면은 검색창을 유지하고 화면 회전 후에도 접기 선택을 유지한다', () => {
  assert.equal(MOBILE_QUERY, '(max-width: 900px)');
  const {get, route, media} = setup(false); route();
  assert.equal(get('search-fields').hidden, false);
  assert.equal(get('search-toggle').hidden, true);
  media.matches = true; media.change();
  assert.equal(get('search-fields').hidden, true);
  get('search-toggle').click();
  media.matches = false; media.change();
  media.matches = true; media.change();
  assert.equal(get('search-fields').hidden, false);
});

test('접히는 입력창 안에 키보드 포커스를 남기지 않는다', () => {
  const {get, doc, route} = setup();
  let blurred = false;
  doc.activeElement = {blur() { blurred = true; }};
  get('search-fields').contains = target => target === doc.activeElement;
  route();
  assert.equal(blurred, true);
});
