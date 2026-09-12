"""Seoul Metro path2 adapter. Timetable data is not a live boarding guarantee."""

from __future__ import annotations

import json
import math
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

ENDPOINT = "https://apis.data.go.kr/B553766/path2/getShtrmPath2"
KST = timezone(timedelta(hours=9))
NOTICE = "서울교통공사 시간표 기준입니다. 지연·운휴는 반영하지 않으며, 역 밖 도보와 막차 귀가 가능 여부는 아직 검증하지 않습니다."
MISSING_NOTICE = "경로는 조회됐지만 연결 가능한 열차 시각을 확인하지 못했습니다. 표시 시간은 API의 총 소요시간을 더한 참고값이며 탑승 가능 여부를 뜻하지 않습니다."


class SeoulError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Never forward a credential-bearing query to a different destination.
        return None


def station_name(value):
    if isinstance(value, dict):
        value = value.get("name")
    if not isinstance(value, str):
        raise SeoulError("출발역과 도착역 이름을 입력해 주세요.", 400)
    name = " ".join(value.split())
    if not 1 <= len(name) <= 60 or not re.fullmatch(r"[가-힣A-Za-z0-9 ·()._-]+", name):
        raise SeoulError("올바른 지하철역 이름을 입력해 주세요. (예: 홍대입구, 강남)", 400)
    # The official example uses 서울역, while other station names omit 역.
    if name.endswith("역") and name != "서울역":
        name = name[:-1].strip()
    if not name:
        raise SeoulError("역 이름을 입력해 주세요.", 400)
    return name


def numeric(value, low=0, high=10_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("Invalid numeric field")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError("Out of range")
    return math.ceil(number)


def optional_number(value, high=10_000_000):
    try:
        return numeric(value, high=high)
    except (TypeError, ValueError):
        return None


def gateway_error(code):
    code = str(code or "").strip()
    if code in ("20", "30", "31", "32", "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "SERVICE_ACCESS_DENIED_ERROR", "DEADLINE_HAS_EXPIRED_ERROR"):
        raise SeoulError("공공데이터 인증에 실패했습니다. 키와 ‘서울교통공사_최단경로이동정보’ 활용신청 승인 상태를 확인해 주세요. 새 키는 반영 시간이 필요할 수 있습니다.")
    if code in ("22", "23", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"):
        raise SeoulError("공공데이터 API 호출 한도에 도달했습니다. 잠시 후 다시 조회해 주세요.", 503)
    raise SeoulError("공공데이터 API가 요청을 처리하지 못했습니다. 서비스 상태를 확인한 뒤 다시 조회해 주세요.", 503)


def get_route(origin, destination, departure, key, search_type="duration"):
    params = {"serviceKey": urllib.parse.unquote(key.strip()), "dataType": "JSON",
              "dptreStn": origin, "arvlStn": destination,
              "searchDt": departure.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S"),
              "searchType": search_type, "schInclYn": "Y", "stationValueType": "name"}
    request = urllib.request.Request(ENDPOINT + "?" + urllib.parse.urlencode(params),
                                     headers={"Accept": "application/json", "User-Agent": "HomeTime/0.2"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=12) as response:
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise SeoulError("경로 응답이 너무 큽니다. 다른 구간으로 조회해 주세요.")
        text = raw.decode("utf-8-sig").strip()
        if text.startswith("<"):
            root = ET.fromstring(text)
            code = root.findtext(".//returnAuthMsg") or root.findtext(".//returnReasonCode")
            if code:
                gateway_error(code)
            raise SeoulError("서울교통공사에서 JSON 경로 응답을 받지 못했습니다. 다시 조회해 주세요.")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("Expected object")
        return payload
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            gateway_error("30")
        if exc.code == 429:
            gateway_error("22")
        raise SeoulError("서울교통공사 API 연결에 실패했습니다. 잠시 후 다시 조회해 주세요.", 503) from None
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        raise SeoulError("서울교통공사 응답이 지연되고 있습니다. 인터넷 연결과 API 서비스 상태를 확인해 주세요.", 503) from None
    except (ValueError, UnicodeError, ET.ParseError):
        raise SeoulError("서울교통공사 응답을 해석하지 못했습니다. 실제 응답 형식 확인이 필요합니다.") from None


def read_body(payload):
    if not isinstance(payload, dict):
        raise SeoulError("경로 응답 형식이 올바르지 않습니다.")
    if "OpenAPI_ServiceResponse" in payload:
        gateway = payload["OpenAPI_ServiceResponse"]
        header = gateway.get("cmmMsgHeader", {}) if isinstance(gateway, dict) else {}
        gateway_error(header.get("returnAuthMsg") or header.get("returnReasonCode"))
    envelope = payload.get("response", payload)
    if not isinstance(envelope, dict) or not isinstance(envelope.get("header"), dict):
        raise SeoulError("경로 응답의 결과 코드를 확인하지 못했습니다.")
    code = str(envelope["header"].get("resultCode", "")).strip()
    if code not in ("0", "00", "200"):
        if code in ("10", "1", "01"):
            raise SeoulError("출발역 또는 도착역을 찾지 못했습니다. 역 이름과 API 지원 구간을 확인해 주세요. (예: 홍대입구, 강남, 서울역)", 422)
        if code in ("11", "2", "02"):
            raise SeoulError("API가 출발 일시를 처리하지 못했습니다. 날짜와 시각을 확인해 주세요.", 422)
        if code in ("30", "31", "32"):
            gateway_error(code)
        raise SeoulError("해당 조건의 경로를 조회하지 못했습니다. 다른 역 또는 시각으로 조회해 주세요.", 422)
    body = envelope.get("body")
    if not isinstance(body, dict) or not isinstance(body.get("paths"), list) or not body["paths"]:
        raise SeoulError("해당 시각의 경로를 찾지 못했습니다. 이것만으로 막차 종료 여부를 확정할 수는 없습니다.", 422)
    if len(body["paths"]) > 500:
        raise SeoulError("경로 구간 수가 너무 많습니다.")
    return body


def train_time(value, departure, not_before):
    """Normalize dated times, HH:mm:ss, HHmmss and service-day 24~29h times.

    Only a plausible evening-to-midnight rollover is inferred. A 22:00 train
    returned for a 23:00 request is never silently moved to tomorrow.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing train time")
    value = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}[ T]", value):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        result = result.replace(tzinfo=KST) if result.tzinfo is None else result.astimezone(KST)
    else:
        match = re.fullmatch(r"(\d{2}):(\d{2})(?::(\d{2}))?", value)
        if not match:
            match = re.fullmatch(r"(\d{2})(\d{2})(\d{2})", value)
        if not match:
            raise ValueError("Unsupported train time")
        hour, minute, second = (int(part or 0) for part in match.groups())
        if hour > 29 or minute > 59 or second > 59:
            raise ValueError("Invalid train time")
        base = departure.replace(hour=0, minute=0, second=0, microsecond=0)
        if hour >= 24 and departure.hour < 6:
            base -= timedelta(days=1)
        result = base + timedelta(hours=hour, minutes=minute, seconds=second)
        if hour < 24 and result < not_before:
            candidate = result + timedelta(days=1)
            if hour < 6 and (not_before.hour >= 18 or not_before.date() > departure.date()) and candidate >= not_before:
                result = candidate
    if result < not_before or result > departure + timedelta(hours=36):
        raise ValueError("Non-chronological train time")
    return result


def clean_text(value, limit=120):
    return str(value or "").strip()[:limit]


def normalize(payload, departure):
    body = read_body(payload)
    try:
        provided_seconds = numeric(body["totalReqHr"], 1, 129600)
        transfers = optional_number(body.get("trsitNmtm"), 100)
        segments = []
        cursor = departure
        schedule_ok = str(body.get("schInclYn", "")).upper() == "Y"
        for raw in body["paths"]:
            start, end = raw["dptreStn"], raw["arvlStn"]
            start_name, end_name = clean_text(start["stnNm"]), clean_text(end["stnNm"])
            if not start_name or not end_name:
                raise ValueError("Empty station")
            if segments and segments[-1]["endName"] != start_name:
                raise ValueError("Disconnected path")
            start_line, end_line = clean_text(start.get("lineNm")), clean_text(end.get("lineNm"))
            is_transfer = start_name == end_name and (start_line != end_line or str(raw.get("trsitYn")).upper() == "Y")
            segment = {"startName": start_name, "endName": end_name, "line": start_line,
                       "endLine": end_line, "type": "WALKING" if is_transfer else "SUBWAY",
                       "durationSeconds": numeric(raw["reqHr"], 0, 129600),
                       "distanceMeters": optional_number(raw.get("stnSctnDstc")),
                       "train": clean_text(raw.get("trainno")), "terminal": clean_text(raw.get("tmnlStnNm")),
                       "express": str(raw.get("etrnYn")).upper() == "Y"}
            try:
                if is_transfer:
                    # Transfer walking duration comes from the documented reqHr,
                    # not the ambiguously documented wtngHr field.
                    segment["departure"] = cursor
                    segment["arrival"] = cursor + timedelta(seconds=segment["durationSeconds"])
                else:
                    segment["departure"] = train_time(raw.get("trainDptreTm"), departure, cursor)
                    segment["arrival"] = train_time(raw.get("trainArvlTm"), departure, segment["departure"])
                cursor = segment["arrival"]
            except ValueError:
                schedule_ok = False
                segment["departure"] = segment["arrival"] = None
            segments.append(segment)
        steps = []
        if not any(s["type"] == "SUBWAY" for s in segments):
            raise ValueError("No train")
        wait_seconds = 0
        cursor = departure
        last_train = None
        for seg in segments:
            identity = (seg["line"], seg["train"], seg["terminal"])
            continuing = seg["type"] == "SUBWAY" and bool(seg["train"]) and last_train == identity
            gap = int((seg["departure"] - cursor).total_seconds()) if schedule_ok else 0
            if schedule_ok and gap > 0 and not continuing:
                steps.append({"type": "WAITING", "title": f'{seg["startName"]}에서 열차 대기',
                              "durationSeconds": gap, "distanceMeters": None, "vehicles": [],
                              "stops": [seg["startName"]], "points": [],
                              "departure": cursor.isoformat(), "arrival": seg["departure"].isoformat()})
                wait_seconds += gap
            duration = int((seg["arrival"] - seg["departure"]).total_seconds()) if schedule_ok else seg["durationSeconds"]
            title = f'{seg["line"]} → {seg["endLine"]} 환승 이동' if seg["type"] == "WALKING" else " · ".join(filter(None, [seg["line"] or "지하철", "급행" if seg["express"] else "", f'{seg["terminal"]} 방면' if seg["terminal"] else ""]))
            step = {"type": seg["type"], "title": title, "durationSeconds": duration,
                    "distanceMeters": seg["distanceMeters"], "vehicles": [seg["line"]],
                    "stops": [seg["startName"], seg["endName"]], "points": [],
                    "departure": seg["departure"].isoformat() if schedule_ok else None,
                    "arrival": seg["arrival"].isoformat() if schedule_ok else None}
            if continuing and steps and steps[-1]["type"] == "SUBWAY":
                previous = steps[-1]
                previous["durationSeconds"] += duration + gap
                previous["arrival"] = step["arrival"]
                previous["stops"].append(seg["endName"])
                previous["distanceMeters"] = previous["distanceMeters"] + step["distanceMeters"] if previous["distanceMeters"] is not None and step["distanceMeters"] is not None else None
            else:
                steps.append(step)
            if schedule_ok:
                cursor = seg["arrival"]
            last_train = identity if seg["type"] == "SUBWAY" else None
        arrival = cursor if schedule_ok else departure + timedelta(seconds=provided_seconds)
        seconds = int((arrival - departure).total_seconds())
        if seconds <= 0:
            raise ValueError("Empty journey")
        warnings = []
        if not schedule_ok:
            warnings.append(MISSING_NOTICE)
        elif wait_seconds >= 3600:
            warnings.append("1시간 이상 대기가 포함된 경로입니다. 다음 날 첫차로 이어지는지 열차 날짜·시각을 확인해 주세요.")
        if schedule_ok and abs(seconds - provided_seconds) > 60:
            warnings.append("API 총 소요시간과 열차 시각의 합계가 다릅니다. 예상 도착은 반환된 열차 시각, 총 소요시간은 입력 시각부터의 경과 시간으로 표시합니다.")
        return {"id": "seoul-0", "type": "SUBWAY", "label": "최소시간 경로",
                "durationSeconds": seconds, "durationMinutes": math.ceil(seconds / 60),
                "providerDurationSeconds": provided_seconds, "departure": departure.isoformat(),
                "arrival": arrival.isoformat(), "transfers": transfers,
                "walkSeconds": sum(s["durationSeconds"] for s in steps if s["type"] == "WALKING"),
                "waitSeconds": wait_seconds if schedule_ok else None,
                "distanceMeters": optional_number(body.get("totalDstc")),
                "fare": optional_number(body.get("totalCardCrg")), "steps": steps,
                "scheduleStatus": "complete" if schedule_ok else "unverified", "warnings": warnings,
                "firstStation": segments[0]["startName"], "lastStation": segments[-1]["endName"]}
    except (KeyError, ValueError, TypeError, AttributeError):
        raise SeoulError("서울교통공사 경로 정보가 불완전합니다. 예상 시간을 임의로 만들지 않았습니다. 다른 구간으로 조회해 주세요.") from None


def annotate(route, buffer_minutes):
    """Boarding deadlines, not proof that these are the day's last trains."""
    margin = timedelta(minutes=buffer_minutes)
    checkpoints = []
    ready = datetime.fromisoformat(route['departure'])
    for step in route['steps']:
        if step['type'] == 'WAITING':
            continue
        if step['type'] == 'SUBWAY':
            board = datetime.fromisoformat(step['departure']) if step.get('departure') else None
            deadline = board - margin if board else None
            checkpoints.append({'station': step['stops'][0], 'line': step['title'],
                                'boardAt': board.isoformat() if board else None,
                                'arriveBy': deadline.isoformat() if deadline else None,
                                'marginOkay': ready <= deadline if deadline else None,
                                'lastBusAt': None})
        if step.get('arrival'):
            ready = datetime.fromisoformat(step['arrival'])
    route['checkpoints'] = checkpoints
    route['bufferMinutes'] = buffer_minutes
    route['marginOkay'] = bool(checkpoints) and all(c['marginOkay'] is True for c in checkpoints)
    if route['scheduleStatus'] == 'complete' and not route['marginOkay']:
        route['warnings'].append(f'환승 중 {buffer_minutes}분 여유를 확보하지 못하는 지점이 있습니다. 해당 열차 연결을 여유 있는 경로로 안내하지 않습니다.')
    return route


def routes(origin, destination, departure, key, buffer_minutes=5):
    def fetch(kind):
        try:
            payload = get_route(origin, destination, departure + timedelta(minutes=buffer_minutes), key, kind)
            route = annotate(normalize(payload, departure), buffer_minutes)
            if station_name(route['firstStation']) != origin or station_name(route['lastStation']) != destination:
                raise SeoulError('요청한 출발역·도착역과 응답 경로가 다릅니다.')
            route['label'] = {'duration': '최소시간', 'transfer': '최소환승'}[kind]
            route['id'] = 'seoul-' + kind
            return route
        except SeoulError as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(fetch, ('duration', 'transfer')))
    good, seen = [], set()
    for result in results:
        if isinstance(result, SeoulError):
            continue
        signature = tuple((s['title'], tuple(s['stops']), s.get('departure')) for s in result['steps'])
        if signature not in seen:
            seen.add(signature)
            good.append(result)
    if not good:
        raise results[0]
    return sorted(good, key=lambda r: (not r['marginOkay'], r['durationSeconds']))


def corridor(route):
    result = []
    for step in route['steps']:
        if step['type'] != 'SUBWAY':
            continue
        name = (step.get('vehicles') or [step['title']])[0]
        a, b = station_name(step['stops'][0]), station_name(step['stops'][-1])
        if result and result[-1][0] == name and result[-1][2] == a:
            result[-1] = (name, result[-1][1], b)
        else:
            result.append((name, a, b))
    return result


def late_departure(origin, destination, departure, key, buffer_minutes=5, line=None, expected_legs=None):
    """Bounded sampling, deliberately NOT labelled an exhaustive last-train search."""
    base = departure.replace(hour=0, minute=0, second=0, microsecond=0)
    if departure.hour < 6:
        base -= timedelta(days=1)
    def check(minute):
        query = base + timedelta(minutes=minute)
        try:
            route = annotate(normalize(get_route(origin, destination, query, key), query), buffer_minutes)
            if route['scheduleStatus'] != 'complete':
                return None
            rides = [s for s in route['steps'] if s['type'] == 'SUBWAY']
            if not rides or (line and any(line not in s['vehicles'] for s in rides)):
                return None
            if expected_legs is not None and corridor(route) != expected_legs:
                return None
            first = datetime.fromisoformat(rides[0]['departure'])
            if first - query > timedelta(minutes=45) or first >= base + timedelta(hours=27):
                return None
            # First-station readiness is obtained by arriving buffer minutes early.
            # Every subsequent transfer must also meet the user's margin.
            if not all(c['marginOkay'] for c in route['checkpoints'][1:]):
                return None
            if station_name(route['firstStation']) != origin or station_name(route['lastStation']) != destination:
                return None
            return route
        except SeoulError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        found = [r for r in pool.map(check, range(22 * 60, 26 * 60 + 1, 15)) if r]
    note = '22:00~다음 날 02:00를 15분 간격으로 조회한 참고 결과입니다. 하루 전체의 막차나 모든 대체 경로를 보장하지 않습니다.'
    if not found:
        return {'status': 'unknown', 'latestDeparture': None, 'checkpoints': [], 'notice': note + ' 조건에 맞는 심야 연결을 확인하지 못했습니다.'}
    best = max(found, key=lambda r: r['checkpoints'][0]['arriveBy'])
    deadline = best['checkpoints'][0]['arriveBy']
    return {'status': 'sampled', 'latestDeparture': deadline, 'arrival': best['arrival'],
            'checkpoints': best['checkpoints'], 'route': best,
            'notice': note + ' 표시 출발 시각은 집·가게가 아닌 첫 승차역 도착 기준입니다.',
            'past': departure > datetime.fromisoformat(deadline)}
