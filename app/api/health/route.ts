import { NextResponse } from "next/server";
import { latestSnapshot } from "@/lib/data";

export const dynamic = "force-dynamic";
export async function GET() {
  const {snapshot,error} = await latestSnapshot();
  const daysOld = snapshot?.trade_date ?
    Math.floor((Date.now() - new Date(`${snapshot.trade_date}T00:00:00+08:00`).getTime()) / 86400000) : null;
  const healthy = !!snapshot && daysOld !== null && daysOld <= 5;
  return NextResponse.json({status: healthy ? "ok" : "needs_attention",
    latest_trade_date: snapshot?.trade_date ?? null,
    days_since_snapshot: daysOld,
    quoted_stocks: snapshot?.metrics.quoted_stocks ?? null,
    financial_data_gap: snapshot?.metrics.financial_data_gap ?? null,
    message: error ?? (healthy ? "latest successful snapshot available" : "snapshot may be stale")},
  {status: healthy ? 200 : 503, headers: {"Cache-Control":"no-store"}});
}
