import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

// Minimal DOM contract test, not a replacement for visual browser testing.
class Element {
  constructor(tag='div') { this.tagName=tag; this.children=[]; this.attrs={}; this.events={}; this.dataset={}; this.style={setProperty(){}}; this.value=''; this.hidden=false; this.disabled=false; this.className=''; this.validity={valid:true}; this._text=''; }
  set textContent(value) { this._text=String(value); this.children=[]; }
  get textContent() { return this._text+this.children.map(c=>c.textContent).join(''); }
  get firstElementChild() { return this.children[0]; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text=''; this.children=children; }
  setAttribute(k,v) { this.attrs[k]=String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(k,fn) { (this.events[k]??=[]).push(fn); }
  async emit(k) { await Promise.all((this.events[k]||[]).map(fn=>fn({preventDefault(){},target:this}))); }
  get classList() { return {toggle:(name,on)=>{let classes=new Set(this.className.split(' ').filter(Boolean));on?classes.add(name):classes.delete(name);this.className=[...classes].join(' ');}}; }
  querySelectorAll(selector) { const all=this.children.flatMap(c=>[c,...c.querySelectorAll('*')]);return selector==='*'?all:all.filter(c=>selector.startsWith('.')?c.className.split(' ').includes(selector.slice(1)):false); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  scrollIntoView() {}
  focus() {}
}

const flush=async()=>{for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));};

test('앱 초기화 → 경로 → 시나리오 → 환승 지점 → 도보 → 입력 변경',async()=>{
  const html=readFileSync(new URL('../frontend/index.html',import.meta.url),'utf8');
  const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const get=id=>{assert.ok(elements.has(id),'HTML id exists: '+id);return elements.get(id);};
  const hero=new Element();
  globalThis.document={getElementById:get,createElement:tag=>new Element(tag),querySelector:s=>s==='.deadline-hero'?hero:null,addEventListener(){}};
  globalThis.matchMedia=()=>({matches:false});
  const interval=globalThis.setInterval;globalThis.setInterval=()=>0;
  get('submit-button').append(new Element('span'),new Element('span'));
  get('access-minutes').value='10';get('buffer-minutes').value='5';
  const origin={id:'a',name:'홍대입구역',x:126.92,y:37.55};
  const destination={id:'b',name:'서울역',x:126.97,y:37.55};
  const rides=[
    {type:'SUBWAY',title:'2호선',vehicles:['2호선'],stops:['홍대입구','사당'],durationSeconds:1200,departure:'2026-09-12T23:05:00+09:00',arrival:'2026-09-12T23:25:00+09:00'},
    {type:'SUBWAY',title:'4호선',vehicles:['4호선'],stops:['사당','서울역'],durationSeconds:1200,departure:'2026-09-12T23:35:00+09:00',arrival:'2026-09-12T23:55:00+09:00'},
  ];
  const route={id:'r1',type:'SUBWAY',label:'2호선 → 4호선',steps:rides,durationSeconds:4500,arrival:'2026-09-12T23:55:00+09:00',departure:'2026-09-12T22:40:00+09:00',accessMinutes:10,bufferMinutes:5,transfers:1,boardingStation:'홍대입구'};
  const busRoute={id:'b1',type:'BUS',label:'201 → 273',steps:[
    {type:'BUS',title:'201',vehicles:['201'],stops:['구리여중고','홍릉초등학교'],durationSeconds:null},
    {type:'BUS',title:'273',vehicles:['273'],stops:['홍릉초등학교','안암역2번출구'],durationSeconds:null},
  ],durationSeconds:3900,arrival:'2026-09-12T23:45:00+09:00',departure:'2026-09-12T22:40:00+09:00',accessMinutes:10,bufferMinutes:5,transfers:1,boardingStation:'구리여중고'};
  const point={rideIndex:1,station:'사당',line:'4호선',readyAt:'2026-09-12T23:27:00+09:00',boardAt:'2026-09-12T23:35:00+09:00',delayedReadyAt:'2026-09-12T23:32:00+09:00',appliedDelayMinutes:5};
  const stress={routeId:'r1',status:'evaluated',toleranceSeconds:180,checkpoints:[point],scenarios:[
    {delayMinutes:0,status:'pass',affectedRideIndices:[]},
    {delayMinutes:5,status:'tight',firstFailure:point,affectedRideIndices:[1]},
    {delayMinutes:10,status:'missed',firstFailure:{...point,appliedDelayMinutes:10,delayedReadyAt:'2026-09-12T23:37:00+09:00'},affectedRideIndices:[1]},
  ]};
  const calls=[];
  globalThis.fetch=async(url,options={})=>{
    const body=options.body?JSON.parse(options.body):null;calls.push({url,body});
    const data=url==='/api/config'?{mode:'seoul',walkingConfigured:true,examples:[{origin,destination}]}:
      url==='/api/routes'?{source:'seoul',origin,destination,routeToken:'token',routes:[route,busRoute],stress:{routes:[stress],recommendedRouteId:'r1',evaluatedCount:1}}:
      url==='/api/deadline'?{status:'unknown',notice:'시간표 미확인'}:
      url==='/api/stress'?stress:
      url==='/api/recovery'?{status:'unknown',routes:[],notice:'대안 미확인'}:
      url==='/api/walk'?{durationSeconds:1800,distanceMeters:1500,departure:'2026-09-12T23:50:00+09:00',arrival:'2026-09-13T00:20:00+09:00',notice:'카카오 도보 예상',url:'https://map.kakao.com/link/by/walk/a/b',directions:[{text:'횡단보도 건너기',distanceMeters:1500}]}:{};
    return {ok:true,json:async()=>data};
  };
  try {
    await import('../frontend/app.js');await flush();
    assert.equal(get('submit-button').disabled,false);
    await get('example-buttons').children[0].emit('click');
    get('departure-date').value='2026-09-12';get('departure-time').value='22:40';
    await get('route-form').emit('submit');await flush();
    assert.equal(get('result-content').hidden,false);
    assert.equal(get('stress-lab').hidden,false);
    assert.match(get('stress-result').textContent,/여유 부족/);
    assert.equal(get('route-line').querySelectorAll('.route-node').length,5);
    assert.equal(get('trip-start').disabled,false);
    await get('trip-start').emit('click');
    assert.equal(get('trip-start').textContent,'안내 종료');
    assert.equal(get('traveler-label').textContent,'내 예상 위치');
    await get('stress-scenarios').children[2].emit('click');
    assert.match(get('stress-result').textContent,/열차 놓침/);
    assert.equal(get('route-line').querySelectorAll('.is-broken').length,1);
    await get('route-line').querySelectorAll('.route-node')[3].emit('click');await flush();
    assert.match(get('node-info').textContent,/사당/);
    assert.equal(get('walk-origin').textContent,'사당');
    assert.equal(calls.findLast(c=>c.url==='/api/stress').body.target,1);
    await get('recovery-button').emit('click');await flush();
    assert.match(get('recovery-result').textContent,/대안 미확인/);
    get('walk-departure').value='2026-09-12T23:50';
    await get('walk-button').emit('click');await flush();
    assert.match(get('walk-result').textContent,/1.5km/);
    assert.match(get('walk-result').textContent,/다음 날 00:20/);
    assert.equal(calls.findLast(c=>c.url==='/api/walk').body.pointKind,'boarding');
    const options=get('route-options').querySelectorAll('.route-option');
    assert.equal(options.length,2);
    await options[1].emit('click');await flush();
    assert.equal(get('trip-start').textContent,'출발');
    assert.equal(get('trip-elapsed').textContent,'출발 전');
    assert.equal(get('stress-lab').hidden,true);
    assert.equal(get('trip-start').disabled,false);
    assert.match(get('route-guide').textContent,/201/);
    assert.match(get('route-guide').textContent,/버스 승차·하차 정류장/);
    await get('access-minutes').emit('input');
    assert.equal(get('result-content').hidden,true);
    assert.equal(get('stress-lab').hidden,true);
    assert.equal(get('trip-start').disabled,true);
  } finally {globalThis.setInterval=interval;}
});
