"""Seoul bus gateway. Credentials stay server-side; never follow redirects."""
import json
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import math
import re
import hashlib
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

from seoul_transit import NoRedirect, SeoulError, KST, numeric, optional_number
import seoul_transit as rail

# Official documented endpoint. HTTPS did not respond during verification.
# This provider uses plaintext HTTP; do not silently redirect credentials.
BASE = "http://ws.bus.go.kr/api/rest/"


def get(service, operation, params, key, timeout=12):
    if not key:
        raise SeoulError("서울 버스 API 인증키가 설정되지 않았습니다.", 503)
    params = dict(params, serviceKey=urllib.parse.unquote(key.strip()), resultType="json")
    request = urllib.request.Request(BASE + service + "/" + operation + "?" + urllib.parse.urlencode(params),
                                     headers={"Accept": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise SeoulError("서울 버스 API 응답이 너무 큽니다.")
        text = raw.decode("utf-8-sig").strip()
        if text.startswith("<"):
            root = ET.fromstring(text)
            code = root.findtext(".//returnReasonCode") or root.findtext(".//headerCd")
            if code in ("20", "30", "31", "7"):
                raise SeoulError("서울 버스 API 인증에 실패했습니다. 해당 서비스 활용신청 승인 상태와 인증키를 확인해 주세요.")
            raise SeoulError("서울 버스 API에서 JSON 응답을 받지 못했습니다.")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError()
        return payload
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise SeoulError("서울 버스 API 이용 권한을 확인해 주세요. 활용신청과 키 반영 시간이 필요할 수 있습니다.") from None
        raise SeoulError("서울 버스 API 연결에 실패했습니다. 잠시 후 다시 조회해 주세요.", 503) from None
    except (urllib.error.URLError, TimeoutError):
        raise SeoulError("서울 버스 API 응답이 지연되고 있습니다. 잠시 후 다시 조회해 주세요.", 503) from None
    except (ValueError, UnicodeError, ET.ParseError):
        raise SeoulError("서울 버스 API 응답을 읽지 못했습니다.") from None


def items(payload):
    header = payload.get("msgHeader", {})
    code = str(header.get("headerCd", ""))
    if code == "4":
        return []
    if code != "0":
        if code in ("7", "8", "20", "30", "31"):
            raise SeoulError("서울 버스 API 인증 또는 이용 권한을 확인해 주세요.")
        raise SeoulError("서울 버스 API가 해당 조건의 정보를 반환하지 않았습니다.", 422)
    rows = payload.get("msgBody", {}).get("itemList") or []
    return rows if isinstance(rows, list) else [rows]


def search(query, key):
    found = items(get('pathinfo', 'getLocationInfo', {'stSrch': query}, key, timeout=4))
    result = []
    for item in found[:40]:
        try:
            x, y = float(item['gpsX']), float(item['gpsY'])
            if not (math.isfinite(x) and math.isfinite(y) and 124 < x < 132 and 33 < y < 40):
                continue
            name = str(item['poiNm'])[:120]
            result.append({'id': str(item['poiId']), 'name': name, 'x': x, 'y': y,
                           'address': str(item.get('addr') or '서울시 출발·도착 지점')[:250]})
        except (KeyError, TypeError, ValueError):
            continue
    return result[:15]


def normalize(rows, departure, buffer_minutes):
    result = []
    for raw in rows[:30]:
        try:
            segments = raw['pathList']
            if not isinstance(segments, list) or not 1 <= len(segments) <= 6:
                continue
            basic = numeric(raw['time'], 1, 1440) * 60
            steps = []
            for leg in segments:
                kind = 'BUS' if leg.get('routeId') else 'SUBWAY' if leg.get('railLinkList') else None
                if not kind or not leg.get('fname') or not leg.get('tname'):
                    raise ValueError('Incomplete leg')
                steps.append({'type': kind, 'title': str(leg['routeNm'])[:120],
                              'durationSeconds': None, 'distanceMeters': None,
                              'stops': [str(leg['fname'])[:120], str(leg['tname'])[:120]],
                              'vehicles': [str(leg['routeNm'])[:120]], 'points': [],
                              'routeId': str(leg.get('routeId') or ''),
                              'fromId': str(leg['fid']), 'toId': str(leg['tid']),
                              'fromX': float(leg['fx']), 'fromY': float(leg['fy']),
                              'toX': float(leg['tx']), 'toY': float(leg['ty'])})
                if any(not math.isfinite(steps[-1][k]) for k in ('fromX', 'fromY', 'toX', 'toY')):
                    raise ValueError('Invalid coordinates')
            seconds = basic + len(steps) * buffer_minutes * 60
            identity = '|'.join(s['routeId'] + ':' + s['fromId'] + ':' + s['toId'] + ':' + s['title'] for s in steps)
            result.append({'id': hashlib.sha256(identity.encode()).hexdigest()[:20], 'type': 'BUS' if all(s['type'] == 'BUS' for s in steps) else 'SUBWAY' if all(s['type'] == 'SUBWAY' for s in steps) else 'TRANSIT',
                           'label': '버스·지하철 경로', 'durationSeconds': seconds, 'durationMinutes': math.ceil(seconds / 60),
                           'providerDurationSeconds': basic, 'departure': departure.isoformat(),
                           'arrival': (departure + timedelta(seconds=seconds)).isoformat(),
                           'transfers': len(steps) - 1, 'walkSeconds': None, 'waitSeconds': None,
                           'bufferMinutes': buffer_minutes, 'allowanceSeconds': len(steps) * buffer_minutes * 60,
                           'distanceMeters': optional_number(raw.get('distance')), 'fare': None,
                           'steps': steps, 'scheduleStatus': 'estimated', 'checkpoints': [],
                           'warnings': ['API 기본 소요시간에 승차마다 선택한 여유시간을 추가한 예상값입니다. 실제 배차 대기·환승 이동시간과 다를 수 있습니다. 구간별 시간은 막차 계산 시 추가 확인합니다.']})
        except (KeyError, ValueError, TypeError):
            continue
    if not result:
        raise SeoulError('해당 구간의 버스·지하철 환승 경로를 찾지 못했습니다.', 422)
    return sorted(result, key=lambda r: r['durationSeconds'])[:8]


def routes(origin, destination, departure, key, buffer_minutes):
    payload = get('pathinfo', 'getPathInfoByBusNSub',
                  {'startX': origin['x'], 'startY': origin['y'], 'endX': destination['x'], 'endY': destination['y']}, key)
    return normalize(items(payload), departure, buffer_minutes)


def all_routes(origin, destination, departure, key, buffer_minutes):
    params = {'startX': origin['x'], 'startY': origin['y'], 'endX': destination['x'], 'endY': destination['y']}
    def fetch(operation):
        try:
            return normalize(items(get('pathinfo', operation, params, key)), departure, buffer_minutes)
        except SeoulError as exc:
            return exc
    operations = ('getPathInfoBySubway', 'getPathInfoByBus', 'getPathInfoByBusNSub')
    with ThreadPoolExecutor(max_workers=3) as pool:
        groups = list(pool.map(fetch, operations))
    unique = {}
    failures = []
    for group in groups:
        if isinstance(group, SeoulError):
            failures.append(str(group))
            continue
        for route in group:
            previous = unique.get(route['id'])
            if previous is None or route['durationSeconds'] < previous['durationSeconds']:
                unique[route['id']] = route
    if not unique:
        raise groups[0]
    # Preserve at least one returned route from each transport composition.
    ordered = sorted(unique.values(), key=lambda r: r['durationSeconds'])
    chosen = ordered[:8]
    for kind in ('SUBWAY', 'BUS', 'TRANSIT'):
        candidate = next((r for r in ordered if r['type'] == kind), None)
        if candidate and not any(r['type'] == kind for r in chosen):
            chosen.append(candidate)
    return sorted(chosen, key=lambda r: r['durationSeconds']), failures


def last_time(value, today):
    # API documents these as today's times, not an arbitrary-date timetable.
    if not isinstance(value, str) or not re.fullmatch(r'\d{6}', value):
        raise ValueError('Missing last bus time')
    hour, minute, second = int(value[:2]), int(value[2:4]), int(value[4:])
    if hour > 29 or minute > 59 or second > 59 or value == '000000':
        raise ValueError('Invalid last bus time')
    if hour < 6:
        hour += 24
    return today.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=hour, minutes=minute, seconds=second)


def bus_deadline(step, departure, config):
    if departure.date() != datetime.now(KST).date() or departure.hour < 6:
        raise SeoulError('버스 막차는 오늘 낮~심야 출발만 확인합니다. 미래 날짜나 자정 이후 서비스일은 확인이 필요합니다.', 422)
    stops = items(get('stationinfo', 'getStationByName', {'stSrch': step['stops'][0]}, config.bus_station_key))
    match = next((s for s in stops if str(s.get('stId')) == step['fromId']), None)
    if not match or not match.get('arsId') or str(match['arsId']) == '0':
        raise SeoulError('승차 정류장의 고유번호를 확인하지 못했습니다.', 422)
    schedules = items(get('stationinfo', 'getBustimeByStation',
                          {'arsId': match['arsId'], 'busRouteId': step['routeId']}, config.bus_station_key))
    schedule = next((s for s in schedules if str(s.get('busRouteId')) == step['routeId'] and str(s.get('arsId')) == str(match['arsId'])), None)
    if not schedule:
        raise SeoulError('이 정류장·노선의 막차 예정시간이 없습니다.', 422)
    try:
        last = last_time(schedule.get('lastBusTm'), datetime.now(KST))
    except ValueError:
        raise SeoulError('막차 예정시간의 날짜·형식을 확인하지 못했습니다.', 422) from None
    return last


def segment_duration(step, key):
    data = items(get('pathinfo', 'getPathInfoByBus',
                     {'startX': step['fromX'], 'startY': step['fromY'], 'endX': step['toX'], 'endY': step['toY']}, key))
    for path in data:
        legs = path.get('pathList') or []
        if len(legs) == 1 and all(str(legs[0].get(k) or '') == step[v] for k, v in [('routeId', 'routeId'), ('fid', 'fromId'), ('tid', 'toId')]):
            return numeric(path['time'], 1, 1440) * 60
    return None


def backward_deadline(checkpoints, buffer_minutes):
    """Conservative estimated model; missing data must not manufacture deadlines."""
    margin = timedelta(minutes=buffer_minutes)
    downstream = None
    for checkpoint in reversed(checkpoints):
        if not checkpoint.get('boardAt'):
            return None
        ready = datetime.fromisoformat(checkpoint['boardAt']) - margin
        if downstream is not None:
            if checkpoint.get('durationSeconds') is None:
                return None
            ready = min(ready, downstream - timedelta(seconds=checkpoint['durationSeconds']) - margin)
        checkpoint['arriveBy'] = ready.isoformat()
        downstream = ready
    return downstream.isoformat() if downstream else None


def deadline(route, departure, config, buffer_minutes):
    def check(pair):
        index, step = pair
        point = {'station': step['stops'][0], 'line': step['title'], 'boardAt': None,
                 'lastBusAt': None, 'arriveBy': None, 'durationSeconds': None,
                 'source': 'bus' if step['type'] == 'BUS' else 'rail-sampled', 'notice': ''}
        try:
            if step['type'] == 'BUS':
                last = bus_deadline(step, departure, config)
                point['boardAt'] = point['lastBusAt'] = last.isoformat()
                point['arriveBy'] = (last - timedelta(minutes=buffer_minutes)).isoformat()
                if index < len(route['steps']) - 1:
                    point['durationSeconds'] = segment_duration(step, config.bus_path_key)
            else:
                if not config.seoul_key:
                    raise SeoulError('지하철 인증키가 없습니다.')
                estimate = rail.late_departure(rail.station_name(step['stops'][0]), rail.station_name(step['stops'][-1]), departure, config.seoul_key, buffer_minutes, line=step['title'])
                if not estimate['latestDeparture']:
                    raise SeoulError('해당 지하철 노선의 심야 연결을 확인하지 못했습니다.', 422)
                point['boardAt'] = estimate['checkpoints'][0]['boardAt']
                point['arriveBy'] = estimate['latestDeparture']
                point['durationSeconds'] = int((datetime.fromisoformat(estimate['arrival']) - datetime.fromisoformat(point['boardAt'])).total_seconds())
                point['notice'] = '22:00~02:00 표본 조회 결과이며 확정 막차가 아닙니다.'
        except (SeoulError, ValueError, TypeError) as exc:
            point['notice'] = str(exc) if isinstance(exc, SeoulError) else '시간 형식을 확인하지 못했습니다.'
        return point
    with ThreadPoolExecutor(max_workers=2) as pool:
        points = list(pool.map(check, enumerate(route['steps'])))
    latest = backward_deadline(points, buffer_minutes)
    return {'status': 'estimated' if latest else 'unknown', 'latestDeparture': latest,
            'checkpoints': points, 'past': departure > datetime.fromisoformat(latest) if latest else None,
            'notice': '첫 승차 지점 도착 기준의 예상 마감입니다. 버스 막차 예정시간·구간별 기본 시간과 승차/환승마다 선택한 여유시간으로 역산합니다. 정류장 사이 실제 보행 경로, 배차·정체·운휴는 검증하지 않습니다.' if latest else '일부 구간의 시간·막차 정보가 부족해 전체 출발 마감을 계산하지 않았습니다. 확인된 정류장별 시각만 참고하세요.'}
