import io
import json
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
