"""Unified route snapshots. No disk storage, keys, or user inputs in logs."""
import copy
import hashlib
import secrets
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import bus_transit as bus
import seoul_transit as rail
import stress
import route_tokens

_snapshots = OrderedDict()
_lock = threading.Lock()
TTL = 300


def context(origin, destination, departure, margin, access):
    return (origin['id'], origin['name'], origin['x'], origin['y'], destination['id'], destination['name'],
            destination['x'], destination['y'], departure.isoformat(), margin, access)


def access_minutes(value):
    if type(value) is not int or not 0 <= value <= 120:
        raise rail.SeoulError('첫 승차 지점까지 이동시간은 0~120분으로 입력해 주세요.', 400)
    return value


def find(origin, destination, departure, margin, access, config):
    signing_key = route_tokens.secret()
    station_departure = departure + timedelta(minutes=access)
    try:
        routes, failures = bus.all_routes(origin, destination, station_departure, config.bus_path_key, margin)
    except rail.SeoulError as exc:
        if not config.seoul_key or not all(p['name'].endswith('역') for p in (origin, destination)):
            raise
        routes, failures = [], [str(exc)]
    def enrich(route):
        original = copy.deepcopy(route)
        candidates = [route]
        # Only replace a rail path when the actual timetable follows the same corridor.
        if route['type'] == 'SUBWAY' and config.seoul_key:
            try:
                expected = rail.corridor(route)
                actual = rail.routes(expected[0][1], expected[-1][2], station_departure, config.seoul_key, margin)
                candidates = actual
            except rail.SeoulError:
                route['warnings'].append('해당 시각의 지하철 열차 연결을 확인하지 못했습니다. 기본 경로만 표시합니다.')
        for candidate in candidates:
            if candidate is not route:
                identity = repr([(s['title'], s['stops'], s.get('departure')) for s in candidate['steps'] if s['type'] == 'SUBWAY'])
                candidate['id'] = 'rail-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
                # Keep only exact station-name endpoint coordinates, never invented station centroids.
                for step in candidate['steps']:
                    if step['type'] != 'SUBWAY':
                        continue
                    for prefix, name in [('from', step['stops'][0]), ('to', step['stops'][-1])]:
                        for old in original['steps']:
                            for old_prefix, old_name in [('from', old['stops'][0]), ('to', old['stops'][-1])]:
                                if rail.station_name(name) == rail.station_name(old_name):
                                    step[prefix + 'X'], step[prefix + 'Y'] = old[old_prefix + 'X'], old[old_prefix + 'Y']
            candidate['stationDeparture'] = station_departure.isoformat()
            candidate['departure'] = departure.isoformat()
            candidate['durationSeconds'] += access * 60
            candidate['durationMinutes'] = (candidate['durationSeconds'] + 59) // 60
            candidate['accessMinutes'] = access
            candidate['boardingStation'] = next(s['stops'][0] for s in candidate['steps'] if s['type'] in ('SUBWAY', 'BUS'))
            candidate['label'] = ' → '.join(dict.fromkeys((s.get('vehicles') or [s['title']])[0] for s in candidate['steps'] if s['type'] in ('BUS', 'SUBWAY')))
        return candidates
    with ThreadPoolExecutor(max_workers=2) as pool:
        enriched = [r for group in pool.map(enrich, routes) for r in group]
    # Seoul's coordinate gateway sometimes returns no subway path even when the
    # station-to-station timetable API has one. Query that provider directly for
    # explicit station inputs only, retaining its actual timed alternatives.
    if config.seoul_key and not any(r['type'] == 'SUBWAY' for r in enriched) and all(p['name'].endswith('역') for p in (origin, destination)):
        try:
            direct = rail.routes(rail.station_name(origin), rail.station_name(destination), station_departure, config.seoul_key, margin)
            for candidate in direct:
                identity = repr([(s['title'], s['stops'], s.get('departure')) for s in candidate['steps'] if s['type'] == 'SUBWAY'])
                candidate['id'] = 'rail-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
                rides = [s for s in candidate['steps'] if s['type'] == 'SUBWAY']
                rides[0].update(fromX=origin['x'], fromY=origin['y'])
                rides[-1].update(toX=destination['x'], toY=destination['y'])
                candidate.update(stationDeparture=station_departure.isoformat(), departure=departure.isoformat(), accessMinutes=access,
                                 boardingStation=rides[0]['stops'][0], label=' → '.join(dict.fromkeys(s['vehicles'][0] for s in rides)))
                candidate['durationSeconds'] += access * 60
                candidate['durationMinutes'] = (candidate['durationSeconds'] + 59) // 60
                enriched.append(candidate)
        except rail.SeoulError as exc:
            failures.append(str(exc))
    routes = sorted({r['id']: r for r in enriched}.values(), key=lambda r: r['durationSeconds'])
    if not routes:
        raise rail.SeoulError('해당 출발지·목적지의 경로를 확인하지 못했습니다. 지점과 시각을 바꿔 다시 조회해 주세요.', 422)
    token = secrets.token_hex(16)
    stamp = time.monotonic()
    entry = {'created': stamp, 'context': context(origin, destination, departure, margin, access),
             'routes': copy.deepcopy(routes), 'reports': {}, 'mutex': threading.Lock()}
    if signing_key:
        token = route_tokens.encode(entry['context'], entry['routes'], signing_key)
    with _lock:
        for old in [k for k, v in _snapshots.items() if stamp - v['created'] > TTL]:
            del _snapshots[old]
        _snapshots[token] = entry
        while len(_snapshots) > 64:
            _snapshots.popitem(last=False)
    return {'source': 'seoul', 'journeyMode': 'auto', 'origin': origin, 'destination': destination,
            'routeToken': token, 'routes': routes, 'timeAware': False, 'demoNotice': None,
            'stress': stress.compare([route for route in routes if route['type'] == 'SUBWAY']),
            'providerWarnings': list(dict.fromkeys(failures)),
            'notice': '선택한 출발지에서 첫 승차 지점까지의 직접 입력 시간과 승차·환승 여유시간을 반영합니다. 지연·정체·운휴는 반영하지 않는 예상 안내입니다.'}


def origin_deadline(report, departure, access):
    report = copy.deepcopy(report)
    first = report.get('latestDeparture')
    report['boardingDeadline'] = first
    report['originLatestDeparture'] = (datetime.fromisoformat(first) - timedelta(minutes=access)).isoformat() if first else None
    report['accessMinutes'] = access
    report['notice'] = report.get('notice', '').replace(
        '표시 출발 시각은 집·가게가 아닌 첫 승차역 도착 기준입니다.',
        '출발지 마감에는 직접 입력한 첫 승차역까지 이동시간을 반영했습니다.').replace(
        '첫 승차 지점 도착 기준의 예상 마감입니다.',
        '출발지에서 첫 승차 지점까지 직접 입력한 이동시간을 반영한 예상 마감입니다.')
    report['past'] = departure > datetime.fromisoformat(report['originLatestDeparture']) if first else None
    return report


def resolve(token, route_id, origin, destination, departure, margin, access):
    with _lock:
        entry = _snapshots.get(token) if isinstance(token, str) else None
    if not entry and isinstance(token, str) and token.startswith('v1.'):
        restored = route_tokens.decode(token, route_tokens.secret())
        entry = {'created': time.monotonic(), 'context': tuple(restored['context']), 'routes': restored['routes'],
                 'reports': {}, 'mutex': threading.Lock()}
    if not entry or time.monotonic() - entry['created'] > TTL:
        raise rail.SeoulError('조회 결과가 만료됐어요. 경로를 다시 검색해 주세요.', 409)
    if entry['context'] != context(origin, destination, departure, margin, access):
        raise rail.SeoulError('입력이 바뀌었어요. 경로를 다시 검색해 주세요.', 409)
    route = next((r for r in entry['routes'] if r['id'] == route_id), None)
    if route is None:
        raise rail.SeoulError('선택한 경로가 없습니다. 다시 검색해 주세요.', 409)
    return entry, route


def deadline(token, route_id, origin, destination, departure, margin, access, config):
    entry, route = resolve(token, route_id, origin, destination, departure, margin, access)
    # Share repeated requests for the same snapshot instead of multiplying API calls.
    with entry['mutex']:
        if route_id in entry['reports']:
            return copy.deepcopy(entry['reports'][route_id])
        station_departure = departure + timedelta(minutes=access)
        if route['type'] == 'SUBWAY':
            expected = rail.corridor(route)
            if not config.seoul_key:
                report = {'status': 'unknown', 'latestDeparture': None, 'checkpoints': [], 'notice': '지하철 인증키가 없습니다.'}
            else:
                report = rail.late_departure(expected[0][1], expected[-1][2], station_departure, config.seoul_key, margin, expected_legs=expected)
        else:
            report = bus.deadline(route, station_departure, config, margin)
        report = origin_deadline(report, departure, access)
        report['routeId'] = route_id
        report['boardingStation'] = route['boardingStation']
        report['bufferMinutes'] = margin
        entry['reports'][route_id] = copy.deepcopy(report)
        return report
