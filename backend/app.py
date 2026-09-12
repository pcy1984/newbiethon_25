"""Local-only transit MVP. Python 3.10+, no third-party dependencies.

Kakao's publictraffic API has no departure-time parameter. Never present its
duration as a timetable-validated journey. Demo fixtures are not real schedules.
"""

from __future__ import annotations

import json
import math
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import seoul_transit
import bus_transit
import journeys
import stress
import walking

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
NOTICE = "출발 시각에 기본 소요시간을 더한 예상값입니다. 시간대별 배차·대기시간, 막차 및 운행 여부는 반영하지 않습니다."
DEMO_NOTICE = "가상 예제 데이터입니다. 실제 경로·요금·운행시간으로 사용하지 마세요."


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Config:
    rest_key: str = ""
    javascript_key: str = ""
    port: int = 8765
    seoul_key: str = ""
    bus_path_key: str = ""
    bus_station_key: str = ""

    @property
    def mode(self):
        if self.seoul_key or self.bus_path_key:
            return "seoul"
        return "kakao" if self.rest_key else "demo"

    @classmethod
    def load(cls, env_file=None):
        values = {}
        env_file = Path(env_file) if env_file else ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip("\"'")
        values.update(os.environ)
        return cls(values.get("KAKAO_REST_API_KEY", "").strip(),
                   values.get("KAKAO_JAVASCRIPT_KEY", "").strip(),
                   int(values.get("PORT", "8765")),
                   values.get("SEOUL_TRANSIT_API_KEY", "").strip(),
                   values.get("SEOUL_BUS_PATH_API_KEY", "").strip(),
                   values.get("SEOUL_BUS_STATION_API_KEY", "").strip())


DEMO_PLACES = [
    {"id": "demo-hongdae", "name": "홍대입구역", "address": "서울 마포구 · 예제 장소", "x": 126.9244, "y": 37.5572},
    {"id": "demo-gangnam", "name": "강남역", "address": "서울 강남구 · 예제 장소", "x": 127.0276, "y": 37.4979},
    {"id": "demo-konkuk", "name": "건대입구역", "address": "서울 광진구 · 예제 장소", "x": 127.0695, "y": 37.5404},
    {"id": "demo-seoul", "name": "서울역", "address": "서울 용산구 · 예제 장소", "x": 126.9726, "y": 37.5547},
]


def kakao_get(path, params, config, secure=False):
    url = "https://dapi.kakao.com" + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Authorization": "KakaoAK " + config.rest_key})
    try:
        opener = urllib.request.build_opener(seoul_transit.NoRedirect()).open if secure else urllib.request.urlopen
        with opener(request, timeout=10) as response:
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise AppError("경로 응답이 너무 큽니다. 다른 장소로 다시 검색해 주세요.", 502)
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Invalid response")
            return data
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AppError("카카오 인증에 실패했습니다. .env의 REST API 키와 카카오맵 사용 설정을 확인해 주세요.", 502) from None
        if exc.code == 429:
            raise AppError("카카오 API 호출 한도에 도달했습니다. 잠시 후 다시 시도하거나 쿼터를 확인해 주세요.", 503) from None
        raise AppError("카카오에서 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.", 502) from None
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        raise AppError("카카오 연결이 지연되고 있습니다. 인터넷 연결을 확인한 후 다시 시도해 주세요.", 503) from None
    except (ValueError, UnicodeError):
        raise AppError("카카오 응답을 읽지 못했습니다. 잠시 후 다시 시도해 주세요.", 502) from None


def finite_number(value, low=0, high=10_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("Invalid number")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError("Out of range")
    return number


def validate_place(value):
    try:
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            raise ValueError()
        name = value["name"].strip()
        if not 1 <= len(name) <= 120:
            raise ValueError()
        return {"id": str(value.get("id", ""))[:120], "name": name,
                "x": finite_number(value.get("x"), -180, 180),
                "y": finite_number(value.get("y"), -90, 90)}
    except (ValueError, TypeError):
        raise AppError("검색 결과에서 출발지와 목적지를 각각 선택해 주세요.") from None


def parse_departure(value):
    try:
        if not isinstance(value, str) or len(value) != 16:
            raise ValueError()
        result = datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=KST)
        if not 2000 <= result.year <= 2100 or result.strftime("%Y-%m-%dT%H:%M") != value:
            raise ValueError()
        return result
    except ValueError:
        raise AppError("올바른 출발 날짜와 시각을 입력해 주세요. (한국 시간 기준)") from None


def search_places(query, kind, config):
    query = query.strip()
    if not 1 <= len(query) <= 80 or kind not in ("keyword", "address"):
        raise AppError("검색어는 1~80자로 입력해 주세요.")
    if config.mode == "demo":
        return [p for p in DEMO_PLACES if query.replace(" ", "") in p["name"]]
    if config.mode == "seoul" and not config.rest_key:
        try:
            return bus_transit.search(query, config.bus_path_key)
        except seoul_transit.SeoulError as exc:
            raise AppError(str(exc), exc.status) from None
    def kakao_places():
        payload = kakao_get(f"/v2/local/search/{kind}.json", {"query": query, "size": 8}, config)
        places = []
        documents = payload.get("documents")
        if not isinstance(documents, list):
            raise AppError("장소 검색 응답 형식이 올바르지 않습니다. 다시 시도해 주세요.", 502)
        for item in documents:
            try:
                place = validate_place({"id": item.get("id", ""),
                                        "name": item.get("place_name") or item.get("address_name"),
                                        "x": item.get("x"), "y": item.get("y")})
                place["address"] = str(item.get("road_address_name") or item.get("address_name") or "")[:250]
                place["resultType"] = "place"
                places.append(place)
            except (AppError, AttributeError):
                continue
        return places

    # A Kakao place search can miss or rank a bus stop below similarly named
    # businesses. In Seoul mode, merge the transit provider's stop/POI matches
    # so inputs such as "구리여중고" select the actual boarding point.
    transit_future = None
    if config.mode == "seoul" and kind == "keyword" and config.bus_path_key:
        with ThreadPoolExecutor(max_workers=2) as pool:
            kakao_future = pool.submit(kakao_places)
            transit_future = pool.submit(bus_transit.search, query, config.bus_path_key)
            places = kakao_future.result()
            try:
                transit = transit_future.result()
            except seoul_transit.SeoulError:
                transit = []
    else:
        places, transit = kakao_places(), []
    merged, seen = [], set()
    for place in [dict(item, resultType="transit") for item in transit] + places:
        marker = (place["name"].replace(" ", "").lower(), round(place["x"], 5), round(place["y"], 5))
        if marker not in seen:
            seen.add(marker)
            merged.append(place)
    return merged[:12]


def selected_snapshot(body):
    origin, destination, departure, margin, mode = live_inputs(body)
    if mode != 'auto':
        raise AppError('통합 경로를 먼저 검색해 주세요.', 409)
    access = journeys.access_minutes(body.get('accessMinutes', 10))
    entry, route = journeys.resolve(body.get('routeToken'), body.get('routeId'), origin, destination, departure, margin, access)
    return route, origin, destination, departure


def calculate_stress(body, config):
    try:
        route, _, _, _ = selected_snapshot(body)
        return stress.evaluate(route, body.get('target', 'weakest'))
    except seoul_transit.SeoulError as exc:
        raise AppError(str(exc), exc.status) from None


def calculate_recovery(body, config):
    try:
        route, _, _, _ = selected_snapshot(body)
        delay = body.get('delayMinutes')
        if type(delay) is not int or delay not in (0, 5, 10):
            raise AppError('지체 시간을 다시 선택해 주세요.')
        return stress.recover(route, body.get('rideIndex'), delay, config.seoul_key)
    except seoul_transit.SeoulError as exc:
        raise AppError(str(exc), exc.status) from None


def calculate_walk(body, config):
    if not config.rest_key:
        raise AppError('카카오 REST API 키를 .env에 넣고 서버를 다시 실행해 주세요.', 503)
    try:
        route, origin, destination, _ = selected_snapshot(body)
        try:
            start = walking.point(route, origin, body.get('pointKind'), body.get('rideIndex'))
        except seoul_transit.SeoulError as exc:
            if exc.status != 422:
                raise
            # Resolve missing interchange coordinates only from an exact subway
            # station + line match; do not silently use a similarly named POI.
            rides = [s for s in route['steps'] if s['type'] in ('SUBWAY', 'BUS')]
            step = rides[body['rideIndex']]
            if step['type'] != 'SUBWAY':
                raise
            name = step['stops'][0 if body['pointKind'] == 'boarding' else -1]
            station = seoul_transit.station_name(name)
            query = station if station.endswith('역') else station + '역'
            places = kakao_get('/v2/local/search/keyword.json', {'query':query, 'category_group_code':'SW8', 'size':15}, config, secure=True)
            matches = [p for p in places.get('documents', []) if seoul_transit.station_name(str(p.get('place_name','')).split(' ')[0]) == station
                       and any(line in p.get('place_name','') for line in step.get('vehicles', []))]
            if len(matches) != 1:
                raise seoul_transit.SeoulError('이 환승역의 노선별 위치를 하나로 확인하지 못했습니다. 도보 출발 지점을 직접 확인해 주세요.', 422)
            start = validate_place({'name':name,'x':matches[0].get('x'),'y':matches[0].get('y')})
        departure = parse_departure(body.get('walkDeparture'))
        payload = kakao_get('/v2/routing/walk', {'start_x': start['x'], 'start_y': start['y'],
            'end_x': destination['x'], 'end_y': destination['y'], 's_name': start['name'], 'e_name': destination['name'],
            'input_coord': 'WGS84', 'output_coord': 'WGS84'}, config, secure=True)
        return walking.normalize(payload, start, destination, departure)
    except seoul_transit.SeoulError as exc:
        raise AppError(str(exc), exc.status) from None


def demo_routes(origin, destination):
    known = {p["id"]: p for p in DEMO_PLACES}
    for place in (origin, destination):
        expected = known.get(place["id"])
        if not expected or any(abs(place[key] - expected[key]) > 0.00001 for key in ("x", "y")):
            raise AppError("예제 모드에서는 아래 예제 경로를 선택해 주세요. 실제 장소 검색은 REST API 키 설정 후 가능합니다.", 422)
    pair = frozenset((origin["id"], destination["id"]))
    if pair == frozenset(("demo-hongdae", "demo-gangnam")):
        durations = [(4, 38, 7), (2, 49, 3)]
    elif pair == frozenset(("demo-konkuk", "demo-seoul")):
        durations = [(5, 30, 6), (2, 43, 3)]
    else:
        raise AppError("이 조합의 예제는 준비되지 않았어요. ‘홍대입구 → 강남’ 또는 ‘건대입구 → 서울역’을 선택해 주세요.", 422)
    routes = []
    for walk_start, transit, walk_end in durations:
        legs = [("WALKING", walk_start, "승차 지점까지 걷기"),
                ("SUBWAY", transit, "예제 지하철 이동"),
                ("WALKING", walk_end, "목적지까지 걷기")]
        steps = [{"properties": {"type": kind, "time": minutes * 60,
                   "distance": minutes * (65 if kind == "WALKING" else 450),
                   "guidance": title, "vehicles": [{"name": "예제 노선"}] if kind == "SUBWAY" else [],
                   "stops": [{"name": origin["name"]}, {"name": destination["name"]}] if kind == "SUBWAY" else []}}
                 for kind, minutes, title in legs]
        routes.append({"properties": {"type": "SUBWAY", "totalTime": sum((walk_start, transit, walk_end)) * 60,
                       "totalDistance": sum(s["properties"]["distance"] for s in steps), "transfers": 0}, "steps": steps})
    return {"status": "OK", "routes": routes}


def normalize_routes(payload, departure):
    if payload.get("status") != "OK":
        raise AppError("이 구간의 대중교통 경로를 찾지 못했어요. 장소를 바꾸어 다시 검색해 주세요.", 422)
    result = []
    routes = payload.get("routes")
    if not isinstance(routes, list):
        raise AppError("사용할 수 있는 경로 정보가 없어요. 다른 장소로 검색해 주세요.", 422)
    for index, raw in enumerate(routes[:30]):
        try:
            props = raw["properties"]
            seconds = math.ceil(finite_number(props["totalTime"], 1, 604800))
            steps = []
            for step in raw.get("steps", []):
                p = step["properties"]
                points = []
                for point in step.get("path", {}).get("points", []):
                    try:
                        if len(point) == 2:
                            points.append([finite_number(point[0], -180, 180), finite_number(point[1], -90, 90)])
                    except (TypeError, ValueError):
                        continue
                steps.append({"type": p.get("type", "OTHER"), "title": str(p.get("guidance", "이동")),
                              "durationSeconds": math.ceil(finite_number(p.get("time", 0))),
                              "distanceMeters": round(finite_number(p.get("distance", 0))),
                              "vehicles": [str(v.get("name", "")) for v in p.get("vehicles", [])],
                              "stops": [str(s.get("name", "")) for s in p.get("stops", [])], "points": points})
            fare = (props.get("fare") or {}).get("value")
            result.append({"id": str(index), "type": props.get("type", "TRANSIT"),
                           "durationSeconds": seconds, "durationMinutes": math.ceil(seconds / 60),
                           "departure": departure.isoformat(),
                           "arrival": (departure + timedelta(seconds=seconds)).isoformat(),
                           "transfers": round(finite_number(props.get("transfers", 0), 0, 100)),
                           "walkSeconds": sum(s["durationSeconds"] for s in steps if s["type"] == "WALKING"),
                           "distanceMeters": round(finite_number(props.get("totalDistance", 0))),
                           "fare": round(finite_number(fare)) if fare is not None else None,
                           "steps": steps})
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    if not result:
        raise AppError("사용할 수 있는 경로 정보가 없어요. 다른 장소로 검색해 주세요.", 422)
    return sorted(result, key=lambda r: r["durationSeconds"])[:6]


def calculate_routes(body, config):
    if not isinstance(body, dict):
        raise AppError("요청 형식이 올바르지 않습니다.")
    if config.mode == "seoul":
        return calculate_seoul(body, config)
    origin, destination = validate_place(body.get("origin")), validate_place(body.get("destination"))
    if abs(origin["x"] - destination["x"]) < 0.000001 and abs(origin["y"] - destination["y"]) < 0.000001:
        raise AppError("출발지와 목적지가 같아요. 다른 장소를 선택해 주세요.")
    departure = parse_departure(body.get("departure"))
    if config.mode == "demo":
        payload = demo_routes(origin, destination)
    else:
        # No time parameter exists: do not imply that departure changes this route.
        payload = kakao_get("/v2/routing/publictraffic", {
            "start_x": origin["x"], "start_y": origin["y"], "end_x": destination["x"], "end_y": destination["y"],
            "s_name": origin["name"], "e_name": destination["name"], "input_coord": "WGS84", "output_coord": "WGS84"}, config)
    return {"source": config.mode, "timeAware": False, "notice": NOTICE,
            "demoNotice": DEMO_NOTICE if config.mode == "demo" else None,
            "origin": origin, "destination": destination, "routes": normalize_routes(payload, departure)}


def live_inputs(body):
    if not isinstance(body, dict):
        raise AppError('요청 형식이 올바르지 않습니다.')
    departure = parse_departure(body.get('departure'))
    buffer_minutes = body.get('bufferMinutes', 5)
    if type(buffer_minutes) is not int or buffer_minutes not in (5, 10):
        raise AppError('여유시간은 5분 또는 10분으로 선택해 주세요.')
    journey_mode = body.get('journeyMode', 'subway')
    if journey_mode not in ('subway', 'mixed', 'auto'):
        raise AppError('이동 수단을 선택해 주세요.')
    if journey_mode == 'subway':
        origin = {'name': seoul_transit.station_name(body.get('origin'))}
        destination = {'name': seoul_transit.station_name(body.get('destination'))}
        if origin == destination:
            raise AppError('출발역과 도착역이 같아요. 다른 역을 입력해 주세요.')
    else:
        origin, destination = validate_place(body.get('origin')), validate_place(body.get('destination'))
        if abs(origin['x'] - destination['x']) < .000001 and abs(origin['y'] - destination['y']) < .000001:
            raise AppError('출발지와 목적지가 같아요. 다른 장소를 선택해 주세요.')
    return origin, destination, departure, buffer_minutes, journey_mode


def calculate_seoul(body, config):
    try:
        origin, destination, departure, margin, mode = live_inputs(body)
        if mode == 'auto':
            access = journeys.access_minutes(body.get('accessMinutes', 10))
            return journeys.find(origin, destination, departure, margin, access, config)
        if mode == 'subway':
            if not config.seoul_key:
                raise AppError('지하철 API 인증키가 없습니다.', 503)
            routes = seoul_transit.routes(origin['name'], destination['name'], departure, config.seoul_key, margin)
        else:
            routes = bus_transit.routes(origin, destination, departure, config.bus_path_key, margin)
        return {'source': 'seoul', 'journeyMode': mode, 'origin': origin, 'destination': destination,
                'timeAware': mode == 'subway' and all(r['scheduleStatus'] == 'complete' for r in routes),
                'demoNotice': None, 'notice': '공식 시간표·예정정보에 기반한 참고 안내입니다. 실시간 지연·운휴와 역 밖 도보 길찾기·택시비는 제외합니다. 출발 시각은 첫 승차 지점에 도착한 시각 기준입니다.',
                'routes': routes}
    except seoul_transit.SeoulError as exc:
        raise AppError(str(exc), exc.status) from None


def calculate_deadline(body, config):
    if config.mode != 'seoul':
        raise AppError('막차 참고 조회는 서울 교통 API 설정 후 사용할 수 있습니다.', 422)
    try:
        origin, destination, departure, margin, mode = live_inputs(body)
        if mode == 'auto':
            access = journeys.access_minutes(body.get('accessMinutes', 10))
            return journeys.deadline(body.get('routeToken'), body.get('routeId'), origin, destination, departure, margin, access, config)
        if mode == 'subway':
            if not config.seoul_key:
                raise AppError('지하철 API 인증키가 없습니다.', 503)
            return seoul_transit.late_departure(origin['name'], destination['name'], departure, config.seoul_key, margin)
        # Re-fetch selected path on the server; never trust client-supplied legs.
        routes = bus_transit.routes(origin, destination, departure, config.bus_path_key, margin)
        route_id = body.get('routeId', routes[0]['id'])
        selected = next((r for r in routes if r['id'] == route_id), None)
        if not selected:
            raise AppError('경로가 변경됐습니다. 다시 조회해 주세요.')
        expected = body.get('routeSignature')
        signature = '|'.join(s['routeId'] + ':' + s['fromId'] + ':' + s['toId'] for s in selected['steps'])
        if expected is not None and expected != signature:
            raise AppError('API 경로가 변경됐습니다. 귀갓길을 다시 조회해 주세요.', 409)
        result = bus_transit.deadline(selected, departure, config, margin)
        result['alternatives'] = []
        if result.get('past'):
            # Bounded alternatives: disclose that these are estimated candidates.
            for other in [r for r in routes if r is not selected][:2]:
                candidate = bus_transit.deadline(other, departure, config, margin)
                if candidate['latestDeparture'] and not candidate['past']:
                    result['alternatives'].append({'routeId': other['id'], 'lines': [s['title'] for s in other['steps']], 'latestDeparture': candidate['latestDeparture']})
            result['notice'] += ' 다른 경로 최대 2개도 확인했습니다. 대안은 예상 시간 조건상 후보이며 실제 환승 성공을 보장하지 않습니다.'
        return result
    except seoul_transit.SeoulError as exc:
        raise AppError(str(exc), exc.status) from None


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, config):
        self.config = config
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeTime/0.1"

    def log_message(self, *args):
        # Do not log user addresses or search queries.
        pass

    def send_data(self, status, content, mime="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json(self, value, status=200):
        self.send_data(status, json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))

    def check_local_request(self):
        port = self.server.server_port
        hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
        if self.headers.get("Host") not in hosts:
            raise AppError("로컬 주소로 접속해 주세요.", 403)
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + host for host in hosts}:
            raise AppError("허용되지 않은 요청입니다.", 403)

    def do_GET(self):
        try:
            self.check_local_request()
            parsed = urllib.parse.urlparse(self.path)
            config = self.server.config
            if parsed.path == "/api/config":
                return self.json({"mode": config.mode, "javascriptKey": config.javascript_key if config.mode == "kakao" else "",
                                  "subwayConfigured": bool(config.seoul_key), "busConfigured": bool(config.bus_path_key and config.bus_station_key),
                                  "walkingConfigured": bool(config.rest_key),
                                  "notice": NOTICE, "demoNotice": DEMO_NOTICE,
                                  "examples": [{"origin": DEMO_PLACES[0], "destination": DEMO_PLACES[1]},
                                               {"origin": DEMO_PLACES[2], "destination": DEMO_PLACES[3]}] if config.mode in ("demo", "seoul") else []})
            if parsed.path == "/api/places":
                params = urllib.parse.parse_qs(parsed.query)
                places = search_places(params.get("q", [""])[0], params.get("kind", ["keyword"])[0], config)
                return self.json({"places": places, "source": config.mode})
            files = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
                     "/styles.css": ("styles.css", "text/css"), "/app.js": ("app.js", "text/javascript"),
                     "/utils.js": ("utils.js", "text/javascript"), "/stress-ui.js": ("stress-ui.js", "text/javascript"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}
            if parsed.path not in files:
                raise AppError("페이지를 찾을 수 없습니다.", 404)
            filename, mime = files[parsed.path]
            self.send_data(200, (ROOT / "frontend" / filename).read_bytes(), mime + "; charset=utf-8")
        except AppError as exc:
            self.json({"error": str(exc)}, exc.status)
        except Exception:
            self.json({"error": "요청을 처리하지 못했습니다. 서버를 확인해 주세요."}, 500)

    def do_POST(self):
        try:
            self.check_local_request()
            if self.path not in ("/api/routes", "/api/deadline", "/api/stress", "/api/recovery", "/api/walk"):
                raise AppError("페이지를 찾을 수 없습니다.", 404)
            if self.headers.get_content_type() != "application/json":
                raise AppError("JSON 형식으로 요청해 주세요.", 415)
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise AppError("요청 크기가 올바르지 않습니다.") from None
            if not 0 < size <= 16384:
                raise AppError("요청 크기가 올바르지 않습니다.", 413)
            try:
                body = json.loads(self.rfile.read(size))
            except (ValueError, UnicodeError):
                raise AppError("JSON 요청을 읽지 못했습니다.") from None
            function = {'/api/routes': calculate_routes, '/api/deadline': calculate_deadline,
                        '/api/stress': calculate_stress, '/api/recovery': calculate_recovery, '/api/walk': calculate_walk}[self.path]
            self.json(function(body, self.server.config))
        except AppError as exc:
            self.json({"error": str(exc)}, exc.status)
        except Exception:
            self.json({"error": "경로 계산 중 문제가 발생했어요. 입력을 확인하고 다시 시도해 주세요."}, 500)


if __name__ == "__main__":
    config = Config.load()
    server = AppServer(("127.0.0.1", config.port), config)
    print(f"HomeTime: http://localhost:{server.server_port} | mode={config.mode}", flush=True)
    print("Ctrl+C to stop. REST API key is never sent to the browser.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
