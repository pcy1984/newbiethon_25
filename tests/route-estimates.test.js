import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildRouteView, buildJourneyGraph, segmentMetrics, coordinateDistance, distanceLabel} from '../frontend/utils.js';
import {buildProgressPlan} from '../frontend/journey-progress.js';

const origin={name:'보문역',x:127.018,y:37.585}, destination={name:'노원역',x:127.061,y:37.656};
function route() {return {departure:'2026-09-12T23:00:00+09:00',accessMinutes:0,bufferMinutes:5,scheduleStatus:'complete',durationSeconds:960,steps:[
 {type:'WAITING',title:'승차 대기',durationSeconds:60,distanceMeters:null},
 {type:'SUBWAY',title:'우이신설',vehicles:['우이신설'],stops:['보문','성신여대입구'],durationSeconds:120,distanceMeters:1100,departure:'2026-09-12T23:01:00+09:00',arrival:'2026-09-12T23:03:00+09:00'},
 {type:'WALKING',title:'환승 도보',durationSeconds:139,distanceMeters:null,stops:['성신여대입구','성신여대입구']},
 {type:'WAITING',title:'4호선 대기',durationSeconds:41,distanceMeters:null},
 {type:'SUBWAY',title:'4호선',vehicles:['4호선'],stops:['성신여대입구','중간역','노원'],durationSeconds:600,distanceMeters:6500,departure:'2026-09-12T23:06:00+09:00',arrival:'2026-09-12T23:16:00+09:00'},
]};}
test('API 순서와 139초 환승·41초 대기를 유지하며 원본을 바꾸지 않는다',()=>{
 const r=route(), before=structuredClone(r), view=buildRouteView(r,origin,destination);
 assert.deepEqual(r,before);assert.equal(view.totalSeconds,960);
 const transfer=view.segments.find(s=>s.title==='환승 도보');assert.equal(transfer.durationSeconds,139);assert.equal(transfer.distanceSource,'estimated');assert.ok(transfer.distanceMeters>0);
 assert.equal(view.segments.find(s=>s.title==='4호선 대기').durationSeconds,41);
 assert.match(segmentMetrics(transfer),/^약 [\d,]+m · 2분 19초$/);
 assert.doesNotMatch(segmentMetrics(transfer),/예상|API|입력값/);
 assert.equal(view.segments.filter(s=>s.kind==='ride')[1].distanceMeters,6500);
 const graph=buildJourneyGraph(r,origin.name,view);assert.match(graph[5].detail,/41초/);
 assert.equal(buildProgressPlan({...r,presentation:view}).totalSeconds,960);
});
test('확인된 구간은 거리·시간만, 추정값은 약으로 짧게 구분한다',()=>{
 assert.equal(segmentMetrics({type:'SUBWAY',distanceMeters:1200,durationSeconds:180,timeSource:'api',distanceSource:'api'}),'1,200m · 3분');
 assert.equal(segmentMetrics({type:'WAITING',durationSeconds:300,timeSource:'estimated',estimated:true}),'약 5분');
});
test('미제공 버스 구간 시간을 총시간 안에서 배분한다',()=>{
 const r={departure:'2026-09-12T22:00:00+09:00',durationSeconds:1800,accessMinutes:0,bufferMinutes:5,steps:[{type:'BUS',title:'101',stops:['보문','노원'],durationSeconds:null,distanceMeters:null,fromX:127.018,fromY:37.585,toX:127.061,toY:37.656}]};
 const view=buildRouteView(r,origin,destination), ride=view.segments.find(s=>s.kind==='ride');
 assert.equal(view.totalSeconds,1800);assert.equal(ride.timeSource,'estimated');assert.ok(ride.distanceMeters>0);assert.equal(ride.departure,undefined);
});
test('최종 정류장과 목적지가 다르면 도보와 목적지 노드를 포함한다',()=>{
 const r=route(), to={name:'목적지 카페',x:127.065,y:37.657};r.steps.at(-1).toX=127.061;r.steps.at(-1).toY=37.656;
 const view=buildRouteView(r,origin,to);assert.equal(view.hasTail,true);assert.ok(view.totalSeconds>r.durationSeconds);
 const graph=buildJourneyGraph(r,origin.name,view);assert.equal(graph.at(-1).name,to.name);assert.equal(buildProgressPlan({...r,presentation:view}).finalNode,graph.filter(n=>n.type==='node').length-1);
});
test('잘못된 좌표·키 미설정은 예제 경로로 바꾸지 않는다',()=>{
 assert.equal(coordinateDistance({x:NaN,y:37},destination),null);assert.equal(coordinateDistance({x:999,y:37},destination),null);
 assert.equal(distanceLabel(1750),'1,750m');
 const html=readFileSync(new URL('../frontend/index.html',import.meta.url),'utf8');assert.doesNotMatch(html,/id="examples"|id="example-buttons"|id="demo-banner"/);
});
