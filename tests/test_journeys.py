import copy
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import app
import journeys
import bus_transit as bus
import seoul_transit as rail


DAY = datetime(2026, 9, 12, 18, tzinfo=rail.KST)


def raw_route(kind='BUS', minutes=30):
    leg = {'routeNm': '740' if kind == 'BUS' else '2호선', 'fid': 'a', 'tid': 'b',
           'fname': '홍대입구', 'tname': '강남', 'fx': 126.92, 'fy': 37.55,
           'tx': 127.02, 'ty': 37.49, 'routeId': '740' if kind == 'BUS' else '',
           'railLinkList': [] if kind == 'BUS' else [{}]}
    return {'time': minutes, 'distance': 10000, 'pathList': [leg]}


class UnifiedTests(unittest.TestCase):
    def setUp(self):
        journeys._snapshots.clear()
        self.origin, self.destination = map(dict, app.DEMO_PLACES[:2])
        self.config = app.Config(bus_path_key='test', bus_station_key='test')

    def test_access_validation(self):
        for value in (0, 10, 120):
            self.assertEqual(journeys.access_minutes(value), value)
        for value in (True, None, '10', -1, 121, 1.5):
            with self.subTest(value=value), self.assertRaises(rail.SeoulError):
                journeys.access_minutes(value)

    def test_all_route_types_and_stable_deduplication(self):
        def get(service, operation, params, key):
            rows = [raw_route('SUBWAY', 20)] if operation == 'getPathInfoBySubway' else [raw_route()]
            return {'msgHeader': {'headerCd': '0'}, 'msgBody': {'itemList': rows}}
        with patch.object(bus, 'get', side_effect=get) as provider:
            routes, failures = bus.all_routes(self.origin, self.destination, DAY, 'test', 5)
        self.assertEqual(provider.call_count, 3)
        self.assertEqual([r['type'] for r in routes], ['SUBWAY', 'BUS'])
        self.assertFalse(failures)
        self.assertEqual(bus.normalize([raw_route(minutes=31)], DAY, 5)[0]['id'], routes[1]['id'])

    def test_partial_failure_keeps_real_routes(self):
        def get(service, operation, params, key):
            if operation == 'getPathInfoBySubway':
                raise rail.SeoulError('upstream unavailable')
            return {'msgHeader': {'headerCd': '0'}, 'msgBody': {'itemList': [raw_route()]}}
        with patch.object(bus, 'get', side_effect=get):
            routes, failures = bus.all_routes(self.origin, self.destination, DAY, 'test', 5)
        self.assertEqual(len(routes), 1)
        self.assertEqual(failures, ['upstream unavailable'])

    def test_all_failures_do_not_fabricate_routes(self):
        with patch.object(bus, 'get', side_effect=rail.SeoulError('failed')):
            with self.assertRaises(rail.SeoulError):
                bus.all_routes(self.origin, self.destination, DAY, 'test', 5)

    def test_origin_deadline_midnight_and_unknown(self):
        original = {'latestDeparture': '2026-09-13T00:05:25+09:00'}
        report = journeys.origin_deadline(original, DAY, 10)
        self.assertEqual(report['originLatestDeparture'], '2026-09-12T23:55:25+09:00')
        self.assertFalse(report['past'])
        self.assertNotIn('originLatestDeparture', original)
        self.assertIsNone(journeys.origin_deadline({}, DAY, 10)['originLatestDeparture'])

    def snapshot(self):
        route = bus.normalize([raw_route()], DAY + timedelta(minutes=10), 5)[0]
        with patch.object(bus, 'all_routes', return_value=([route], [])):
            return journeys.find(self.origin, self.destination, DAY, 5, 10, self.config)

    def test_access_is_counted_once_and_snapshot_bound(self):
        found = self.snapshot()
        route = found['routes'][0]
        self.assertEqual(route['durationMinutes'], 45)
        self.assertEqual(route['arrival'], '2026-09-12T18:45:00+09:00')
        self.assertEqual(route['departure'], DAY.isoformat())
        with self.assertRaises(rail.SeoulError):
            journeys.deadline(found['routeToken'], route['id'], self.origin, self.destination, DAY, 5, 11, self.config)
        with self.assertRaises(rail.SeoulError):
            journeys.deadline(found['routeToken'], 'unknown', self.origin, self.destination, DAY, 5, 10, self.config)

    def test_deadline_reuses_trusted_snapshot_and_cache(self):
        found = self.snapshot()
        route_id = found['routes'][0]['id']
        found['routes'][0]['steps'][0]['title'] = 'client mutation'
        args = (found['routeToken'], route_id, self.origin, self.destination, DAY, 5, 10, self.config)
        with patch.object(bus, 'deadline', return_value={'latestDeparture': '2026-09-12T23:05:25+09:00'}) as calc:
            report = journeys.deadline(*args)
            report['originLatestDeparture'] = 'client mutation'
            second = journeys.deadline(*args)
        self.assertEqual(calc.call_count, 1)
        self.assertEqual(calc.call_args.args[0]['steps'][0]['title'], '740')
        self.assertEqual(second['originLatestDeparture'], '2026-09-12T22:55:25+09:00')
        journeys._snapshots[found['routeToken']]['created'] -= journeys.TTL + 1
        with self.assertRaises(rail.SeoulError):
            journeys.deadline(*args)

    def test_backward_transfer_constraints_and_missing_duration(self):
        points = [{'boardAt': '2026-09-12T23:50:00+09:00', 'durationSeconds': 1800},
                  {'boardAt': '2026-09-12T23:40:00+09:00', 'durationSeconds': None}]
        self.assertEqual(bus.backward_deadline(points, 5), '2026-09-12T23:00:00+09:00')
        points[0]['durationSeconds'] = None
        self.assertIsNone(bus.backward_deadline(points, 5))

    def test_corridor_merges_same_line_only(self):
        route = {'steps': [{'type': 'SUBWAY', 'vehicles': ['2호선'], 'title': '2호선', 'stops': ['홍대입구역', '성수역']},
                           {'type': 'SUBWAY', 'vehicles': ['2호선'], 'title': '2호선', 'stops': ['성수역', '강남역']}]}
        self.assertEqual(rail.corridor(route), [('2호선', '홍대입구', '강남')])


if __name__ == '__main__':
    unittest.main()
