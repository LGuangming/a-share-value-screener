"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLayoutEffect, useRef, useState } from "react";
import { Activity, BookOpenText, ChartNoAxesCombined, Database, Radar, SlidersHorizontal } from "lucide-react";

const links = [
  { href: "/", label: "每日选股", Icon: Radar },
  { href: "/signals", label: "交易观察", Icon: Activity },
  { href: "/backtest", label: "策略回测", Icon: ChartNoAxesCombined },
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
  const desktopNavRef = useRef<HTMLElement>(null);
  const [indicator, setIndicator] = useState<{ top: number; height: number } | null>(null);

  useLayoutEffect(() => {
    const nav = desktopNavRef.current;
    if (!nav) return;

    const measure = () => {
      const selected = nav.querySelector<HTMLElement>(".nav-link.is-active");
      if (!selected) {
        setIndicator(null);
        return;
      }
      const parentRect = nav.getBoundingClientRect();
      const selectedRect = selected.getBoundingClientRect();
      setIndicator({ top: selectedRect.top - parentRect.top + nav.scrollTop, height: selectedRect.height });
    };

    measure();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    if (observer) {
      observer.observe(nav);
      for (const item of nav.querySelectorAll(".nav-link")) observer.observe(item);
    }
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [activeHref]);

  return <>
    <aside className="sidebar">
      <Link className="brand" href="/">
        <span className="brand-icon"><Radar size={24} strokeWidth={1.9}/></span>
        <span className="brand-text"><strong>价值雷达</strong><small>VALUE RADAR / A-SHARE</small></span>
      </Link>
      <div className="nav-caption">RESEARCH WORKSPACE</div>
      <nav ref={desktopNavRef} className="nav-main" aria-label="主导航">
        <span
          aria-hidden="true"
          className={`nav-active-indicator${indicator ? " is-ready" : ""}`}
          style={indicator ? { transform: `translateY(${indicator.top}px)`, height: indicator.height } : undefined}
        />
        {links.map(({ href, label, Icon }) => {
          const isActive = activeHref === href;
          return <Link key={href} href={href}
            className={`nav-link${isActive ? " is-active" : ""}`}
            aria-current={isActive ? "page" : undefined}>
            <Icon size={18}/><span>{label}</span>
          </Link>;
        })}
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-info"><Database size={16}/><span>数据源：Tushare Pro</span></div>
        <p>研究工具 · 不构成投资建议</p>
      </div>
    </aside>
    <div className="mobile-header">
      <Link className="mobile-brand" href="/"><Radar size={20}/> 价值雷达</Link>
      <SlidersHorizontal size={18} className="muted"/>
    </div>
    <nav className="mobile-nav" aria-label="移动端导航">
      {links.map(({ href, label, Icon }) => {
        const isActive = activeHref === href;
        return <Link key={href} href={href}
          className={isActive ? "is-active" : undefined}
          aria-current={isActive ? "page" : undefined}>
          <Icon size={17}/><span>{label}</span>
        </Link>;
      })}
    </nav>
  </>;
}
