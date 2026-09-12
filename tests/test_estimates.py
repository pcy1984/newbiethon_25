import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import app, walking

class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.a={'name':'보문역','x':127.018,'y':37.585}
        self.b={'name':'노원역','x':127.061,'y':37.656}
        self.when=app.parse_departure('2026-09-12T23:50')

    def test_model_is_labelled_without_inventing_directions(self):
        result=walking.estimate(self.a,self.b,self.when)
        self.assertTrue(result['estimated'])
        self.assertEqual(result['directions'],[])
        self.assertEqual(result['durationSeconds'],math.ceil(result['distanceMeters'] / (4000/3600)))
        self.assertIn('직선거리',result['notice'])
        self.assertTrue(result['arrival'].startswith('2026-09-13'))

    def test_invalid_coordinates_are_not_guessed(self):
        with self.assertRaises(app.seoul_transit.SeoulError):
            walking.estimate({'name':'없음','x':float('nan'),'y':37},self.b,self.when)

    def test_walk_api_error_returns_explicit_estimate_only_after_snapshot_validation(self):
        with patch.object(app,'selected_snapshot',return_value=({},self.a,self.b,self.when)), patch.object(app,'selected_point',return_value=self.a), patch.object(app,'kakao_get',side_effect=app.AppError('API error',503)):
            result=app.calculate_walk({'walkDeparture':'2026-09-12T23:50'},app.Config(rest_key='test'))
        self.assertEqual(result['source'],'estimated-walk')
        self.assertNotIn('checkpoints',result)

    def test_real_walk_response_is_preserved(self):
        payload={'status':'OK','route':{'properties':{'totalTime':101,'totalDistance':87},'legs':[{'steps':[{'properties':{'time':101,'distance':87,'guidance':'출입구로 이동'}}]}]}}
        with patch.object(app,'selected_snapshot',return_value=({},self.a,self.b,self.when)), patch.object(app,'selected_point',return_value=self.a), patch.object(app,'kakao_get',return_value=payload):
            result=app.calculate_walk({'walkDeparture':'2026-09-12T23:50'},app.Config(rest_key='test'))
        self.assertEqual(result['distanceMeters'],87)
        self.assertEqual(result['durationSeconds'],101)
        self.assertEqual(result['source'],'kakao-walk')

if __name__=='__main__': unittest.main()
