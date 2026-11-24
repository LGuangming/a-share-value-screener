"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { findStrategy } from "@/lib/strategies";
import { Activity, ChartCandlestick, Columns3, BookOpenText, ChartNoAxesCombined, Database, Layers3, Radar, SlidersHorizontal, UserRound } from "lucide-react";

const links = [
  { href: "/", label: "选股中心", Icon: Radar },
  { href: "/research", label: "行情研究", Icon: ChartCandlestick },
  { href: "/compare", label: "股票对比", Icon: Columns3 },
  { href: "/strategies", label: "策略中心", Icon: Layers3 },
  { href: "/signals", label: "交易观察", Icon: Activity },
  { href: "/backtest", label: "策略回测", Icon: ChartNoAxesCombined },
  { href: "/data-center", label: "数据中心", Icon: Database },
  { href: "/methodology", label: "筛选方法", Icon: BookOpenText },
];

function currentLink(pathname: string): string {
  // Exact root match; nested feature routes stay with their parent menu.
  // Stock details belong to the daily screener.
  const target = links.find(({ href }) =>
    href !== "/" && (pathname === href || pathname.startsWith(`${href}/`))
  );
  return target?.href ?? "/";
}

export default function Nav() {
  const pathname = usePathname();
  const activeHref = currentLink(pathname);
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null);
  useEffect(() => {
    const refresh = () => {
      const id = new URLSearchParams(window.location.search).get("strategy");
      setSelectedStrategy(id && (findStrategy(id)||id==="summary") ? id : null);
    };
    const onStrategyChange = (event: Event) => {
      const id = (event as CustomEvent<string>).detail;
      setSelectedStrategy(findStrategy(id)||id==="summary" ? id : null);
    };
    refresh();
    window.addEventListener("a-share-strategy-change", onStrategyChange);
    window.addEventListener("popstate", refresh);
    return () => {
      window.removeEventListener("a-share-strategy-change", onStrategyChange);
      window.removeEventListener("popstate", refresh);
    };
  }, [pathname]);
  function strategyHref(href: string): string {
    if(selectedStrategy==="summary") return href==="/" ? "/?strategy=summary" : href;
    return selectedStrategy && (href === "/" || href === "/signals" || href === "/methodology")
      ? `${href}?strategy=${encodeURIComponent(selectedStrategy)}`
      : href;
  }
  return <>
    <aside className="sidebar">
      <Link className="brand" href={strategyHref("/")} aria-label="价值雷达 · 返回选股中心" title="价值雷达">
        <span className="brand-compact" aria-hidden="true"><Radar size={22}/></span>
        <span className="brand-text"><strong>价值雷达</strong><small>A 股多策略研究</small></span>
      </Link>
      <div className="nav-caption">研究工作台</div>
      <nav className="nav-main" aria-label="主导航">
        {links.map(({ href, label, Icon }) => {
          const isActive = activeHref === href;
          return <Link key={href} href={strategyHref(href)}
            className={`nav-link${isActive ? " is-active" : ""}`}
            aria-current={isActive ? "page" : undefined} title={label}>
            <Icon size={18}/><span>{label}</span>
          </Link>;
        })}
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-info"><UserRound size={16}/><span>作者：Guangming</span></div>
        <p>研究工具 · 不构成投资建议</p>
      </div>
    </aside>
    <div className="mobile-header">
      <Link className="mobile-brand" href={strategyHref("/")}>价值雷达</Link>
      <SlidersHorizontal size={18} className="muted"/>
    </div>
    <nav className="mobile-nav" aria-label="移动端导航">
      {links.map(({ href, label, Icon }) => {
        const isActive = activeHref === href;
        return <Link key={href} href={strategyHref(href)}
          className={isActive ? "is-active" : undefined}
          aria-current={isActive ? "page" : undefined}>
          <Icon size={17}/><span>{label}</span>
        </Link>;
      })}
    </nav>
  </>;
}
