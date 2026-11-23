import Link from "next/link";
import { Activity, BookOpenText, ChartNoAxesCombined, Database, FlaskConical, Radar, SlidersHorizontal } from "lucide-react";

const links = [
  { href: "/", label: "每日选股", Icon: Radar },
  { href: "/signals", label: "交易观察", Icon: Activity },
  { href: "/akshare-test", label: "AKShare测试", Icon: FlaskConical },
  { href: "/backtest", label: "策略回测", Icon: ChartNoAxesCombined },
  { href: "/methodology", label: "筛选方法", Icon: BookOpenText },
];

export default function Nav() {
  return <>
    <aside className="sidebar">
      <Link className="brand" href="/">
        <span className="brand-icon"><Radar size={24} strokeWidth={1.9}/></span>
        <span className="brand-text"><strong>价值雷达</strong><small>VALUE RADAR / A-SHARE</small></span>
      </Link>
      <div className="nav-caption">RESEARCH WORKSPACE</div>
      <nav className="nav-main" aria-label="主导航">
        {links.map(({href,label,Icon}) => <Link key={href} href={href} className="nav-link"><Icon size={18}/><span>{label}</span></Link>)}
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-info"><Database size={16}/><span>正式：Tushare Pro · 实验：AKShare</span></div>
        <p>研究工具 · 不构成投资建议</p>
      </div>
    </aside>
    <div className="mobile-header">
      <Link className="mobile-brand" href="/"><Radar size={20}/> 价值雷达</Link>
      <SlidersHorizontal size={18} className="muted"/>
    </div>
    <nav className="mobile-nav" aria-label="移动端导航">
      {links.map(({href,label,Icon}) => <Link key={href} href={href}><Icon size={17}/><span>{label}</span></Link>)}
    </nav>
  </>;
}
