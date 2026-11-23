export function Sparkline({ prices }: { prices: {day: string;close: number}[] }) {
  if (prices.length < 2) return <p className="muted">历史行情少于2个交易日，暂不展示走势。</p>;
  const values = prices.map(x => x.close);
  const min = Math.min(...values), max = Math.max(...values);
  const range = Math.max(max - min, 0.01);
  const pts = values.map((price, i) => `${20 + i * 760 / (values.length - 1)},${180 - (price - min) * 140 / range}`).join(" ");
  return <div className="sparkline-wrap">
    <svg className="sparkline" viewBox="0 0 800 208" role="img" aria-label="历史收盘价走势，非复权收益图">
      <line x1="20" y1="180" x2="780" y2="180" stroke="#e6e8e5" strokeDasharray="5 7"/>
      <line x1="20" y1="108" x2="780" y2="108" stroke="#e6e8e5" strokeDasharray="5 7"/>
      <line x1="20" y1="40" x2="780" y2="40" stroke="#e6e8e5" strokeDasharray="5 7"/>
      <polyline fill="none" points={pts} stroke="#147d70" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"/>
      <circle cx="780" cy={180 - (values.at(-1)! - min) * 140 / range} r="4.5" fill="#147d70"/>
      <text x="20" y="202" fontSize="12" fill="#84908e">{prices[0].day}</text>
      <text x="779" y="202" textAnchor="end" fontSize="12" fill="#84908e">{prices.at(-1)!.day}</text>
      <text x="780" y="25" textAnchor="end" fontSize="12" fill="#65716f">最高 {max.toFixed(2)} / 最低 {min.toFixed(2)} 元</text>
    </svg>
  </div>;
}
