"""Synthetic unit-test fixture only; never imported by the running application."""
from app import AppError, DEMO_PLACES

def fixture_routes(origin, destination):
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
