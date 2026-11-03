"""Withdraw an accidental notification without deleting its audit or booked calendar event."""
import unittest
from tests import test_review_workflow as base
from agent.server.review_workflow import retract_notification,history

class NotificationRetractionTests(unittest.TestCase):
 setUp=base.ReviewWorkflowTests.setUp
 login=base.ReviewWorkflowTests.login
 post=base.ReviewWorkflowTests.post
 data=base.ReviewWorkflowTests.data
 issue=base.ReviewWorkflowTests.issue
 def test_only_target_message_and_action_are_withdrawn(self):
  self.login()
  response=self.post('walking','appointment_request',recipients=['patient'],timeframe='q',message='q',confirmed=True);self.assertEqual(response.status_code,200);target=response.json()['event']
  self.post('critical-sodium','appointment_request',recipients=['patient'],timeframe='This week',message='Please arrange a discussion.',confirmed=True)
  slot=self.client.get('/api/patient/appointments/availability').json()['slots'][0]['id']
  response=self.client.post('/api/patient/appointments/book',json={'request':target,'slot':slot,'recipient':'patient','confirmed':True,'request_id':'existing-booking'});self.assertEqual(response.status_code,200,response.text)
  booking=response.json()['booking']['id'];before=self.data()['tab_counts']['action_required']
  result=retract_notification(self.app.state.record,target,'Accidental demo notification removed at the user’s request.')
  self.assertFalse(result['duplicate'])
  feed=self.client.get('/api/shared-care').json()['messages'];self.assertNotIn(target,{m['id'] for m in feed});self.assertEqual(len(feed),1)
  self.assertFalse(self.issue('walking')['workflow']['actions']);self.assertEqual(self.data()['tab_counts']['action_required'],before-1)
  self.assertIn(booking,{e['id'] for e in self.client.get('/api/calendar').json()['events']})
  audit=history(self.app.state.record.snapshot().events);self.assertTrue(any(a['id']==target and a['message']=='q' for a in audit));self.assertTrue(any(a.get('target')==target and a['kind']=='notification_retracted' for a in audit))
  self.assertTrue(retract_notification(self.app.state.record,target,'Repeated cleanup')['duplicate'])
