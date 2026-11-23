import io
import json
import os
import unittest
from unittest.mock import patch
from value_screener.tushare_client import TushareClient, TushareError


class FakeHTTP:
    def __init__(self, body):
        self.body = io.BytesIO(json.dumps(body).encode())
    def __enter__(self):
        # Each mocked HTTP request must start with fresh response bytes.
        self.body.seek(0)
        return self.body
    def __exit__(self, *args):
        pass


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class TestProvider(unittest.TestCase):
    def test_maps_fields_to_dicts(self):
        payload = {"code":0,"msg":"","data":{"fields":["ts_code","total_mv"],"items":[["000001.SZ",12345.6]]}}
        with patch("value_screener.tushare_client.urlopen",return_value=FakeHTTP(payload)) as fetch:
            rows = TushareClient("dummy-token").query("daily_basic",{"trade_date":"20261120"})
        self.assertEqual(rows[0]["total_mv"],12345.6)
        request = fetch.call_args.args[0]
        self.assertEqual(json.loads(request.data)["api_name"],"daily_basic")

    def test_provider_error_is_visible(self):
        payload = {"code":-2001,"msg":"permission denied","data":None}
        with patch("value_screener.tushare_client.urlopen",return_value=FakeHTTP(payload)):
            with self.assertRaises(TushareError):
                TushareClient("dummy-token").query("balancesheet")

    def test_missing_token(self):
        with self.assertRaises(ValueError):
            TushareClient("")

    def test_default_api_remains_official_https(self):
        payload = {"code":0,"data":{"fields":["cal_date","is_open"],"items":[["20261120",1]]}}
        with patch("value_screener.tushare_client.urlopen", return_value=FakeHTTP(payload)) as fetch:
            rows = TushareClient("official-test").query("trade_cal",
                {"exchange":"SSE","start_date":"20261120","end_date":"20261120"},"cal_date,is_open")
        request = fetch.call_args.args[0]
        self.assertEqual(request.full_url,"https://api.tushare.pro")
        self.assertEqual(request.get_method(),"POST")
        self.assertEqual(rows,[{"cal_date":"20261120","is_open":1}])

    def test_relay_post_same_tushare_json_contract(self):
        payload = {"code":0,"data":{"fields":["ts_code","total_mv"],"items":[["000001.SZ",123.4]]}}
        with patch("value_screener.tushare_client.urlopen",return_value=FakeHTTP(payload)) as fetch:
            api = TushareClient("relay-test", api_source="relay",
                               relay_url="http://tus.sxdata.top/tu_share/",
                               allow_insecure_http=True)
            result=api.query("daily_basic",{"trade_date":"20261120"},"ts_code,total_mv")
        request=fetch.call_args.args[0]
        data=json.loads(request.data)
        self.assertEqual(request.full_url,"http://tus.sxdata.top/tu_share/")
        self.assertEqual(request.get_method(),"POST")
        self.assertEqual(data,{"api_name":"daily_basic","token":"relay-test",
                               "params":{"trade_date":"20261120"},"fields":"ts_code,total_mv"})
        self.assertEqual(result[0]["total_mv"],123.4)

    def test_plain_http_requires_explicit_opt_in(self):
        with self.assertRaisesRegex(ValueError,"unencrypted"):
            TushareClient("token", api_source="relay")

    def test_relay_cannot_inherit_official_token(self):
        env={"TUSHARE_API_SOURCE":"relay","TUSHARE_TOKEN":"official-only",
             "TUSHARE_ALLOW_HTTP":"1"}
        with patch.dict(os.environ,env,clear=True):
            with self.assertRaisesRegex(ValueError,"TUSHARE_RELAY_TOKEN"):
                TushareClient.from_environment()

    def test_environment_sources_are_independent(self):
        env={"TUSHARE_API_SOURCE":"official","TUSHARE_TOKEN":"official-test",
             "TUSHARE_RELAY_TOKEN":"relay-test","TUSHARE_ALLOW_HTTP":"1"}
        with patch.dict(os.environ,env,clear=True):
            official=TushareClient.from_environment()
            self.assertEqual(official.source,"official")
            self.assertEqual(official.api_url,"https://api.tushare.pro")
            self.assertEqual(official._token,"official-test")
        env["TUSHARE_API_SOURCE"]="relay"
        with patch.dict(os.environ,env,clear=True):
            relay=TushareClient.from_environment()
            self.assertEqual(relay.source,"relay")
            self.assertEqual(relay._token,"relay-test")
            self.assertEqual(relay.api_url,"http://tus.sxdata.top/tu_share/")

    def test_https_relay_does_not_require_insecure_opt_in(self):
        relay=TushareClient("relay-test",api_source="relay",
                            relay_url="https://relay.example.com/tu_share/")
        self.assertEqual(relay.api_url,"https://relay.example.com/tu_share/")

    def test_rejects_unsafe_relay_urls(self):
        for url in ("file:///etc/passwd","http://user:pw@example.com/api",
                    "http://example.com/?token=secret","http://example.com/#secret"):
            with self.subTest(url=url),self.assertRaises(ValueError):
                TushareClient("token",api_source="relay",relay_url=url,
                              allow_insecure_http=True)

    def test_relay_429_then_success_waits_60_seconds(self):
        limited = {"code":429,"msg":"您请求速度过快","data":None}
        allowed = {"code":0,"data":{"fields":["cal_date","is_open"],"items":[["20261123",1]]}}
        clock = FakeClock()
        with patch("value_screener.tushare_client.urlopen",
                   side_effect=[FakeHTTP(limited), FakeHTTP(allowed)]) as fetch, \
             patch("value_screener.tushare_client.time.sleep",side_effect=clock.sleep), \
             patch("value_screener.tushare_client.time.monotonic",side_effect=clock.monotonic):
            client=TushareClient("relay-key",api_source="relay",
                                 allow_insecure_http=True,relay_min_interval_seconds=5)
            rows=client.query("trade_cal",retries=3)
        self.assertEqual(rows,[{"cal_date":"20261123","is_open":1}])
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(clock.sleeps,[60])

    def test_relay_429_exhaustion_is_bounded_to_three_attempts(self):
        limited = {"code":429,"msg":"您请求速度过快","data":None}
        clock = FakeClock()
        with patch("value_screener.tushare_client.urlopen",
                   side_effect=[FakeHTTP(limited)]*3) as fetch, \
             patch("value_screener.tushare_client.time.sleep",side_effect=clock.sleep), \
             patch("value_screener.tushare_client.time.monotonic",side_effect=clock.monotonic):
            client=TushareClient("relay-key",api_source="relay",
                                 allow_insecure_http=True)
            with self.assertRaisesRegex(TushareError,"rate-limited"):
                client.query("daily_basic",retries=3)
        self.assertEqual(fetch.call_count,3)
        self.assertEqual(clock.sleeps,[60,60])

    def test_relay_pacing_between_separate_requests(self):
        allowed={"code":0,"data":{"fields":[],"items":[]}}
        clock=FakeClock()
        with patch("value_screener.tushare_client.urlopen",
                   side_effect=[FakeHTTP(allowed),FakeHTTP(allowed)]), \
             patch("value_screener.tushare_client.time.sleep",side_effect=clock.sleep), \
             patch("value_screener.tushare_client.time.monotonic",side_effect=clock.monotonic):
            client=TushareClient("relay-key",api_source="relay",
                                 allow_insecure_http=True,relay_min_interval_seconds=5)
            client.query("trade_cal")
            client.query("stock_basic")
        self.assertEqual(clock.sleeps,[5])

    def test_official_429_retries_same_api_after_60_seconds(self):
        limited={"code":429,"msg":"rate limited","data":None}
        allowed={"code":0,"data":{"fields":[],"items":[]}}
        clock=FakeClock()
        with patch("value_screener.tushare_client.urlopen",
                   side_effect=[FakeHTTP(limited),FakeHTTP(allowed)]) as fetch, \
             patch("value_screener.tushare_client.time.sleep",side_effect=clock.sleep), \
             patch("value_screener.tushare_client.time.monotonic",side_effect=clock.monotonic):
            result=TushareClient("official-key").query("trade_cal")
        self.assertEqual(result,[])
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(clock.sleeps,[60])
        self.assertEqual(fetch.call_args.args[0].full_url,"https://api.tushare.pro")

    def test_official_paces_even_with_successful_requests(self):
        allowed={"code":0,"data":{"fields":[],"items":[]}}
        clock=FakeClock()
        requests=[]
        def fake_urlopen(*_args,**_kwargs):
            requests.append(clock.monotonic())
            return FakeHTTP(allowed)
        with patch("value_screener.tushare_client.urlopen",side_effect=fake_urlopen), \
             patch("value_screener.tushare_client.time.sleep",side_effect=clock.sleep), \
             patch("value_screener.tushare_client.time.monotonic",side_effect=clock.monotonic):
            client=TushareClient("official-key")
            for _ in range(103):
                client.query("trade_cal")
        self.assertEqual(len(requests),103)
        for earlier,later in zip(requests,requests[1:]):
            self.assertGreaterEqual(later-earlier,0.61-1e-9)
        for t in requests:
            self.assertLessEqual(sum((t-60)<q<=t for q in requests),100)

    def test_strict_rolling_window_caps_calls_even_with_existing_burst(self):
        from collections import deque
        allowed={"code":0,"data":{"fields":[],"items":[]}}
        clock=FakeClock()
        with patch("value_screener.tushare_client.urlopen",return_value=FakeHTTP(allowed)), \
             patch("value_screener.tushare_client.time.sleep",side_effect=clock.sleep), \
             patch("value_screener.tushare_client.time.monotonic",side_effect=clock.monotonic):
            client=TushareClient("official-key")
            client._request_starts=deque([0.0]*100)
            client.query("daily_basic")
        self.assertEqual(clock.sleeps,[60])

    def test_transient_network_error_waits_60_seconds(self):
        from urllib.error import URLError
        allowed={"code":0,"data":{"fields":[],"items":[]}}
        clock=FakeClock()
        with patch("value_screener.tushare_client.urlopen",
                   side_effect=[URLError("temporarily unavailable"),FakeHTTP(allowed)]) as fetch, \
             patch("value_screener.tushare_client.time.sleep",side_effect=clock.sleep), \
             patch("value_screener.tushare_client.time.monotonic",side_effect=clock.monotonic):
            result=TushareClient("official-key").query("daily_basic")
        self.assertEqual(result,[])
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(clock.sleeps,[60])

    def test_permission_denial_never_sleeps_or_retries(self):
        payload={"code":40203,"msg":"no access","data":None}
        with patch("value_screener.tushare_client.urlopen",
                   return_value=FakeHTTP(payload)) as fetch, \
             patch("value_screener.tushare_client.time.sleep") as sleep:
            with self.assertRaisesRegex(TushareError,"40203"):
                TushareClient("official-key").query("trade_cal")
        self.assertEqual(fetch.call_count,1)
        sleep.assert_not_called()

    def test_relay_interval_validation(self):
        with self.assertRaises(ValueError):
            TushareClient("token",api_source="relay",
                          allow_insecure_http=True,relay_min_interval_seconds=0.1)

    def test_provider_permission_error_does_not_silently_fallback(self):
        payload={"code":40203,"msg":"permission denied","data":None}
        with patch("value_screener.tushare_client.urlopen",return_value=FakeHTTP(payload)) as fetch:
            with self.assertRaises(TushareError):
                TushareClient("relay-test",api_source="relay",
                              allow_insecure_http=True).query("trade_cal")
        self.assertEqual(fetch.call_count,1)
