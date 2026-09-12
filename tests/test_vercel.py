import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'backend'))
import app
import journeys
import route_tokens
from api.index import handler
from test_journeys import DAY, raw_route


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.key = 'test-only-signing-key-32-characters-long'
        self.origin, self.destination = map(dict, app.DEMO_PLACES[:2])
        self.context = journeys.context(self.origin, self.destination, DAY, 5, 10)
        self.routes = [{'id': 'route-1', 'steps': [{'type': 'BUS', 'title': '740'}]}]

    def test_signature_expiry_and_no_secret(self):
        with patch.object(route_tokens.time, 'time', return_value=1000):
            token = route_tokens.encode(self.context, self.routes, self.key)
            self.assertEqual(route_tokens.decode(token, self.key)['routes'], self.routes)
            self.assertNotIn(self.key, token)
            for bad in (token[:-1] + ('0' if token[-1] != '0' else '1'), 'v1.bad.bad', token + 'x'):
                with self.assertRaises(app.seoul_transit.SeoulError): route_tokens.decode(bad, self.key)
            with self.assertRaises(app.seoul_transit.SeoulError): route_tokens.decode(token, 'different-key')
        with patch.object(route_tokens.time, 'time', return_value=1301):
            with self.assertRaises(app.seoul_transit.SeoulError): route_tokens.decode(token, self.key)

    def test_size_bounds_and_missing_deployment_secret(self):
        with self.assertRaises(app.seoul_transit.SeoulError): route_tokens.decode('v1.' + 'a' * 48000, self.key)
        with self.assertRaises(app.seoul_transit.SeoulError): route_tokens.encode(self.context, ['a' * 270000], self.key)
        with patch.dict(os.environ, {'VERCEL': '1', 'JOURNEY_TOKEN_SECRET': ''}):
            with self.assertRaises(app.seoul_transit.SeoulError): route_tokens.secret()

    def test_new_instance_can_restore_but_cannot_change_context(self):
        with patch.dict(os.environ, {'JOURNEY_TOKEN_SECRET': self.key}):
            route = journeys.bus.normalize([raw_route()], DAY, 5)[0]
            with patch.object(journeys.bus, 'all_routes', return_value=([route], [])):
                found = journeys.find(self.origin, self.destination, DAY, 5, 10, app.Config(bus_path_key='test'))
            journeys._snapshots.clear()
            token = found['routeToken']; route_id = found['routes'][0]['id']
            entry, restored = journeys.resolve(token, route_id, self.origin, self.destination, DAY, 5, 10)
            self.assertEqual(restored['id'], route_id)
            self.assertEqual(entry['context'], self.context)
            with self.assertRaises(app.seoul_transit.SeoulError):
                journeys.resolve(token, route_id, self.origin, self.destination, DAY, 5, 11)


class VercelHandlerTests(unittest.TestCase):
    def request(self, host='demo.vercel.app', origin=None, path='/api/index.py?endpoint=config'):
        item = object.__new__(handler)
        item.headers = {'Host': host}
        if origin: item.headers['Origin'] = origin
        item.path = path
        item.server = type('Server', (), {})()
        return item

    def test_trusted_host_rewrite_and_encoded_search(self):
        with patch.dict(os.environ, {'VERCEL_URL': 'demo.vercel.app'}):
            request = self.request(origin='https://demo.vercel.app', path='/api/index.py?endpoint=places&q=%EC%84%9C%EC%9A%B8')
            request.check_local_request()
            self.assertTrue(request.path.startswith('/api/places?q='))

    def test_foreign_origin_host_and_private_path_are_rejected(self):
        with patch.dict(os.environ, {'VERCEL_URL': 'demo.vercel.app'}):
            for request in (self.request(host='evil.example'), self.request(origin='https://evil.example'),
                            self.request(path='/api/index.py?endpoint=../.env'), self.request(path='/frontend/index.html')):
                with self.assertRaises(app.AppError): request.check_local_request()


if __name__ == '__main__': unittest.main()
