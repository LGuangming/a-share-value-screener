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
        return self.body
    def __exit__(self, *args):
        pass


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

    def test_provider_permission_error_does_not_silently_fallback(self):
        payload={"code":40203,"msg":"permission denied","data":None}
        with patch("value_screener.tushare_client.urlopen",return_value=FakeHTTP(payload)) as fetch:
            with self.assertRaises(TushareError):
                TushareClient("relay-test",api_source="relay",
                              allow_insecure_http=True).query("trade_cal")
        self.assertEqual(fetch.call_count,1)
