import copy
import io
import json
import sys
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import app
import taxi
from test_stress import route


class TaxiTests(unittest.TestCase):
    def setUp(self):
        self.origin, self.destination = app.DEMO_PLACES[:2]
        self.departure = app.parse_departure('2026-09-12T23:50')
        self.payload = {'routes': [{'result_code': 0, 'summary': {'fare': {'taxi': 10300, 'toll': 1500}, 'distance': 5000, 'duration': 1200}}]}

    def test_fare_time_midnight_and_toll_not_added(self):
        result = taxi.normalize(self.payload, self.origin, self.destination, self.departure, True)
        self.assertEqual(result['fareWon'], 10300)
        self.assertEqual(result['tollWon'], 1500)
        self.assertEqual(result['arrival'], '2026-09-13T00:10:00+09:00')
        self.assertEqual(result['surchargeStatus'], 'unknown')
        self.assertEqual(result['timeBasis'], 'future')

    def test_missing_fare_is_not_free(self):
        for value in (None, 0, -1, True, 'NaN'):
            data = copy.deepcopy(self.payload); data['routes'][0]['summary']['fare']['taxi'] = value
            with self.subTest(value=value):
                result = taxi.normalize(data, self.origin, self.destination, self.departure, False)
                self.assertIsNone(result['fareWon']); self.assertEqual(result['fareStatus'], 'unknown')

    def test_no_fabricated_route_or_time(self):
        for data in ({}, {'routes': []}, {'routes': [{'result_code': 104}]}, {'routes': [None]}):
            with self.subTest(data=data), self.assertRaises(app.seoul_transit.SeoulError):
                taxi.normalize(data, self.origin, self.destination, self.departure, False)
        for value in (None, True, 0, -1, 'NaN'):
            data = copy.deepcopy(self.payload); data['routes'][0]['summary']['duration'] = value
            with self.assertRaises(app.seoul_transit.SeoulError): taxi.normalize(data, self.origin, self.destination, self.departure, False)

    def test_current_minute_future_and_past(self):
        now = self.departure + timedelta(seconds=30)
        self.assertEqual(taxi.request_context(self.departure, now), (now, False))
        later = self.departure + timedelta(minutes=10)
        self.assertEqual(taxi.request_context(later, now), (later, True))
        with self.assertRaises(app.seoul_transit.SeoulError): taxi.request_context(self.departure - timedelta(minutes=1), now)

    def test_https_header_future_parameter_and_no_redirect(self):
        opener = Mock(); opener.open.return_value = io.BytesIO(json.dumps(self.payload).encode())
        with patch.object(taxi.urllib.request, 'build_opener', return_value=opener) as build:
            taxi.fetch(self.origin, self.destination, self.departure, 'private-test-key', True)
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.startswith('https://apis-navi.kakaomobility.com/v1/future/directions?'))
        self.assertIn('departure_time=202609122350', request.full_url)
        self.assertNotIn('private-test-key', request.full_url)
        self.assertEqual(request.headers['Authorization'], 'KakaoAK private-test-key')
        self.assertIsInstance(build.call_args.args[0], taxi.NoRedirect)

    def test_remote_errors_do_not_expose_upstream_text(self):
        for error in (urllib.error.HTTPError('private-url', 403, 'secret', {}, None),
                      urllib.error.HTTPError('private-url', 429, 'secret', {}, None), TimeoutError('secret')):
            opener = Mock(); opener.open.side_effect = error
            with patch.object(taxi.urllib.request, 'build_opener', return_value=opener), self.assertRaises(app.seoul_transit.SeoulError) as caught:
                taxi.fetch(self.origin, self.destination, self.departure, 'private-key', False)
            self.assertNotIn('secret', str(caught.exception)); self.assertNotIn('private', str(caught.exception))

    def test_missing_key_and_stale_snapshot(self):
        with self.assertRaises(app.AppError) as caught: app.calculate_taxi({}, app.Config())
        self.assertEqual(caught.exception.status, 503)
        body = {'origin': self.origin, 'destination': self.destination, 'departure': '2026-09-12T23:00', 'journeyMode': 'auto', 'routeToken': 'bad', 'routeId': 'bad'}
        with self.assertRaises(app.AppError) as caught: app.calculate_taxi(body, app.Config(rest_key='test'))
        self.assertEqual(caught.exception.status, 409)

    def test_selected_interchange_uses_snapshot_not_client_coordinates(self):
        body = {'pointKind': 'boarding', 'rideIndex': 1, 'taxiDeparture': '2026-09-12T23:50', 'x': 0, 'y': 0}
        with patch.object(app, 'selected_snapshot', return_value=(route(), self.origin, self.destination, self.departure)), \
             patch.object(taxi, 'request_context', return_value=(self.departure, True)), patch.object(taxi, 'fetch', return_value=self.payload) as fetch:
            result = app.calculate_taxi(body, app.Config(rest_key='test'))
        self.assertEqual(fetch.call_args.args[0]['x'], 126.9817)
        self.assertEqual(result['origin']['name'], '사당')

    def test_unknown_bus_coordinate_is_not_guessed(self):
        data = route(); data['steps'][-1]['type'] = 'BUS'; del data['steps'][-1]['fromX']
        body = {'pointKind': 'boarding', 'rideIndex': 1, 'taxiDeparture': '2026-09-12T23:50'}
        with patch.object(app, 'selected_snapshot', return_value=(data, self.origin, self.destination, self.departure)), \
             patch.object(taxi, 'request_context', return_value=(self.departure, True)), patch.object(taxi, 'fetch') as fetch:
            with self.assertRaises(app.AppError): app.calculate_taxi(body, app.Config(rest_key='test'))
            fetch.assert_not_called()


if __name__ == '__main__': unittest.main()
