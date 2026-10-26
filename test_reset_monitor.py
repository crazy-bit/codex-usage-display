import tempfile, unittest, time, datetime, json
from pathlib import Path
from unittest.mock import Mock, patch
from reset_monitor import ResetMonitor, ResetClient, ResetAPIError

def record(identifier, state='announced', group='reset', age=10):
    return dict(id=identifier, group=group, announcement_state=state, announced_at=datetime.datetime.fromtimestamp(time.time()-age,datetime.timezone.utc).isoformat())

class ResetTests(unittest.TestCase):
    def test_baseline_confirmation_dedup_restart_ack(self):
        with tempfile.TemporaryDirectory() as directory, patch('reset_monitor.data_dir', return_value=Path(directory)):
            client=Mock(); client.timeline.return_value={'events':[record('old'), record('pending','none')]}
            monitor=ResetMonitor(client); monitor.poll(); self.assertIsNone(monitor.pending())
            visible=json.loads((Path(directory)/'reset-status.json').read_text(encoding='utf-8'))['events']
            self.assertEqual(len(visible),2)
            self.assertFalse(visible[1]['confirmed'])
            self.assertNotIn('pending',monitor.state['seen'])
            client.timeline.return_value={'events':[record('old'),record('pending'),record('banked',group='credits'),record('expired',age=90000)]}
            monitor.poll(); monitor.poll(); self.assertEqual(len(monitor.state['pending']),1)
            monitor=ResetMonitor(client); self.assertEqual(monitor.pending()['id'],'pending')
            monitor.acknowledge('pending'); monitor.poll(); self.assertIsNone(monitor.pending())
    def test_failed_fetch_keeps_baseline(self):
        with tempfile.TemporaryDirectory() as directory, patch('reset_monitor.data_dir', return_value=Path(directory)):
            client=Mock();client.timeline.side_effect=ResetAPIError('offline')
            monitor=ResetMonitor(client)
            with self.assertRaises(ResetAPIError): monitor.poll()
            self.assertFalse(monitor.state['initialized'])
    def test_invalid_response(self):
        with patch('reset_monitor.urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value=b'{}'
            with self.assertRaises(ResetAPIError): ResetClient().timeline()


class ForecastTests(unittest.TestCase):
    def test_valid_probability_and_stale_data(self):
        client = ResetClient()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with patch.object(client, 'get', return_value={'probabilities':{'rounded_48h':39},'updated_at':now}):
            self.assertEqual(client.forecast()['p'],39)
        for value in (True, -1, 101, None, float('nan')):
            with patch.object(client,'get', return_value={'probabilities':{'rounded_48h':value},'updated_at':now}):
                with self.assertRaises(ResetAPIError): client.forecast()
        with patch.object(client,'get', return_value={'probabilities':{'rounded_48h':39},'updated_at':'2020-01-01T00:00:00Z'}):
            with self.assertRaises(ResetAPIError): client.forecast()

    def test_wire_omits_stale_forecast(self):
        from ble_protocol import add_forecast
        message={}
        add_forecast(message, {'p':39,'t':1000}, now=1100)
        self.assertEqual(message['f']['p'],39)
        self.assertEqual(message['app'],'Codex')
        for probability in (None, {'p':101,'t':1000}, {'p':39,'t':1000}, {'p':39,'t':4000}):
            add_forecast(message,probability,now=3000)
            self.assertIsNone(message['f'])


class NextResetTests(unittest.TestCase):
    def test_future_explicit_window_only(self):
        client=ResetClient()
        now=datetime.datetime.now(datetime.timezone.utc)
        future=(now+datetime.timedelta(hours=6)).isoformat()
        result={'probabilities':{'rounded_48h':39},'updated_at':now.isoformat(),
                'last_reset_at':'2026-10-20T06:33:48Z', 'time_window':{'start_hour':3},
                'official_signal':{'window':{'start_at':future}}}
        with patch.object(client,'get',return_value=result):
            self.assertEqual(client.forecast()['nr'],int(datetime.datetime.fromisoformat(future).timestamp()))
        for window in (None, 'today', '2020-01-01T00:00:00Z'):
            result['official_signal']['window']=window
            with patch.object(client,'get',return_value=result):
                self.assertIsNone(client.forecast()['nr'])


class TiboSignalTests(unittest.TestCase):
    def test_homepage_signal_overrides_cadence(self):
        client=ResetClient()
        data={'updated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'probabilities':{'rounded_48h':39,'signal_percent':93},
            'official_signal':{'url':'https://x.com/thsottiaux/status/123'}}
        with patch.object(client,'get',return_value=data):
            result=client.forecast(); self.assertEqual(result['p'],93);self.assertEqual(result['kind'],'signal')
        data['official_signal']=None
        with patch.object(client,'get',return_value=data):self.assertEqual(client.forecast()['p'],39)

    def test_preview_notifies_once_then_confirmation_separately(self):
        with tempfile.TemporaryDirectory() as directory, patch('reset_monitor.data_dir',return_value=Path(directory)):
            client=Mock(); preview=record('123','none');preview['url']='https://x.com/thsottiaux/status/123'
            preview['summary']='Reset tomorrow'
            client.timeline.return_value={'events':[preview]}
            monitor=ResetMonitor(client);monitor.poll();monitor.poll()
            self.assertEqual(len(monitor.state['pending']),1)
            self.assertEqual(monitor.pending()['kind'],'preview')
            monitor.acknowledge('preview:123');monitor.poll();self.assertIsNone(monitor.pending())
            preview['summary']='Done and all propagated.'
            monitor.poll();self.assertEqual(monitor.pending()['kind'],'confirmed')
            monitor.acknowledge('123');monitor.poll();self.assertIsNone(monitor.pending())

    def test_other_accounts_are_not_trusted(self):
        from reset_monitor import tibo_confirmed,trusted_tibo_url
        self.assertFalse(trusted_tibo_url('https://x.com/other/status/123'))
        self.assertFalse(tibo_confirmed({'url':'https://x.com/thsottiaux/status/123','summary':'Not done yet'}))
