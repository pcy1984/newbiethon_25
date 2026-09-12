"""Kakao Mobility driving estimates, not booking quotes or guaranteed taxi fares."""
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from seoul_transit import KST, NoRedirect, SeoulError, numeric

HOST = 'https://apis-navi.kakaomobility.com'
NOTICE = ('택시 호출 견적이 아닌 자동차 경로 기반 참고값입니다. 심야·시외 할증과 호출료 포함 여부는 '
          '확인되지 않았으며 실제 요금과 다를 수 있습니다. 배차 대기와 승차 지점까지 이동은 포함하지 않습니다.')


def request_context(departure, now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    departure = departure.astimezone(KST)
    if departure < now.replace(second=0, microsecond=0):
        raise SeoulError('지난 시각의 택시 요금은 조회할 수 없어요. 탑승 시각을 현재 이후로 바꿔 주세요.', 400)
    future = departure > now
    return departure if future else now, future


def fetch(origin, destination, departure, key, future):
    if not key:
        raise SeoulError('카카오 REST API 키를 설정한 뒤 서버를 다시 실행해 주세요.', 503)
    params = {'origin': f"{origin['x']},{origin['y']}", 'destination': f"{destination['x']},{destination['y']}",
              'priority': 'RECOMMEND', 'summary': 'true', 'alternatives': 'false'}
    path = '/v1/future/directions' if future else '/v1/directions'
    if future:
        params['departure_time'] = departure.astimezone(KST).strftime('%Y%m%d%H%M')
    request = urllib.request.Request(HOST + path + '?' + urllib.parse.urlencode(params),
                                     headers={'Authorization': 'KakaoAK ' + key, 'Accept': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=12) as response:
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise SeoulError('택시 경로 응답이 너무 큽니다. 다른 지점을 선택해 주세요.', 502)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('Expected object')
        return payload
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise SeoulError('카카오모빌리티 인증·이용 권한을 확인하지 못했어요. REST API 키와 길찾기 이용 설정을 확인해 주세요.', 503) from None
        if exc.code == 429:
            raise SeoulError('택시 경로 API 조회 한도에 도달했어요. 잠시 후 다시 시도해 주세요.', 503) from None
        raise SeoulError('택시 경로 제공처가 요청을 처리하지 못했어요. 지점·탑승 시각을 확인해 주세요.', 502) from None
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        raise SeoulError('택시 경로 응답이 늦어지고 있어요. 잠시 후 다시 시도해 주세요.', 503) from None
    except (ValueError, UnicodeError):
        raise SeoulError('택시 경로 응답을 읽지 못했어요. 다시 시도해 주세요.', 502) from None


def optional_fare(value, minimum=0):
    try:
        return numeric(value, minimum, 10_000_000)
    except (ValueError, TypeError):
        return None


def normalize(payload, origin, destination, departure, future):
    try:
        routes = payload['routes']
        if not isinstance(routes, list) or not routes:
            raise ValueError('Missing routes')
        route = routes[0]
        if type(route.get('result_code')) is not int:
            raise ValueError('Missing result code')
        if route['result_code'] != 0:
            raise SeoulError('해당 지점의 자동차 경로를 찾지 못했어요. 택시비를 임의로 계산하지 않았습니다.', 422)
        summary = route['summary']
        seconds = numeric(summary['duration'], 1, 172800)
        distance = numeric(summary['distance'], 1, 1_500_000)
        fare = summary.get('fare') or {}
        taxi = optional_fare(fare.get('taxi'), 1)
        toll = optional_fare(fare.get('toll'))
        return {'source': 'kakao-mobility', 'origin': origin, 'destination': destination,
                'fareWon': taxi, 'tollWon': toll, 'distanceMeters': distance, 'durationSeconds': seconds,
                'departure': departure.isoformat(), 'arrival': (departure + timedelta(seconds=seconds)).isoformat(),
                'timeBasis': 'future' if future else 'current', 'fareStatus': 'estimated' if taxi is not None else 'unknown',
                'surchargeStatus': 'unknown', 'notice': NOTICE}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SeoulError('택시 경로의 거리·소요시간을 확인하지 못했어요.', 502) from None
