import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import app
import stress
import walking
import journeys
import seoul_transit as rail


def route():
    return {'id':'test', 'label':'2호선 → 4호선', 'type':'SUBWAY', 'scheduleStatus':'complete',
            'departure':'2026-09-12T22:40:00+09:00', 'stationDeparture':'2026-09-12T22:50:00+09:00',
            'bufferMinutes':5, 'durationSeconds':3600, 'steps':[
                {'type':'WAITING','durationSeconds':900},
                {'type':'SUBWAY','title':'2호선','vehicles':['2호선'], 'stops':['홍대입구','사당'],
                 'departure':'2026-09-12T23:05:00+09:00','arrival':'2026-09-12T23:25:00+09:00',
                 'fromX':126.9244,'fromY':37.5572,'toX':126.9817,'toY':37.4765},
                {'type':'WALKING','durationSeconds':120},
                {'type':'WAITING','durationSeconds':480},
                {'type':'SUBWAY','title':'4호선','vehicles':['4호선'],'stops':['사당','서울역'],
                 'departure':'2026-09-12T23:35:00+09:00','arrival':'2026-09-12T23:55:00+09:00',
                 'fromX':126.9817,'fromY':37.4765,'toX':126.9726,'toY':37.5547}]}


class StressTests(unittest.TestCase):
    def test_wait_absorbed_and_transfer_walk_included(self):
        report=stress.evaluate(route())
        self.assertEqual([p['slackSeconds'] for p in report['checkpoints']],[600,180])
        self.assertEqual([s['status'] for s in report['scenarios']],['pass','tight','missed'])
        self.assertEqual(report['toleranceSeconds'],180)
        self.assertEqual(report['scenarios'][2]['firstFailure']['station'],'사당')

    def test_delay_is_injected_only_at_selected_point(self):
        self.assertEqual([s['status'] for s in stress.evaluate(route(),0)['scenarios']],['pass','pass','pass'])
        self.assertEqual(stress.evaluate(route(),1)['scenarios'][2]['status'],'missed')

    def test_exact_margin_and_departure_boundary(self):
        points=stress.checkpoints(route())
        points[1]['slackSeconds']=300
        points[1]['boardingGapSeconds']=600
        self.assertEqual(stress.scenario(points,5,1)['status'],'pass')
        self.assertEqual(stress.scenario(points,10,1)['status'],'tight')
        points[1]['boardingGapSeconds']=599
        self.assertEqual(stress.scenario(points,10,1)['status'],'missed')

    def test_unverified_never_scores(self):
        data=route(); data['scheduleStatus']='estimated'
        self.assertEqual(stress.evaluate(data)['status'],'unknown')
        self.assertTrue(all(s['status']=='unknown' for s in stress.evaluate(data)['scenarios']))
        del data['steps'][-1]['arrival']
        data['scheduleStatus']='complete'
        self.assertIsNone(stress.evaluate(data)['toleranceSeconds'])

    def test_midnight(self):
        data=route(); data['steps'][-1]['departure']='2026-09-13T00:05:00+09:00'; data['steps'][-1]['arrival']='2026-09-13T00:25:00+09:00'
        self.assertEqual(stress.evaluate(data)['scenarios'][2]['status'],'pass')

    def test_invalid_targets_and_delays(self):
        for value in (-1,True,None,'0',99):
            with self.subTest(value=value),self.assertRaises(rail.SeoulError): stress.evaluate(route(),value)
        for value in (True,3,'5',None):
            with self.subTest(value=value),self.assertRaises(rail.SeoulError): stress.scenario(stress.checkpoints(route()),value,0)

    def test_recommendation_excludes_unknown_and_baseline_tight(self):
        a=route(); b=copy.deepcopy(a); b['id']='better'; b['steps'][-1]['departure']='2026-09-12T23:45:00+09:00'
        unknown=copy.deepcopy(a); unknown['id']='unknown'; unknown['scheduleStatus']='estimated'
        self.assertEqual(stress.compare([a,b,unknown])['recommendedRouteId'],'better')
        self.assertEqual(stress.compare([a,b,unknown])['evaluatedCount'],2)

    def test_recovery_uses_delayed_time_and_does_not_claim_no_route(self):
        with patch.object(rail,'routes',side_effect=rail.SeoulError('없음')) as fetch:
            result=stress.recover(route(),1,10,'test')
        self.assertEqual(fetch.call_args.args[0],'사당')
        self.assertEqual(fetch.call_args.args[2].isoformat(),'2026-09-12T23:37:00+09:00')
        self.assertEqual(result['status'],'unknown')
        self.assertIn('확정이 아닙니다',result['notice'])

    def test_no_mutation(self):
        data=route(); original=copy.deepcopy(data); stress.evaluate(data)
        self.assertEqual(data,original)


class WalkingTests(unittest.TestCase):
    def setUp(self):
        self.origin=app.DEMO_PLACES[0]; self.destination=app.DEMO_PLACES[1]
        self.departure=app.parse_departure('2026-09-12T23:50')
        self.payload={'status':'OK','route':{'properties':{'totalTime':1800,'totalDistance':1500,'landingUrl':'javascript:bad'},
            'legs':[{'steps':[{'properties':{'time':1800,'distance':1500,'guidance':'횡단보도 건너기'}}]}]}}

    def test_real_duration_midnight_and_safe_link(self):
        value=walking.normalize(self.payload,self.origin,self.destination,self.departure)
        self.assertEqual(value['arrival'],'2026-09-13T00:20:00+09:00')
        self.assertEqual(value['distanceMeters'],1500)
        self.assertTrue(value['url'].startswith('https://map.kakao.com/link/by/walk/'))
        self.assertNotIn('javascript',value['url'])

    def test_no_straight_line_fallback(self):
        for status in ('SAME_POINT','TOO_FAR_AWAY','ROUTE_RESULT_NOT_FOUND'):
            with self.subTest(status=status),self.assertRaises(rail.SeoulError):
                walking.normalize({'status':status},self.origin,self.destination,self.departure)

    def test_invalid_payload(self):
        for number in (None,True,'NaN',-5):
            data=copy.deepcopy(self.payload); data['route']['properties']['totalTime']=number
            with self.subTest(number=number),self.assertRaises(rail.SeoulError):walking.normalize(data,self.origin,self.destination,self.departure)

    def test_points_are_from_the_route(self):
        self.assertEqual(walking.point(route(),self.origin,'boarding',1)['name'],'사당')
        self.assertEqual(walking.point(route(),self.origin,'alighting',1)['name'],'서울역')
        for index in (-1,True,6):
            with self.assertRaises(rail.SeoulError):walking.point(route(),self.origin,'boarding',index)
        data=route(); del data['steps'][-1]['toX']
        with self.assertRaises(rail.SeoulError):walking.point(data,self.origin,'alighting',1)

    def test_missing_key(self):
        with self.assertRaises(app.AppError) as exc:app.calculate_walk({},app.Config())
        self.assertEqual(exc.exception.status,503)

    def test_stale_snapshot_is_not_used(self):
        body={'journeyMode':'auto','origin':self.origin,'destination':self.destination,'departure':'2026-09-12T23:00','routeToken':'madeup','routeId':'test'}
        for fn in (app.calculate_stress,app.calculate_recovery,app.calculate_walk):
            with self.subTest(function=fn.__name__),self.assertRaises(app.AppError) as exc:fn(body,app.Config(rest_key='test'))
            self.assertEqual(exc.exception.status,409)


if __name__ == '__main__':unittest.main()
