import type { Metadata } from "next";
import Nav from "@/components/nav";
import GlobalCommand from "@/components/global-command";
import StockPreview from "@/components/stock-preview";
import {PreferencesRoot,AppearanceControls} from "@/components/workspace-preferences";
import "./workspace.css";
import "./globals.css";
import "./theme-orange.css";

export const metadata: Metadata = {
  title: { default: "价值雷达 | A股多策略选股", template: "%s | 价值雷达" },
  description: "A股多策略选股与研究平台。现有Net-Net每日筛选，同时支持扩展技术面、基本面及多因子策略。",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{children: React.ReactNode}>) {
  return <html lang="zh-CN"><body>
    <PreferencesRoot><Nav/><div className="site-main"><header className="workspace-global-bar"><GlobalCommand/><AppearanceControls/></header>{children}
      <footer className="footer">A-share Multi-Strategy Research · 财务筛选与研究信号，不构成交易建议 · 网站归属：Guangming</footer>
    </div><StockPreview/></PreferencesRoot>
  </body></html>;
}
