export function Sparkline({ prices }: { prices: {day: string;close: number}[] }) {
  if (prices.length < 2) return <p className="muted">历史行情少于2个交易日，暂不展示走势。</p>;
  const values = prices.map(x => x.close);
  const min = Math.min(...values), max = Math.max(...values);
  const range = Math.max(max - min, 0.01);
  const lower = min - range * .08, upper = min + range * 1.08;
  const y = (price: number) => 190 - (price-lower) * 155 / (upper-lower);
  const x = (i: number) => 12 + i * 720 / (values.length - 1);
  const pts = values.map((price, i) => `${x(i)},${y(price)}`).join(" ");
  return <div className="sparkline-wrap">
    <svg className="sparkline" viewBox="0 0 800 224" role="img" aria-label="历史收盘价走势，非复权收益图">
      {[0,1,2,3].map(i=>{
        const price=lower+(upper-lower)*i/3;
        return <g key={i}><line x1="12" y1={y(price)} x2="732" y2={y(price)} stroke="#e2e5e8"/>
          <text x="790" y={y(price)+4} textAnchor="end" fontSize="12" fill="#606770">{price.toFixed(2)}</text></g>;
      })}
      <polyline className="price-line" fill="none" points={pts} stroke="var(--accent)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke"/>
      <circle cx={x(values.length-1)} cy={y(values.at(-1)!)} r="3.5" fill="var(--accent)"/>
      <text x="12" y="215" fontSize="12" fill="#606770">{prices[0].day}</text>
      <text x="732" y="215" textAnchor="end" fontSize="12" fill="#606770">{prices.at(-1)!.day}</text>
      <text x="732" y="16" textAnchor="end" fontSize="12" fill="#606770">最高 {max.toFixed(2)} / 最低 {min.toFixed(2)} 元</text>
    </svg>
  </div>;
}
