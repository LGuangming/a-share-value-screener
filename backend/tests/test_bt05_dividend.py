import unittest
import json
from datetime import date,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stdout
from io import StringIO
from value_screener import bt05_dividend_capture as cap,bt05_dividend_reconcile as rec
from value_screener.bt05_actions import Actions
from value_screener.technical_research import sha256

DAY=date(2025,6,10)
PREV=DAY-timedelta(days=1)
CODE="000001.SZ"

def dividend(**kw):
    row=dict.fromkeys(cap.FIELDS.split(","),None)
    row.update(ts_code=CODE,end_date="20241231",ann_date="20250331",div_proc="实施",
        stk_div=0.,stk_bo_rate=0.,stk_co_rate=0.,cash_div=.4,cash_div_tax=.5,
        record_date="20250609",ex_date="20250610",pay_date="20250612",imp_ann_date="20250605")
    row.update(kw)
    return row

class Client:
    source="official"
    def __init__(self,rows):self.rows=rows;self.calls=[]
    def query(self,api,params,fields,retries):
        self.calls.append((api,params,fields,retries))
        return self.rows.get(params["ex_date"],[])

class DividendTests(unittest.TestCase):
    def test_bounded_capture_resume_empty_and_corruption(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            days=[DAY,DAY+timedelta(days=1)]
            client=Client({"20250610":[dividend()]})
            with redirect_stdout(StringIO()):
                a=cap.collect(root,days,client,1)
                b=cap.collect(root,days,client,1)
            self.assertEqual(a["verified_sessions"],1)
            self.assertEqual(b["verified_sessions"],2)
            self.assertEqual(b["empty_responses"],1)
            self.assertEqual(len(client.calls),2)
            self.assertEqual(client.calls[0],("dividend",{"ex_date":"20250610"},cap.FIELDS,1))
            self.assertFalse(cap.audit(root,days)["missing_sessions"])
            p=next(cap.folder(root,DAY).glob("dividend-*.json"))
            p.write_text("[]")
            with self.assertRaisesRegex(ValueError,"hash/size"):cap.audit(root,days)

    def test_cap_and_wrong_date_are_not_published(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            for rows in ([dividend()]*cap.CAP,[dividend(ex_date="20250611")]):
                with self.assertRaises(ValueError):
                    cap.collect_day(root,DAY,Client({"20250610":rows}))
                self.assertIsNone(cap.receipt(root,DAY))

    def test_reconciliation_cash_bonus_conflicts_and_unknown(self):
        prior=(PREV,1.,10.)
        current=(DAY,10/9.5,9.5)
        item=rec.candidate(CODE,DAY,prior,current,[dividend()],.2,"fixture")
        self.assertEqual(item["kind"],"cash_only")
        self.assertAlmostEqual(item["cash_per_old_share_net"],.4)
        bonus=dividend(stk_div=1.,stk_bo_rate=1.,div_listdate="20250610")
        self.assertEqual(rec.candidate(CODE,DAY,prior,(DAY,20/9.5,4.75),[bonus],.2,"fixture")["kind"],"cash_bonus_same_day")
        for rows,now in (([],current),([dividend(),dividend(cash_div_tax=.7)],current),
                         ([dividend()],(DAY,2.,9.5)),([dict(bonus,div_listdate="20250611")],(DAY,20/9.5,4.75))):
            self.assertEqual(rec.candidate(CODE,DAY,prior,now,rows,.2,"fixture")["kind"],"blocked")
        self.assertEqual(rec.candidate(CODE,DAY,None,current,[dividend()],.2,"fixture")["kind"],"blocked")

    def test_review_is_explicit_and_bound_to_candidate_hash(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            calendar=root/"calendar.json";calendar.write_text(json.dumps({"from":PREV.isoformat(),"to":DAY.isoformat(),"sessions":[PREV.isoformat(),DAY.isoformat()]}))
            item=rec.candidate(CODE,DAY,(PREV,1.,10.),(DAY,10/9.5,9.5),[dividend()],.2,"fixture")
            item["id"]=cap.identity(item)
            blocked=rec.candidate("000002.SZ",DAY,(PREV,1.,10.),(DAY,2.,5.),[],.2,"fixture")
            blocked["id"]=cap.identity(blocked)
            c=root/"candidates.json";c.write_text(json.dumps({"status":rec.STATUS,"from":DAY.isoformat(),"through":DAY.isoformat(),"calendar_sha256":sha256(calendar),"withholding_assumption":.2,"events":[item,blocked]}))
            v=root/"review.json";v.write_text(json.dumps({"status":"OPERATOR_REVIEWED_RESEARCH_NOT_CERTIFIED","candidate_sha256":sha256(c),"reviewer":"test","accepted_ids":[item["id"]]}))
            result=rec.review(c,v,calendar)
            self.assertEqual((result["accepted_count"],result["blocked_count"]),(1,1))
            ledger=root/"ledger.json";ledger.write_text(json.dumps(result))
            actions,_=Actions.load(ledger,[PREV,DAY],DAY,DAY)
            self.assertIn(CODE,actions.by_ex[DAY])
            self.assertIn("000002.SZ",actions.blocked[DAY])
            v.write_text(json.dumps({"status":"OPERATOR_REVIEWED_RESEARCH_NOT_CERTIFIED","candidate_sha256":sha256(c),"reviewer":"test","accepted_ids":[blocked["id"]]}))
            with self.assertRaisesRegex(ValueError,"Blocked event"):rec.review(c,v,calendar)

if __name__=="__main__":unittest.main()
