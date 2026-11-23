# A股价值雷达 / A-share Value Screener

Next.js + PostgreSQL + Python value-investing screener for A-shares, focusing on Net-Net (NCAV = Current Assets − Total Liabilities), adjusted asset valuation, preliminary financial-risk assessment and research-only trend signals.

The website displays **real data only** when a PostgreSQL database is configured; it does not ship fake or sample quotes. The daily scanner needs Tushare Pro data access and GitHub Actions secrets. For setup see `README.zh-CN.md` after the source import.

> Research prototype, not personalized financial advice or an automated trading system. Backtesting is not yet implemented.
