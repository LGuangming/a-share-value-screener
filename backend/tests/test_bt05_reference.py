import json,unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from value_screener import bt05_reference_capture as ref
from value_screener.technical_research import sha256
from value_screener.bt05_walk_forward import benchmark

START=date(2025,1,1);END=date(2025,1,3)

class Client:
    source="official"
    def __init__(self):self.calls=[];self.bad=False
    def query(self,api,params,fields,retries):
        self.calls.append((api,params,retries))
        if api=="trade_cal":
            exchange=params["exchange"]
            return [{"exchange":exchange,"cal_date":d,"is_open":int(d!="20250101"),"pretrade_date":"20241231"}
                    for d in ("20250101","20250102","20250103")]
        return [{"ts_code":params["ts_code"],"trade_date":d,"close":100+i,"pre_close":99+i}
                for i,d in enumerate(("20250102","20250103")) if not self.bad or i==0]

class ReferenceTests(unittest.TestCase):
    def test_capture_resume_build_review(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/"raw";raw.mkdir();out=root/"out";out.mkdir()
            client=Client();codes=["000300.SH"]
            self.assertFalse(ref.capture(raw,START,END,codes,client,1)["complete"])
            self.assertTrue(ref.capture(raw,START,END,codes,client,2)["complete"])
            self.assertEqual(len(client.calls),3)
            self.assertEqual(ref.capture(raw,START,END,codes,client,1)["new_requests"],0)
            doc=ref.build(raw,START,END,codes)
            self.assertEqual(doc["sessions"],["2025-01-02","2025-01-03"])
            candidate=root/"candidate.json";candidate.write_text(json.dumps(doc))
            rev=root/"review.json";rev.write_text(json.dumps({"status":"OPERATOR_REVIEWED_REFERENCE_NOT_CERTIFIED",
                "candidate_sha256":sha256(candidate),"reviewer":"fixture","independent_calendar_source":"fixture",
                "index_source_check":"fixture"}))
            ref.review(candidate,rev,out)
            levels,_=benchmark(out/"benchmark-000300.SH.json",[date(2025,1,2),END])
            self.assertEqual(levels[END],101.)
            marker=next(raw.glob("index*.manifest.json"))
            m=json.loads(marker.read_text());(raw/m["file"]).write_text("[]")
            with self.assertRaisesRegex(ValueError,"receipt mismatch"):ref.build(raw,START,END,codes)

    def test_missing_index_session_and_calendar_duplicates_block(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);client=Client();client.bad=True
            ref.capture(root,START,END,["000300.SH"],client,3)
            with self.assertRaisesRegex(ValueError,"calendar mismatch"):ref.build(root,START,END,["000300.SH"])
        row={"exchange":"SSE","cal_date":"20250101","is_open":0,"pretrade_date":"20241231"}
        with self.assertRaisesRegex(ValueError,"Duplicate"):ref.validate("calendar","SSE",START,END,[row,row])

if __name__=="__main__":unittest.main()
