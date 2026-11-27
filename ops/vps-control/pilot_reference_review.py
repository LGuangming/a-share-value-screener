"""Pilot reference review: exact scheduled sessions and independent close samples.
Read-only, no network or credentials. A local administrator must approve export.
"""
from datetime import date,timedelta
from pathlib import Path
import json
import math

START=date(2024,7,1)
END=date(2025,1,31)
CLOSURES=((date(2024,9,15),date(2024,9,17)),
          (date(2024,10,1),date(2024,10,7)),
          (date(2025,1,1),date(2025,1,1)),
          (date(2025,1,28),date(2025,2,4)))
CALENDAR_SOURCES=(
    "https://www.sse.com.cn/disclosure/announcement/general/c/c_20231226_5733939.shtml",
    "https://www.szse.cn/disclosure/notice/t20231226_605108.html",
    "https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml",
    "https://www.szse.cn/disclosure/notice/t20241223_611283.html",
)
# Public closing prices rounded to 2 decimals. Secondary source for 500/1000;
# cross-check samples, not comprehensive independent daily price verification.
SAMPLES=(
    ("000300.SH","2024-12-30",3999.05,"https://www.nbd.com.cn/articles/2024-12-30/3704199.html"),
    ("000300.SH","2024-12-31",3934.91,"https://www.nbd.com.cn/articles/2024-12-31/3706155.html"),
    ("000905.SH","2024-12-30",5898.88,"https://www.lujiaocj.com/whrm/411159.html"),
    ("000852.SH","2024-12-30",6145.93,"https://www.lujiaocj.com/whrm/411159.html"),
)
def expected_sessions():
    return [(START+timedelta(days=i)).isoformat() for i in range((END-START).days+1)
            if (d:=START+timedelta(days=i)).weekday()<5 and not any(lo<=d<=hi for lo,hi in CLOSURES)]

def check(candidate):
    if candidate.get("status")!="BT05_REFERENCE_CANDIDATE_NOT_REVIEWED":
        raise ValueError("Unreviewed reference candidate required")
    if not candidate["from"]<=START.isoformat()<=END.isoformat()<=candidate["through"]:
        raise ValueError("Reference period incomplete")
    expected=expected_sessions()
    sessions=[d for d in candidate["sessions"] if START.isoformat()<=d<=END.isoformat()]
    if sessions!=expected:raise ValueError("Pilot session mismatch")
    indexes={}
    for code in ("000300.SH","000905.SH","000852.SH"):
        rows=[row for row in candidate["indexes"][code] if START.isoformat()<=row["date"]<=END.isoformat()]
        if [row["date"] for row in rows]!=expected:raise ValueError("Pilot benchmark date mismatch")
        if any(type(row["close"]) not in (int,float) or not math.isfinite(row["close"]) or row["close"]<=0 for row in rows):
            raise ValueError("Invalid benchmark close")
        indexes[code]=rows
    for code,day,close,source in SAMPLES:
        observed=next(row["close"] for row in indexes[code] if row["date"]==day)
        if abs(observed-close)>0.0051:raise ValueError("Independent benchmark sample mismatch")
    return {"status":candidate["status"],"from":START.isoformat(),"through":END.isoformat(),
            "sessions":sessions,"indexes":indexes,"inputs":candidate["inputs"],
            "limitations":["pilot_only","exchange_scheduled_sessions_not_all_emergency_closures",
              "four_independent_price_samples_only","price_index_not_total_return",
              "operator_review_required","not_certified_historical_coverage"]}

def main():
    import argparse
    import sys
    import os
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend",required=True,type=Path)
    p.add_argument("--archive",required=True,type=Path)
    p.add_argument("--candidate",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    if os.geteuid()==0:p.error("Run as collection account")
    sys.path.insert(0,str(a.backend))
    from value_screener import bt05_reference_capture as ref
    before=ref.sha256(a.candidate)
    candidate=json.loads(a.candidate.read_text())
    # Revalidate all raw receipts against the immutable full candidate.
    if ref.build(a.archive,date(2020,1,1),date(2025,12,31),ref.INDEXES)!=candidate or ref.sha256(a.candidate)!=before:
        raise ValueError("Reference candidate changed or differs from receipts")
    pilot=check(candidate)
    pilot["full_candidate_sha256"]=before
    pilot["calendar_sources"]=list(CALENDAR_SOURCES)
    pilot["index_sample_sources"]=[{"code":c,"date":d,"close":v,"source":s} for c,d,v,s in SAMPLES]
    if a.output.exists():
        if json.loads(a.output.read_text())!=pilot:raise ValueError("Pilot candidate differs")
    else:ref.write_new(a.output,pilot)
    print(json.dumps({"pilot_sessions":len(pilot["sessions"]),"benchmark_samples":len(SAMPLES),
                      "candidate_sha256":ref.sha256(a.output),"formal_backtest_ready":False}))
if __name__=="__main__":main()
