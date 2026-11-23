import type { Metadata } from "next";
import Nav from "@/components/nav";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "价值雷达 | A股烟蒂股筛选", template: "%s | 价值雷达" },
  description: "A股Net-Net、净流动资产折价与困境反转研究平台。每日按披露数据计算，结果可追溯。",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{children: React.ReactNode}>) {
  return <html lang="zh-CN"><body>
    <Nav/><div className="site-main">{children}
      <footer className="footer">价值雷达 / A-share Net-Net Research · 财务筛选与研究信号，不构成交易建议 · 数据版权归原提供方</footer>
    </div>
  </body></html>;
}
