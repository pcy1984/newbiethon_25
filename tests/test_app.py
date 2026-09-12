import copy
import io
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import app


def request_body(departure="2026-09-12T23:40"):
    return {"origin": dict(app.DEMO_PLACES[0]), "destination": dict(app.DEMO_PLACES[1]), "departure": departure}


class CalculationTests(unittest.TestCase):
    def test_demo_is_explicit_not_time_aware(self):
        result = app.calculate_routes(request_body(), app.Config())
        self.assertEqual(result["source"], "demo")
        self.assertFalse(result["timeAware"])
        self.assertIn("가상", result["demoNotice"])
        self.assertIn("막차", result["notice"])

    def test_midnight_rollover_and_seconds(self):
        route = app.calculate_routes(request_body(), app.Config())["routes"][0]
        self.assertEqual(route["durationSeconds"], 2940)
        self.assertEqual(route["arrival"], "2026-09-13T00:29:00+09:00")

    def test_year_rollover(self):
        route = app.calculate_routes(request_body("2026-12-31T23:40"), app.Config())["routes"][0]
        self.assertEqual(route["arrival"], "2027-01-01T00:29:00+09:00")

    def test_departure_does_not_fake_time_dependent_duration(self):
        a = app.calculate_routes(request_body("2026-09-12T09:00"), app.Config())["routes"][0]
        b = app.calculate_routes(request_body("2026-09-12T23:00"), app.Config())["routes"][0]
        self.assertEqual(a["durationSeconds"], b["durationSeconds"])
        self.assertNotEqual(a["arrival"], b["arrival"])

    def test_sorted_alternatives_and_walk(self):
        routes = app.calculate_routes(request_body(), app.Config())["routes"]
        self.assertEqual([r["durationMinutes"] for r in routes], [49, 54])
        self.assertEqual([r["walkSeconds"] for r in routes], [660, 300])

    def test_reverse_demo_supported(self):
        body = request_body()
        body["origin"], body["destination"] = body["destination"], body["origin"]
        result = app.calculate_routes(body, app.Config())
        self.assertEqual(result["origin"]["name"], "강남역")
        self.assertEqual(result["routes"][0]["steps"][1]["stops"][0], "강남역")

    def test_other_example(self):
        body = request_body()
        body.update(origin=app.DEMO_PLACES[2], destination=app.DEMO_PLACES[3])
        self.assertEqual(app.calculate_routes(body, app.Config())["routes"][0]["durationMinutes"], 41)

    def test_unsupported_demo_not_invented(self):
        body = request_body()
        body["destination"] = app.DEMO_PLACES[3]
        with self.assertRaises(app.AppError) as caught:
            app.calculate_routes(body, app.Config())
        self.assertEqual(caught.exception.status, 422)

    def test_demo_coordinates_not_spoofed(self):
        body = request_body()
        body["origin"]["x"] = 125
        with self.assertRaises(app.AppError):
            app.calculate_routes(body, app.Config())

    def test_same_place(self):
        body = request_body()
        body["destination"] = body["origin"]
        with self.assertRaisesRegex(app.AppError, "같아요"):
            app.calculate_routes(body, app.Config())

    def test_bad_coordinates(self):
        for value in (None, True, "NaN", "Infinity", {}, 181):
            with self.subTest(value=value), self.assertRaises(app.AppError):
                app.validate_place({"name": "역", "x": value, "y": 37})

    def test_bad_dates(self):
        for date in (None, "2026-02-30T22:00", "2026-09-12T24:10", "2026-9-2T02:10", "2026-09-12", "1999-01-01T12:00"):
            with self.subTest(date=date), self.assertRaises(app.AppError):
                app.parse_departure(date)

    def test_missing_places(self):
        for body in ({}, None, [], {"origin": "서울"}):
            with self.subTest(body=body), self.assertRaises(app.AppError):
                app.calculate_routes(body, app.Config())

    def test_places_search(self):
        self.assertEqual(app.search_places("홍대", "keyword", app.Config())[0]["name"], "홍대입구역")
        self.assertEqual(app.search_places("없는장소", "keyword", app.Config()), [])

    def test_seoul_keyword_search_prioritizes_transit_points(self):
        kakao = {"documents": [{"id": "shop", "place_name": "고뮤즈", "address_name": "경기 구리시",
                                 "x": "127.14", "y": "37.60"}]}
        stop = {"id": "221000085", "name": "구리여중고.청소년문화의집", "address": "대중교통 승차 지점",
                "x": 127.14979, "y": 37.59172}
        with patch.object(app, "kakao_get", return_value=kakao), patch.object(app.bus_transit, "search", return_value=[stop]):
            places = app.search_places("구리여중고", "keyword", app.Config(rest_key="key", bus_path_key="bus"))
        self.assertEqual(places[0]["name"], "구리여중고.청소년문화의집")
        self.assertEqual(places[0]["resultType"], "transit")
        self.assertEqual(places[1]["resultType"], "place")

    def test_empty_query(self):
        with self.assertRaises(app.AppError):
            app.search_places("  ", "keyword", app.Config())

    def test_provider_contract_no_departure_parameter(self):
        fixture = app.demo_routes(request_body()["origin"], request_body()["destination"])
        with patch.object(app, "kakao_get", return_value=fixture) as get:
            result = app.calculate_routes(request_body(), app.Config(rest_key="server-secret"))
        self.assertEqual(result["source"], "kakao")
        self.assertIsNone(result["demoNotice"])
        self.assertEqual(get.call_args.args[0], "/v2/routing/publictraffic")
        self.assertEqual(get.call_args.args[1]["start_x"], 126.9244)
        self.assertNotIn("departure_time", get.call_args.args[1])
        self.assertNotIn("server-secret", json.dumps(result))

    def test_no_results(self):
        with self.assertRaises(app.AppError):
            app.normalize_routes({"status": "NO_RESULTS"}, app.parse_departure("2026-09-12T22:00"))

    def test_bad_routes_skipped(self):
        fixture = app.demo_routes(request_body()["origin"], request_body()["destination"])
        fixture["routes"].insert(0, {"properties": {"totalTime": "NaN"}})
        self.assertEqual(len(app.normalize_routes(fixture, app.parse_departure("2026-09-12T22:00"))), 2)

    def test_duration_rounding_and_coordinates(self):
        fixture = app.demo_routes(request_body()["origin"], request_body()["destination"])
        fixture["routes"][0]["properties"]["totalTime"] = 61
        fixture["routes"][0]["steps"][0]["path"] = {"points": [[127.1, 37.1], [999, 37], [None, 2]]}
        route = app.normalize_routes(fixture, app.parse_departure("2026-09-12T22:00"))[0]
        self.assertEqual(route["durationMinutes"], 2)
        self.assertEqual(route["arrival"], "2026-09-12T22:01:01+09:00")
        self.assertEqual(route["steps"][0]["points"], [[127.1, 37.1]])

    def test_place_address_provider_contract(self):
        with patch.object(app, "kakao_get", return_value={"documents": [{"address_name": "서울 주소", "x": "127.1", "y": "37.2"}]}) as get:
            places = app.search_places("서울", "address", app.Config(rest_key="key"))
        self.assertEqual(get.call_args.args[0], "/v2/local/search/address.json")
        self.assertEqual(places[0]["x"], 127.1)

    def test_auth_rate_timeout_errors_not_demo_fallback(self):
        for code in (401, 403, 429, 500):
            with self.subTest(code=code), patch.object(urllib.request, "urlopen", side_effect=urllib.error.HTTPError("url", code, "private-detail", {}, None)):
                with self.assertRaises(app.AppError) as caught:
                    app.calculate_routes(request_body(), app.Config(rest_key="private-key"))
                self.assertNotIn("private", str(caught.exception))
        with patch.object(urllib.request, "urlopen", side_effect=TimeoutError()):
            with self.assertRaises(app.AppError) as caught:
                app.calculate_routes(request_body(), app.Config(rest_key="private-key"))
            self.assertEqual(caught.exception.status, 503)

    def test_invalid_upstream_json(self):
        response = io.BytesIO(b"not json")
        with patch.object(urllib.request, "urlopen", return_value=response), self.assertRaises(app.AppError):
            app.kakao_get("/path", {}, app.Config(rest_key="private-key"))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.AppServer(("127.0.0.1", 0), app.Config())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_static_files(self):
        for path, mime in (("/", "text/html"), ("/app.js", "text/javascript"), ("/styles.css", "text/css")):
            with urllib.request.urlopen(self.url + path) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(mime, response.headers["Content-Type"])

    def test_config_no_rest_key(self):
        with urllib.request.urlopen(self.url + "/api/config") as response:
            data = json.load(response)
        self.assertEqual(data["mode"], "demo")
        self.assertNotIn("rest_key", data)
        self.assertNotIn("restKey", data)

    def test_post_end_to_end(self):
        request = urllib.request.Request(self.url + "/api/routes", data=json.dumps(request_body()).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            data = json.load(response)
        self.assertEqual(data["routes"][0]["durationMinutes"], 49)

    def test_private_files_not_served(self):
        for path in ("/.env", "/backend/app.py", "/.git/config", "/../README.md", "/%2e%2e/.env"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.url + path)
            self.assertEqual(caught.exception.code, 404)

    def test_foreign_origin(self):
        request = urllib.request.Request(self.url + "/api/config", headers={"Origin": "https://example.com"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 403)

    def test_bad_json(self):
        request = urllib.request.Request(self.url + "/api/routes", data=b"broken", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 400)

    def test_live_config_does_not_leak_key(self):
        with patch.object(self.server, "config", app.Config(rest_key="secret-never-expose", javascript_key="public-js-key")):
            with urllib.request.urlopen(self.url + "/api/config") as response:
                raw = response.read().decode()
        self.assertNotIn("secret-never-expose", raw)
        self.assertIn("public-js-key", raw)


if __name__ == "__main__":
    unittest.main()
