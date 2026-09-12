"""Validated Kakao walking responses; coordinates come from trusted route snapshots."""
import math
from datetime import timedelta
from urllib.parse import quote
from seoul_transit import SeoulError, numeric


def estimate(origin, destination, departure):
    """Explicit distance/time model only. Never invent turn-by-turn directions."""
    try:
        coords = [float(p[k]) for p in (origin, destination) for k in ('x', 'y')]
        if not all(math.isfinite(v) for v in coords):
            raise ValueError()
        if not all(124 < p['x'] < 132 and 33 < p['y'] < 40 for p in (origin, destination)):
            raise ValueError()
        lat1, lat2 = math.radians(origin['y']), math.radians(destination['y'])
        dlat, dlon = lat2 - lat1, math.radians(destination['x'] - origin['x'])
        h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        direct = 6371000 * 2 * math.atan2(math.sqrt(h), math.sqrt(max(0, 1 - h)))
        distance = math.ceil(direct * 1.3)
        seconds = math.ceil(distance / (4000 / 3600))
        def location(p):
            return quote(p['name'], safe='') + ',' + str(p['y']) + ',' + str(p['x'])
        return {'source': 'estimated-walk', 'estimated': True, 'origin': origin, 'destination': destination,
                'distanceMeters': distance, 'straightDistanceMeters': round(direct), 'durationSeconds': seconds,
                'departure': departure.isoformat(), 'arrival': (departure + timedelta(seconds=seconds)).isoformat(),
                'directions': [], 'url': 'https://map.kakao.com/link/by/walk/' + location(origin) + '/' + location(destination),
                'notice': 'API 도보 경로를 확인하지 못해 추정한 값입니다. 양끝 좌표의 직선거리×1.3, 보행속도 4km/h를 가정합니다. 실제 도로·횡단보도·하천·출입구·통제를 반영하지 않으며 길안내나 통행 가능 여부가 아닙니다.'}
    except (KeyError, TypeError, ValueError):
        raise SeoulError('양끝 좌표가 없어 도보 거리·시간을 추정할 수 없습니다.', 422) from None


def normalize(payload, origin, destination, departure):
    if payload.get('status') != 'OK':
        raise SeoulError('해당 지점의 도보 경로를 찾지 못했습니다. 직선거리로 대체하지 않았습니다.', 422)
    try:
        route = payload['route']
        seconds = numeric(route['properties']['totalTime'], 1, 172800)
        distance = numeric(route['properties']['totalDistance'], 0, 200000)
        directions = []
        for leg in route['legs']:
            for step in leg['steps']:
                props = step['properties']
                directions.append({'text': str(props.get('guidance') or '도보 이동')[:500],
                                   'distanceMeters': numeric(props['distance'], 0, 200000),
                                   'durationSeconds': numeric(props['time'], 0, 172800)})
        if not directions:
            raise ValueError('Missing directions')
        def location(place):
            return quote(place['name'], safe='') + ',' + str(place['y']) + ',' + str(place['x'])
        return {'source': 'kakao-walk', 'origin': origin, 'destination': destination,
                'durationSeconds': seconds, 'distanceMeters': distance, 'departure': departure.isoformat(),
                'arrival': (departure + timedelta(seconds=seconds)).isoformat(), 'directions': directions[:100],
                'url': 'https://map.kakao.com/link/by/walk/' + location(origin) + '/' + location(destination),
                'notice': '카카오 도보 경로의 예상값입니다. 입력한 걷기 시작 시각 기준이며 실제 보행속도·통제·야간 보행 안전은 보장하지 않습니다.'}
    except (KeyError, TypeError, ValueError):
        raise SeoulError('도보 경로 응답의 거리·시간을 확인하지 못했습니다.', 502) from None


def point(route, origin, kind, index):
    if kind == 'origin':
        return dict(origin)
    rides = [s for s in route['steps'] if s['type'] in ('BUS', 'SUBWAY')]
    if kind not in ('boarding', 'alighting') or type(index) is not int or not 0 <= index < len(rides):
        raise SeoulError('도보 출발 지점을 다시 선택해 주세요.', 400)
    step = rides[index]
    prefix = 'from' if kind == 'boarding' else 'to'
    try:
        x, y = float(step[prefix + 'X']), float(step[prefix + 'Y'])
        if not (math.isfinite(x) and math.isfinite(y) and 124 < x < 132 and 33 < y < 40):
            raise ValueError()
        return {'name': step['stops'][0 if kind == 'boarding' else -1], 'x': x, 'y': y}
    except (KeyError, TypeError, ValueError):
        raise SeoulError('선택한 역·정류장의 정확한 좌표가 없어 도보 경로를 조회하지 않았습니다.', 422) from None
