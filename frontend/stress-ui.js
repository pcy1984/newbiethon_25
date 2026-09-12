import {formatDuration, timeInKorea, localKoreaInput, arrivalDayLabel, distanceLabel} from './utils.js';
import {createTaxiUI} from './taxi-ui.js';

const $ = id => document.getElementById(id);
const element = (tag, text = '', cls = '') => { const e = document.createElement(tag); e.textContent = text; e.className = cls; return e; };
export const scenarioLabel = status => ({pass:'여유 유지', tight:'여유 부족', missed:'열차 놓침', unknown:'미확인'}[status] || '미확인');
const time = value => value ? timeInKorea(value) : '미확인';
const reserve = seconds => seconds == null ? '미확인' : seconds < 0 ? '기본 여유 부족' : Math.floor(seconds / 60) + '분 ' + seconds % 60 + '초';

export function createStressUI({post, chooseRoute, highlight}) {
  const taxiUI = createTaxiUI({post});
  let context = null, current = null, generation = 0, requestId = 0, target = 'weakest', delay = 5, point = null;
  let stressRequest, recoveryRequest, walkRequest, recoveryId = 0;
  const body = extra => ({...context.body, routeToken:context.response.routeToken, routeId:context.route.id, ...extra});
  const datedTime = value => {
    if (!value) return '미확인';
    const day = arrivalDayLabel(context.body.departure + ':00+09:00', value);
    return (day === '당일' ? '' : day + ' ') + time(value);
  };
  const abort = () => { stressRequest?.abort(); recoveryRequest?.abort(); walkRequest?.abort(); };
  function reset() {
    taxiUI.reset();
    abort(); generation++; requestId++; context = current = point = null;
    $('stress-lab').hidden = true; $('walking-panel').hidden = true;
  }
  function setRoute(value) {
    taxiUI.setRoute(value);
    abort(); generation++; requestId++; context = value; target = 'weakest'; delay = 5;
    current = value.response.stress?.routes.find(r => r.routeId === value.route.id) || null;
    $('stress-lab').hidden = value.route.type !== 'SUBWAY';
    if (value.route.type !== 'SUBWAY') {
      current = null;
      highlight([], 'unknown');
      selectPoint({kind:'origin', name:value.response.origin.name}, false);
      return;
    }
    $('recovery-button').disabled = false;
    $('recovery-result').replaceChildren();
    const select = $('stress-target'); select.replaceChildren(element('option','한 곳이 지체될 때 · 가장 취약한 지점'));
    select.firstElementChild.value = 'weakest';
    const rides = value.route.steps.filter(s => ['BUS','SUBWAY'].includes(s.type));
    for (const [i,s] of rides.entries()) { const opt=element('option',(i ? '환승 ' : '첫 승차 ') + s.stops[0]); opt.value=String(i); select.append(opt); }
    select.disabled = current?.status !== 'evaluated';
    const comparison = value.response.stress;
    const bestIndex = value.response.routes.findIndex(r => r.id === comparison?.recommendedRouteId);
    $('stress-recommendation').replaceChildren();
    if (bestIndex >= 0 && comparison.evaluatedCount >= 2) {
      const best = comparison.routes.find(r => r.routeId === comparison.recommendedRouteId);
      const text = element('p','시간표가 확인된 ' + comparison.evaluatedCount + '개 경로 중, 경로 ' + (bestIndex+1) + '의 추가 지체 여유가 가장 커요: ' + reserve(best.toleranceSeconds));
      const button = element('button','해당 경로 보기 ↗','secondary-button'); button.type='button'; button.addEventListener('click',()=>chooseRoute(bestIndex));
      $('stress-recommendation').append(text,button);
    } else $('stress-recommendation').append(element('p','지하철 경로 ' + (comparison?.routes?.length || 0) + '개 중 시간표 분석 ' + (comparison?.evaluatedCount || 0) + '개. 버스 경로에는 지연 결과를 표시하지 않아요.'));
    render();
    selectPoint({kind:'origin',name:value.response.origin.name}, false);
  }
  function chosen() { return current?.scenarios.find(s => s.delayMinutes === delay); }
  function render() {
    const cases = current?.scenarios || [0,5,10].map(n=>({delayMinutes:n,status:'unknown'}));
    $('stress-scenarios').replaceChildren();
    for (const entry of cases) {
      const button = element('button','','scenario-card ' + entry.status); button.type='button'; button.setAttribute('aria-pressed',String(entry.delayMinutes===delay));
      button.append(element('span',entry.delayMinutes ? '+'+entry.delayMinutes+'분 지체' : '지체 없음'),element('strong',scenarioLabel(entry.status)));
      button.addEventListener('click',()=>{ delay=entry.delayMinutes; recoveryId++; recoveryRequest?.abort(); $('recovery-button').disabled=false; $('recovery-result').replaceChildren(); render(); });
      $('stress-scenarios').append(button);
    }
    const item = chosen(), failure = item?.firstFailure;
    const result = $('stress-result'); result.replaceChildren(); result.className='stress-result ' + (item?.status || 'unknown');
    if (!current || current.status === 'unknown') {
      result.append(element('h3','이 경로는 시뮬레이션 미확인'),element('p',current?.notice || '검증된 열차 시간표가 없어 가상의 결과를 만들지 않았습니다.'));
    } else if (item.status === 'pass') {
      result.append(element('h3',delay ? delay+'분 지체해도 설정한 여유시간이 남아요' : '조회 시간표상 연결 여유가 있어요'),
        element('p','가정한 조건에서 같은 열차들을 탈 수 있는 계산입니다. 실제 운행·안전을 보장하지 않습니다.'));
    } else {
      result.append(element('h3',failure.station + ' · ' + scenarioLabel(item.status)),
        element('p',failure.line + ' / 지체 후 도착 ' + datedTime(failure.delayedReadyAt) + ' → 예정 열차 ' + datedTime(failure.boardAt)),
        element('p',item.status==='missed' ? '예정 열차 출발보다 늦게 도착하는 가정입니다. 이 열차를 놓치는 것이 전체 귀가 불가를 뜻하지는 않아요.' : '예정 열차 출발 전이지만 설정한 '+context.body.bufferMinutes+'분 여유를 확보하지 못해요.'));
    }
    $('stress-tolerance').textContent = '한 지점에서 추가로 지체할 수 있는 최소 여유: ' + reserve(current?.toleranceSeconds);
    $('stress-assumption').textContent = current?.notice || '지하철 시간표가 확인된 경로만 평가합니다. 버스 경로에는 지연 결과를 표시하지 않습니다.';
    $('recovery-button').hidden = !failure;
    highlight(item?.affectedRideIndices || [], item?.status || 'unknown');
  }
  async function setTarget(value) {
    if (!context) return;
    target = value; $('stress-target').value = String(value); requestId++; recoveryId++;
    const version=generation, request=requestId;
    stressRequest?.abort(); recoveryRequest?.abort(); stressRequest=new AbortController();
    $('recovery-result').replaceChildren();
    if (value === 'weakest') { current=context.response.stress?.routes.find(r=>r.routeId===context.route.id) || null; render(); return; }
    current=null; render(); $('recovery-button').disabled=false;
    $('stress-result').replaceChildren(element('p','선택한 지점의 지체 영향을 계산하고 있어요…'));
    highlight([], 'unknown');
    try {
      const data=await post('/api/stress',body({target:value}),stressRequest.signal);
      if (version!==generation || request!==requestId) return;
      current=data; render();
    } catch(error) {
      if (version!==generation || request!==requestId || error.name==='AbortError') return;
      current=null; render(); $('stress-result').replaceChildren(element('p',error.message));
    }
  }
  $('stress-target').addEventListener('change',()=>setTarget($('stress-target').value==='weakest' ? 'weakest' : Number($('stress-target').value)));
  async function recovery() {
    const failure=chosen()?.firstFailure; if (!failure || !context) return;
    const version=generation, request=requestId, recoveryVersion=++recoveryId;
    recoveryRequest?.abort(); recoveryRequest=new AbortController();
    $('recovery-button').disabled=true; $('recovery-result').replaceChildren(element('p','놓치는 지점에서 시간표 경로를 다시 조회하고 있어요…'));
    try {
      const data=await post('/api/recovery',body({rideIndex:failure.rideIndex,delayMinutes:failure.appliedDelayMinutes}),recoveryRequest.signal);
      if (version!==generation || request!==requestId || recoveryVersion!==recoveryId) return;
      $('recovery-result').replaceChildren(element('p',data.notice));
      for (const alternative of data.routes) {
        const title=alternative.steps.filter(s=>s.type==='SUBWAY').map(s=>s.title).join(' → ');
        $('recovery-result').append(element('strong',title),element('p','이 지점부터 '+formatDuration(alternative.durationSeconds)+' · 최종 하차 '+datedTime(alternative.arrival)));
      }
    } catch(error) { if (version===generation && request===requestId && recoveryVersion===recoveryId && error.name!=='AbortError') $('recovery-result').replaceChildren(element('p',error.message)); }
    finally { if(version===generation && recoveryVersion===recoveryId) $('recovery-button').disabled=false; }
  }
  $('recovery-button').addEventListener('click',recovery);
  function selectPoint(value, updateStress=true) {
    if(!context)return;
    point=value; walkRequest?.abort(); $('walk-result').replaceChildren(); $('walk-button').disabled=false;
    $('walking-panel').hidden=false; $('walk-origin').textContent=value.name;
    $('walk-help').textContent=context.config.walkingConfigured ? '선택한 지점에서 목적지까지 실제 도보 경로를 조회해요. 걷기 시작할 시각은 직접 바꿀 수 있어요.' : '카카오 REST 키를 넣고 서버를 다시 실행하면 도보 경로를 확인할 수 있어요.';
    const checkpoint=current?.checkpoints?.find(p=>p.rideIndex===value.rideIndex);
    const when=value.kind==='origin' ? context.body.departure : value.kind==='alighting' ? value.step?.arrival : checkpoint?.readyAt;
    $('walk-departure').value=when?.length===16 ? when : when ? localKoreaInput(new Date(when)) : context.body.departure;
    taxiUI.selectPoint(value, when || context.body.departure);
    if(updateStress && current?.status==='evaluated') {
      if(value.kind==='boarding') setTarget(value.rideIndex);
      else if(value.kind==='origin') setTarget(0);
      else if(!value.final) setTarget(value.rideIndex+1);
    }
  }
  async function walk() {
    if(!point || !context)return;
    const input=$('walk-departure');
    if(!input.value || !input.validity.valid) { $('walk-result').replaceChildren(element('p','걷기 시작할 날짜·시각을 입력해 주세요.')); return; }
    const version=generation, selected=point, departure=input.value;
    walkRequest?.abort(); walkRequest=new AbortController(); $('walk-button').disabled=true;
    $('walk-result').replaceChildren(element('p','카카오 도보 경로를 확인하고 있어요…'));
    try {
      const data=await post('/api/walk',body({pointKind:selected.kind,rideIndex:selected.rideIndex,walkDeparture:departure}),walkRequest.signal);
      if(version!==generation || selected!==point || departure!==input.value)return;
      const result=$('walk-result'); result.replaceChildren();
      result.append(element('strong',distanceLabel(data.distanceMeters)+' · '+formatDuration(data.durationSeconds)),
        element('p',arrivalDayLabel(data.departure,data.arrival)+' '+time(data.arrival)+' 도착 예상'),element('p',data.notice));
      const link=element('a','카카오맵에서 도보 경로 보기 ↗','secondary-button'); link.href=data.url; link.target='_blank'; link.rel='noreferrer'; result.append(link);
      const details=element('details'),list=element('ol'); details.append(element('summary','도보 구간 안내'));
      for(const direction of data.directions) list.append(element('li',direction.text+' · '+distanceLabel(direction.distanceMeters)));
      details.append(list); result.append(details);
    } catch(error) { if(version===generation && selected===point && error.name!=='AbortError') $('walk-result').replaceChildren(element('p',error.message)); }
    finally { if(version===generation && selected===point) $('walk-button').disabled=false; }
  }
  $('walk-button').addEventListener('click',walk);
  $('walk-departure').addEventListener('input',()=>{walkRequest?.abort(); $('walk-button').disabled=false; $('walk-result').replaceChildren();});
  return {reset,setRoute,selectPoint};
}
