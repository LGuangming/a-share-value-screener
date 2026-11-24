import type { Metadata } from "next";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { accessConfig, validSession, COOKIE_NAME } from "@/lib/site-access";
import { LogOut } from "lucide-react";
export const dynamic="force-dynamic";
import Nav from "@/components/nav";
import GlobalCommand from "@/components/global-command";
import StockPreview from "@/components/stock-preview";
import {PreferencesRoot,AppearanceControls} from "@/components/workspace-preferences";
import "./globals.css";
import "./theme-orange.css";
import "./workspace.css";

export const metadata: Metadata = {
  title: { default: "价值雷达 | A股多策略选股", template: "%s | 价值雷达" },
  description: "A股多策略选股与研究平台。现有Net-Net每日筛选，同时支持扩展技术面、基本面及多因子策略。",
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: Readonly<{children: React.ReactNode}>) {
  if(!validSession((await cookies()).get(COOKIE_NAME)?.value,accessConfig()))redirect("/login");
  return <html lang="zh-CN"><body>
    <PreferencesRoot><Nav/><div className="site-main"><header className="workspace-global-bar"><GlobalCommand/><div className="site-access-actions"><AppearanceControls/><form method="post" action="/api/access/logout"><button type="submit" className="appearance-button" title="退出访问验证" aria-label="退出访问验证"><LogOut size={17}/></button></form></div></header>{children}
      <footer className="footer">A-share Multi-Strategy Research · 财务筛选与研究信号，不构成交易建议 · 网站归属：Guangming</footer>
    </div><StockPreview/></PreferencesRoot>
  </body></html>;
}
