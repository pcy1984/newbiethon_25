"""Deterministic passenger-delay scenarios, never a probability or live prediction."""
from datetime import datetime, timedelta
import seoul_transit as rail

NOTICE = ('입력한 출발 시각의 조회 열차를 고정하고, 출발 또는 환승 지점 한 곳에서만 사람이 지체되는 상황을 가정합니다. '
          '열차 자체의 지연·운휴 확률을 예측하지 않습니다. 설정한 여유시간은 환승 이동시간과 별도로 확보합니다.')
SEVERITY = {'pass': 0, 'tight': 1, 'missed': 2}


def moment(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Missing timezone')
    return parsed


def checkpoints(route):
    points = []
    try:
        ready = moment(route.get('stationDeparture') or route['departure'])
    except (KeyError, ValueError, TypeError):
        ready = None
    margin = route.get('bufferMinutes', 5) * 60
    ride_index = 0
    for step in route['steps']:
        if step['type'] == 'WAITING':
            continue  # Scheduled waiting absorbs a passenger delay; it is not mandatory travel.
        if step['type'] == 'WALKING':
            seconds = step.get('durationSeconds')
            ready = ready + timedelta(seconds=seconds) if ready is not None and seconds is not None else None
            continue
        if step['type'] not in ('BUS', 'SUBWAY'):
            ready = None
            continue
        board = arrival = None
        try:
            board, arrival = moment(step['departure']), moment(step['arrival'])
            if arrival < board:
                board = arrival = None
        except (KeyError, ValueError, TypeError):
            pass
        gap = int((board - ready).total_seconds()) if board is not None and ready is not None else None
        points.append({'rideIndex': ride_index, 'station': step['stops'][0], 'line': step['title'],
                       'kind': 'first' if ride_index == 0 else 'transfer',
                       'readyAt': ready.isoformat() if ready is not None else None,
                       'boardAt': board.isoformat() if board is not None else None,
                       'arrivalAt': arrival.isoformat() if arrival is not None else None,
                       'slackSeconds': gap - margin if gap is not None else None,
                       'boardingGapSeconds': gap, 'marginSeconds': margin})
        ride_index += 1
        ready = arrival
    return points


def scenario(points, delay_minutes, target):
    if type(delay_minutes) is not int or delay_minutes not in (0, 5, 10):
        raise rail.SeoulError('지체 시간은 0분, 5분, 10분 중 선택해 주세요.', 400)
    if target != 'weakest' and (type(target) is not int or not 0 <= target < len(points)):
        raise rail.SeoulError('출발·환승 지점을 다시 선택해 주세요.', 400)
    known = bool(points) and all(p['slackSeconds'] is not None for p in points)
    candidates = points if target == 'weakest' else [points[target]]
    if not known:
        return {'delayMinutes': delay_minutes, 'status': 'unknown', 'firstFailure': None, 'affectedRideIndices': [], 'remainingSeconds': None}
    failures = []
    reserves = []
    # Each candidate is an independent one-location scenario, not cumulative delays.
    for selected in candidates:
        for p in points:
            extra = delay_minutes * 60 if p['rideIndex'] == selected['rideIndex'] else 0
            left = p['slackSeconds'] - extra
            reserves.append(left)
            if left < 0:
                status = 'missed' if p['boardingGapSeconds'] - extra < 0 else 'tight'
                failures.append(dict(p, status=status, remainingSeconds=left,
                                     delayedReadyAt=(moment(p['readyAt']) + timedelta(seconds=extra)).isoformat(),
                                     appliedDelayMinutes=extra // 60))
    if not failures:
        return {'delayMinutes': delay_minutes, 'status': 'pass', 'firstFailure': None, 'affectedRideIndices': [], 'remainingSeconds': min(reserves)}
    failures.sort(key=lambda p: (-SEVERITY[p['status']], p['rideIndex']))
    worst = failures[0]
    return {'delayMinutes': delay_minutes, 'status': worst['status'], 'firstFailure': worst,
            'affectedRideIndices': sorted({p['rideIndex'] for p in failures}), 'remainingSeconds': min(reserves)}


def evaluate(route, target='weakest'):
    points = checkpoints(route)
    known = bool(points) and all(p['slackSeconds'] is not None for p in points) and route.get('scheduleStatus') == 'complete'
    if not known:
        for p in points:
            p['slackSeconds'] = None  # Partial schedules cannot earn a route-level recommendation.
    cases = [scenario(points, delay, target) for delay in (0, 5, 10)]
    tolerance = min((p['slackSeconds'] for p in points), default=None) if known else None
    return {'routeId': route['id'], 'label': route['label'], 'durationSeconds': route['durationSeconds'],
            'status': 'evaluated' if known else 'unknown', 'target': target, 'checkpoints': points,
            'toleranceSeconds': tolerance, 'scenarios': cases,
            'notice': NOTICE if known else '열차 출발·도착 시간표가 부족해 지체 영향을 계산하지 않았습니다. 추정 소요시간만으로 환승 성공을 판단하지 않습니다.'}


def compare(routes):
    results = [evaluate(route) for route in routes]
    eligible = [r for r in results if r['status'] == 'evaluated' and r['scenarios'][0]['status'] == 'pass']
    best = max(eligible, key=lambda r: (r['toleranceSeconds'], -r['durationSeconds'])) if eligible else None
    return {'routes': results, 'recommendedRouteId': best['routeId'] if best else None,
            'evaluatedCount': sum(r['status'] == 'evaluated' for r in results), 'notice': NOTICE}


def recover(route, ride_index, delay_minutes, key):
    report = evaluate(route, ride_index)
    chosen = next(s for s in report['scenarios'] if s['delayMinutes'] == delay_minutes)
    point = chosen.get('firstFailure')
    if chosen['status'] not in ('tight', 'missed') or point is None:
        return {'status': 'unknown', 'routes': [], 'notice': '시간표가 확인되고 여유 부족 또는 연결 실패가 표시된 지점에서 조회해 주세요.'}
    if not key or route['type'] != 'SUBWAY':
        return {'status': 'unknown', 'routes': [], 'notice': '현재는 시간표가 확인된 지하철 경로의 대안만 조회합니다.'}
    when = moment(point['delayedReadyAt'])
    destination = rail.corridor(route)[-1][2]
    try:
        alternatives = rail.routes(rail.station_name(point['station']), destination, when, key, route['bufferMinutes'])
        # Do not sell the next morning's first train as a late-night recovery.
        base = moment(route['departure']).replace(hour=0, minute=0, second=0, microsecond=0)
        if moment(route['departure']).hour < 6:
            base -= timedelta(days=1)
        end = base + timedelta(hours=30)
        alternatives = [r for r in alternatives if r['scheduleStatus'] == 'complete' and r['marginOkay']
                        and moment(r['arrival']) < end and r['durationSeconds'] <= 4 * 3600]
        return {'status': 'found' if alternatives else 'unknown', 'routes': alternatives[:2],
                'fromStation': point['station'], 'departure': when.isoformat(),
                'notice': '지체 후 도착한 시각부터 최종 하차역까지 다시 조회한 시간표 경로입니다. 운행 보장이나 모든 대안의 검색 결과는 아닙니다.' if alternatives else '조건에 맞는 대안을 확인하지 못했습니다. 귀가 불가능이 확정된 것은 아닙니다.'}
    except rail.SeoulError as exc:
        return {'status': 'unknown', 'routes': [], 'notice': str(exc) + ' 대안 미확인은 귀가 불가 확정이 아닙니다.'}
