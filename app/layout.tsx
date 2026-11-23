import type { Metadata } from "next";
import Nav from "@/components/nav";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "价值雷达 | A股多策略选股", template: "%s | 价值雷达" },
  description: "A股多策略选股与研究平台。现有Net-Net每日筛选，同时支持扩展技术面、基本面及多因子策略。",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{children: React.ReactNode}>) {
  return <html lang="zh-CN"><body>
    <Nav/><div className="site-main">{children}
      <footer className="footer">价值雷达 / A-share Multi-Strategy Research · 财务筛选与研究信号，不构成交易建议 · 数据版权归原提供方</footer>
    </div>
  </body></html>;
}
