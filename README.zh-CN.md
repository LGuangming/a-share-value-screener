# A股价值雷达 v0.1｜中文部署说明

这是已完成代码的**第一版MVP**，不是已部署上线的成品。主要功能：每日净流动资产折价初筛、低价股列表、基本面初步评价、风险标记、交易观察。回测页面仅为后续开发占位，不能提供实盘已验证的交易建议。

## 0. 当前状态

- 代码：Next.js + TypeScript网站，Python数据采集/筛选程序，Supabase PostgreSQL建表脚本，GitHub Actions每日定时任务。
- 页面：每日选股看板、股票详情、交易观察、筛选方法、回测开发占位。
- 数据：**默认不内置假股票或假行情**。必须配置Tushare Token与数据库，第一次任务成功后页面才有真实数据。
- 时间：每个中国A股交易日北京时间19:30扫描；节假日按上证交易日历跳过。
- 股票范围：第一版先按沪深主板非ST低价股筛选；其他板块、退市历史、重大风险公告暂不覆盖完整。

## 1. 创建一个独立的私人GitHub仓库

推荐仓库名：`LGuangming/a-share-value-screener`，**Private**。

先在Windows上安装Git和[GitHub CLI](https://cli.github.com/)，解压本项目并进入文件夹，运行：

```powershell
gh auth login
.\publish-to-github.ps1
```

脚本会在你本机完成Git初始化、初始提交、创建GitHub私人仓库和推送代码，不会修改其他仓库。

如果不安装GitHub CLI，也可以在GitHub网页先创建同名空白私人仓库，进入解压后的项目目录执行：

```powershell
git init -b main
git add .
git commit -m "Initialize A-share value screener"
git remote add origin https://github.com/LGuangming/a-share-value-screener.git
git push -u origin main
```

## 2. 创建Supabase数据库

1. 登录 [Supabase](https://supabase.com/)，新建项目。
2. 进入 SQL Editor，粘贴并执行 `database/schema.sql`。
3. 在Supabase创建只读账号，给网站使用。`schema.sql` 最后附有账号及只读权限示例（需自己设置安全密码）。
4. 获取PostgreSQL连接字符串。部分环境建议使用Supabase的Session Pooler或Transaction Pooler，根据服务商的实际连接要求调整主机、端口和SSL。

注意：数据库连接字符串中包含密码，不能上传到代码仓库，也不能设置成 `NEXT_PUBLIC_` 开头的前端变量。

## 3. 获取Tushare Pro Token并配置GitHub Secrets

1. 注册 [Tushare Pro](https://tushare.pro/) 并确认账户拥有 `trade_cal`、`stock_basic`、`daily_basic`、`balancesheet`、`income`、`cashflow` 等接口的调用权限。
2. GitHub仓库 → Settings → Secrets and variables → Actions → 新建两个Repository secrets：
   - `TUSHARE_TOKEN`：你的行情及财务数据Token
   - `DATABASE_URL`：有写权限的PostgreSQL连接字符串
3. 进入仓库 Actions → **A-Share Daily Net-Net Scan** → Run workflow，手动执行第一次扫描。
4. 运行记录中应出现行情股票数、财报获取数量、严格候选数量，以及财报未补齐数量。

注意：**每日行情扫描已经与财务报表采集解耦**。每日任务只使用3个基础接口，结合Neon已保存的财务数据计算NCAV；未补齐财报会保留`DATA_GAP`或相应风险标记，不会伪造数据。另有独立`A-Share Financial Statement Refresh`用于增量补齐，默认每批5家公司（每家3个财务API）。

## 3A. Tushare官方API与第三方中转API双模式

程序保留原有官方地址 `https://api.tushare.pro`，**正式定时任务默认仍使用官方HTTPS API**。同时支持你提供的第三方中转 `http://tus.sxdata.top/tu_share/`，复用同一HTTP JSON请求结构（`api_name`、`token`、`params`、`fields`），不需要安装Tushare Python SDK。中转能否访问各个财务接口以实际测试结果为准，不能假设其绕过官方积分权限。

**安全须知：** 中转地址目前是HTTP而非HTTPS，Token可能在网络传输中被截获，且由第三方服务器处理。程序不会自动把 `TUSHARE_TOKEN` 转发给中转地址，也不会在原始API返回40203时自动切换中转。若确认信任中转服务，建议使用独立的中转Token；如确需使用同一Token，须自行明确承担明文传输风险。

1. GitHub仓库 → **Settings → Secrets and variables → Actions → Secrets**：新增 `TUSHARE_RELAY_TOKEN`，填中转服务实际要求的Token；原有 `TUSHARE_TOKEN` 不变。
2. GitHub Actions → [A-Share Daily Net-Net Scan](.github/workflows/daily-scan.yml) → **Run workflow**。
3. 将 `api_source` 设置为 `relay`，`check_api_only` 设为 `true`，先检查 `trade_cal`、`stock_basic`，如为交易日还检查 `daily_basic`，**不写入Neon**。
4. 如果连接测试通过，再用 `api_source=relay` 和 `check_api_only=false` 运行**仅含行情与缓存财报筛选**的每日任务；财务采集请使用独立`A-Share Financial Statement Refresh`工作流，建议从`budget=1`或`5`开始。行情不完整时仍会失败，不伪造选股结果。
5. 想恢复官方接口，将 `api_source` 切回 `official`。工作日北京时间19:30的自动任务始终按默认 `official` 执行。

默认中转网址已内置；如果服务方提供HTTPS或更换域名，可在GitHub Actions的 **Variables** 添加 `TUSHARE_RELAY_URL`。仅在显式选择中转的运行中生效。

**统一限速及失败补偿（2026-11-23）：** 官方Tushare与中转API的每个请求（含重试）都经过底层节流：前后请求至少间隔0.61秒，并在滚动60秒窗口中最多发送100次。中转额外保留至少5秒间隔（可在手动运行中调整 `relay_min_interval_seconds`，建议5或更高）。遇到服务端429、暂时性的网络断连/超时或HTTP 5xx，**等待60秒再重试，最多尝试3次**；达到上限后报错，不发布假数据。权限拒绝40203、参数错误等永久性失败立即停止，不浪费额度。此限制针对单个Python作业；多个外部实例同时使用同一Token时仍需额外统一额度，且中转商可能施加更严格IP级限制。默认定时任务继续采用官方HTTPS接口。

本机CLI可选用环境变量（不要提交真实Token）：

```bash
# 官方模式（默认）：TUSHARE_TOKEN + DATABASE_URL
PYTHONPATH=backend python -m value_screener.cli check-api

# 中转模式：显式提供 TUSHARE_API_SOURCE=relay、
# TUSHARE_RELAY_TOKEN 和 TUSHARE_ALLOW_HTTP=1
PYTHONPATH=backend python -m value_screener.cli check-api
```

`check-api` 不写数据库，也不需要 `DATABASE_URL`。 `scan`、`bootstrap-prices`、`bootstrap-financials` 均采用相同的数据源选择配置。官方Token和中转Token都只能放在机密变量中，不能放在GitHub代码、URL参数或公开日志中。

## 3B. 每日扫描与独立财务增量更新（2026-11-23升级）

为避免中转接口`balancesheet`、`income`、`cashflow`反复返回429而令当日选股失败，现拆分两个GitHub Actions工作流：

| 任务 | 链接 | 默认调用 |
|---|---|---|
| A-Share Daily Net-Net Scan | [每日行情与筛选](.github/workflows/daily-scan.yml) | 交易日历、股票基本信息、每日行情，各1次；**不下载财报** |
| A-Share Financial Statement Refresh | [独立财务补齐](.github/workflows/financial-refresh.yml) | 每批默认5家公司，最多约15次财务请求（未计失败重试） |

**建议的首次执行顺序：**

1. 先运行每日筛选（`api_source=relay`、`check_api_only=false`）。该步骤会更新Neon行情，以缓存中**公告日早于交易日**的财务数据进行NCAV计算，财务缺失即标记为数据缺口。
2. 如需要补齐财报，打开独立财务工作流 → Run workflow，`api_source=relay`、`budget=1`或`5`。财务API再次429时，只会使**财务工作流**失败，并保留此前已成功写入的个股财报；不会回滚或标记每日行情任务失败。
3. 财报补齐后需**再运行一次每日筛选**，才会刷新网站的当日已发布NCAV结果；财务任务本身不会直接修改网站上的选股榜单。
4. 每个失败的股票会写入`financial_fetch_log`，至少隔6小时才会进入新一轮候选财务补齐队列；已成功的股票默认约30天后才会被重新排入刷新队列。当前为按时间和缺口选取的**增量补齐**，尚不是完全基于交易所新公告事件的实时触发。
5. 如需启用财务自动更新，在仓库Settings → Secrets and variables → Actions → **Variables** 添加 `ENABLE_FINANCIAL_SCHEDULE=true`；开启后工作日北京时间23:00执行。定时默认选择官方Tushare API；需要专门为定时财务任务使用中转时，可以再加 `FINANCIAL_SCHEDULE_API_SOURCE=relay`。由于当前中转经常429，**建议先不要开启定时财务更新**，待服务商稳定后再启用。

每日扫描与财务补齐共用一个GitHub并发组，避免同仓库两项任务同时调用API。此机制不能限制仓库以外使用同一Token的其他客户端。中转地址目前为HTTP，建议服务商提供HTTPS后再考虑常态化自动采集。

## 4. 在Vercel部署

1. 登录 [Vercel](https://vercel.com/)，选择 Add New → Project → Import Git Repository，选中刚创建的仓库。
2. Framework Preset选择Next.js；根目录为项目根目录。
3. 环境变量新增 `DATABASE_READ_URL`，填写**只读**数据库账号的连接字符串。
4. 点击 Deploy。
5. 建议先为个人研究用途开启Vercel Deployment Protection，勿直接公开财务数据和后台管理接口。

Vercel仅渲染结果，不参与数千只股票数据采集，不需要Tushare Token。

## 5. 初次完整数据准备（可选）

下面是本机终端执行的Python程序命令，需安装Python 3.11并正确配置`TUSHARE_TOKEN`和`DATABASE_URL`环境变量：

```bash
pip install -r backend/requirements.txt
PYTHONPATH=backend python -m value_screener.cli init-db
PYTHONPATH=backend python -m value_screener.cli bootstrap-prices --days 110
PYTHONPATH=backend python -m value_screener.cli scan
PYTHONPATH=backend python -m value_screener.cli refresh-financials --budget 5
```

如果有Tushare Pro VIP全市场财务接口权限，建议再执行：

```bash
PYTHONPATH=backend python -m value_screener.cli bootstrap-financials --period 20260930
PYTHONPATH=backend python -m value_screener.cli bootstrap-financials --period 20260630
PYTHONPATH=backend python -m value_screener.cli scan
```

在Windows PowerShell中，需要先设置 `$env:PYTHONPATH='backend'`，并在**本机环境**设置对应数据库URL和Tushare Token；不要将真实密钥写入脚本或提交到GitHub。

## 6. 验证运行与常见问题

- `https://你的网站/api/health`：检查最近成功发布的扫描日期，超出5日返回告警状态；节假日误报需结合交易日历判断。
- 首页显示“尚未配置数据库”：检查Vercel `DATABASE_READ_URL`。
- 首页显示“数据库已连接但没有结果”：检查GitHub Actions运行日志，确认首次财务扫描成功。
- 股票数量偏少：检查Tushare行情接口、已有财报的覆盖率和独立财务补齐工作流。**数据缺口不等于没有符合筛选条件的股票。**
- 买入信号显示“历史行情不足”：先运行 `bootstrap-prices` 回填至少50个交易日。
- 回测页面没有收益：回测引擎尚未开发，不能将技术观察信号误认为已回测策略。

## 7. 算法提醒

账面 `NCAV=流动资产-全部负债`，覆盖率至少125%。压力测试再扣少数股东权益及对应流动资产折价，但不能替代对资产实际可回收性、受限资金、应收账龄和诉讼担保的核查。

由于源数据没有精确披露时间，为避免使用当日收盘后发布的财报，本程序保守规定：**只使用交易日期之前已披露的财报**。2026年三季报不得用于其实际公告之前的历史策略筛选。

最后，交易信号仅供研究，不自动下单、不保证收益。上线面向公众前，需核验数据源的商业授权与适用的合规要求。
