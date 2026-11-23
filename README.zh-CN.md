# A股价值雷达 v0.1｜中文部署说明

这是已完成代码的**第一版MVP**，不是已部署上线的成品。主要功能：每日净流动资产折价初筛、低价股列表、基本面初步评价、风险标记、交易观察。回测页面仅为后续开发占位，不能提供实盘已验证的交易建议。

## 0. 当前状态

- 代码：Next.js + TypeScript网站，Python数据采集/筛选程序，Supabase PostgreSQL建表脚本，GitHub Actions每日定时任务。
- 页面：每日选股看板、股票详情、交易观察、筛选方法、回测开发占位。
- 数据：**默认不内置假股票或假行情**。必须配置Tushare Token与数据库，第一次任务成功后页面才有真实数据。
- 时间：工作日19:30补行情、每天08:17补财报，数据有实质变化后事件驱动重算NCAV；近期历史价格通过独立补齐工作流回填，默认总覆盖160自然日。
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
3. 进入仓库 Actions → **A-Share Market Data Refresh** → Run workflow，手动执行第一次扫描。
4. 运行记录中应出现行情股票数、财报获取数量、严格候选数量，以及财报未补齐数量。

注意：**每日行情与财报采集已解耦**。行情、财务采集和NCAV计算分为三个独立工作流。行情任务不下载财报，NCAV计算不调用Tushare，财务缺口保留显式风险标记。

## 3A. Tushare官方API与第三方中转API双模式

程序保留原有官方地址 `https://api.tushare.pro`，**正式定时任务默认仍使用官方HTTPS API**。同时支持你提供的第三方中转 `http://tus.sxdata.top/tu_share/`，复用同一HTTP JSON请求结构（`api_name`、`token`、`params`、`fields`），不需要安装Tushare Python SDK。中转能否访问各个财务接口以实际测试结果为准，不能假设其绕过官方积分权限。

**安全须知：** 中转地址目前是HTTP而非HTTPS，Token可能在网络传输中被截获，且由第三方服务器处理。程序不会自动把 `TUSHARE_TOKEN` 转发给中转地址，也不会在原始API返回40203时自动切换中转。若确认信任中转服务，建议使用独立的中转Token；如确需使用同一Token，须自行明确承担明文传输风险。

1. GitHub仓库 → **Settings → Secrets and variables → Actions → Secrets**：新增 `TUSHARE_RELAY_TOKEN`，填中转服务实际要求的Token；原有 `TUSHARE_TOKEN` 不变。
2. GitHub Actions → [A-Share Market Data Refresh](.github/workflows/daily-scan.yml) → **Run workflow**。
3. 将 `api_source` 设置为 `relay`，`check_api_only` 设为 `true`，先检查 `trade_cal`、`stock_basic`，如为交易日还检查 `daily_basic`，**不写入Neon**。
4. 如果连接测试通过，再用 `api_source=relay` 和 `check_api_only=false` 运行**只保存行情数据**的任务；成功后将自动触发独立NCAV缓存重算；财务采集由`A-Share Financial Statement Refresh`工作流负责，建议从`budget=1`或`5`开始。行情不完整时仍会失败，不伪造选股结果。
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

## 3B. 三个独立工作流：行情、财务、NCAV重算

| 工作流 | 执行时间（北京时间） | Tushare调用 | 用途 |
|---|---|---|---|
| [A-Share Market Data Refresh](.github/workflows/daily-scan.yml) | 周一至周五19:30 | 交易日历、股票列表、日行情 | 验证行情完整性并缓存到Neon，不计算NCAV |
| [A-Share Financial Statement Refresh](.github/workflows/financial-refresh.yml) | **每天08:17**（含周末，需开启） | 最多40家，优先缺失报表 | 补齐历史财报，之后根据实际披露日期增量更新 |
| [A-Share Recalculate NCAV](.github/workflows/recalculate-ncav.yml) | 上游采集完成且相关数据实际改变后发布；也可手动运行 | **0次** | 读取Neon缓存行情与财报，计算并原子发布NCAV榜单 |

在 **Settings → Secrets and variables → Actions → Variables** 设置 `ENABLE_FINANCIAL_SCHEDULE=true` 才会启用定时财务任务。财务更新默认 `budget=40`，若已配置 `FINANCIAL_SCHEDULE_BUDGET`，请设为40或删除该变量。两类采集任务和重算任务共享 `a-share-data-pipeline` 并发组，并设置 `queue: max` 和 `cancel-in-progress: false`，允许多个任务依次排队，避免运行中读取不完整数据或待运行任务被新任务替换。

数据流：**08:17财报补齐（7天） / 19:30行情采集（交易日） → Neon数据库 → 数据变更检测 → Recalculate NCAV → 网站榜单**。

行情采集记录入库前后的行情和股票基础资料指纹；同一交易日的行情无变化、且已发布NCAV时，重算流程会跳过。财务采集记录最近行情交易日可使用的最新三张报表内容指纹；**只有财报内容确实发生变化并且披露日期早于该行情交易日时**，才允许重算。若只是补齐上一个交易日收盘后新披露的财报，保留到下一交易日行情更新后自动使用，绝不回填过去的交易日。已采集内容的重复下载、API连接测试、空更新和失败任务都不会触发NCAV发布。

行情工作流仍可手动选择 `check_api_only=true` 检查API。该模式不写数据库；Recalculate NCAV 会通过前置GitHub运行ID核对Neon中是否存在成功的采集任务，没有对应的成功入库记录及`recalc_needed=true`标记，则安全跳过、不发布。工作日休市时行情任务也会跳过写入。旧的手工CLI `scan` 保留兼容，但自动工作流使用分开的 `fetch-market` 和 `recalculate-ncav`。

**财务补齐与增量更新：**

1. 初始阶段选择财报缺失、三表报告期不一致或报告期过旧的低价、低PB股票。每天最多补齐40家公司；只下载需要的报表种类。失败至少冷却6小时。
2. 上述缺口完成后，使用2,000积分可调用的 `disclosure_date` 检查最近两个已结束季度，仅采纳 `actual_date` 已实际披露的公司；未出现真实新披露的，不重复请求财务报表。已保存的完整财报不再因满30天而自动全部重下。
3. 周末不更新股票行情，早上08:17继续补齐财报，但仅在财报实际改变了最新已完成交易日可用的信息时触发NCAV重算。**历史时点安全规则**：重算使用最后一个已保存的交易日行情，且只纳入该交易日之前已披露的财报（`disclosed_at < trade_date`）。如果周五收盘后、周末新披露报表，这些资料必须等下一交易日行情入库后才能进入正式榜单，不能回填周五快照。
4. 重算仅需GitHub Secret `DATABASE_URL`，不使用Tushare Token；行情或财报API限流不会影响已发布快照。对于当日多次重算，数据库会在同一事务中替换同一交易日的筛选结果。

**补充限制**：披露日历并非完整历史财报修订事件流，最新两季以外的更正、源站延迟、缺失公告日期仍需单独核查。已有成功的快照不会因下游任务失败被标记为成功或清空。

## 3C. 近期历史行情补齐（独立工作流）

[**A-Share Recent Price History Backfill**](.github/workflows/recent-price-history.yml) 是一次性历史数据建设工作流，与行情、财务、NCAV三项定时任务相互独立。首次发布会自动启动一次，后续需要重试可在GitHub Actions手动使用 **Run workflow**。不设长期Cron，避免补齐完成后每天反复调用接口。

- 默认回填**最近160个自然日**，结束日为Neon最近已保存的交易日；有至少50个有效交易日，才能消除20日/50日均线的 `INSUFFICIENT_HISTORY` 提示。
- 只使用Tushare官方HTTPS的 `trade_cal`、`stock_basic` 和按交易日调用的 `daily_basic`。每个历史交易日最多调用一次 `daily_basic`；已完成的日期跳过，重复运行只请求仍缺少的交易日。
- 新增**精简表** `price_history(ts_code,trade_date,close)` 保存未复权收盘价，`price_history_fetches` 保存每个交易日的完成记录；程序启动时自动创建这两张表，不需要手工运行数据库迁移。已有 `daily_prices` 的完整交易日也会跳过。单日数据与其完成标记在同一事务中写入，失败可断点续跑。
- `Recalculate NCAV` 读取 `price_history` 和 `daily_prices` 合并去重后的最近90日价格；优先保留正式行情表的相同交易日收盘价。回填完成后自动运行一次独立NCAV计算，更新网站交易观察信号，不重复请求行情。
- **局限**：历史价格未复权，分红除权及拆并股可能造成技术均线不连续；历史退市证券不在此次当前上市股票的回填范围内。此工作流旨在补齐**当前股票池的近期交易观察**，不是已验证的历史回测引擎。GitHub Actions为尽力调度，首次任务的实际完成情况以运行日志为准。

## 3D. 交易观察：基本面复核与现金流数据分层

交易观察列表和个股详情页根据现有风险标记展示**复核原因、风险等级、优先级、系统规则初评结论**。不增加数据库字段，无需额外迁移或手动重新发布NCAV。

- **风险等级**：高——压力NCAV不足、负债率过高、重要数据异常，或经营现金流与净利润同时为负；中——单项现金流负值、利润亏损、应收/存货集中、资料缺失等；低——现有规则未命中重点复核项目。
- **优先级**：P0优先处理、P1尽快核验、P2常规跟踪。另按**经营风险、资产质量、资料缺失**明确主要复核类别。
- **系统初评结论**：通过、待补充资料、不建议进入买入观察。它只用于研究筛选和人工尽调排序，不代表审计意见、实际资产回收验证或投资建议。非严格NCAV候选者显示“不适用”。
- **现金流量表缺失**：截至交易日，Neon中没有可用的现金流量表；不代表企业尚未公开披露。
- **经营现金流关键字段缺失**：已取得可用现金流量表，但 `n_cashflow_act` 为空或无效。
- **报表尚未披露（截至筛选日）**：Neon存在该交易日之后才披露的报表记录，能够确认当时不可用，不能提前计入历史筛选。

这三类现金流数据问题都不等于现金流为负或为零。已发布快照中的原泛化标记 `CASHFLOW_NOT_VERIFIED` 可由页面联查财报证据并细分；不需要重新调用Tushare。规则分类由 `lib/review.ts` 实现，`npm run test:review` 执行相关边界测试。


### 个股收盘价变化修复

个股详情页原来仅查 `daily_prices`，导致即使后端MA50已从 `price_history` 取得完整历史数据，前端仍显示“历史行情少于2个交易日”。现在页面合并 `daily_prices` 与 `price_history`，按交易日去重，优先采用当天正式行情收盘价，并取最近至多120个交易日展示。近期历史回填默认**160个自然日（总区间）**，已有日期跳过，仅下载额外缺失日期。

如果网站使用专门的只读角色 `screener_reader`，回填程序会自动赋予该角色新表 `price_history` 的读取权限；若使用其它自定义只读角色，需要在Neon中由有权账户执行 `GRANT SELECT ON price_history TO 你的只读角色;`。不会开放给PUBLIC。

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
PYTHONPATH=backend python -m value_screener.cli bootstrap-prices --days 160 --min-sessions 50
PYTHONPATH=backend python -m value_screener.cli fetch-market
PYTHONPATH=backend python -m value_screener.cli recalculate-ncav
PYTHONPATH=backend python -m value_screener.cli refresh-financials --budget 40
PYTHONPATH=backend python -m value_screener.cli recalculate-ncav
```

如果有Tushare Pro VIP全市场财务接口权限，建议再执行：

```bash
PYTHONPATH=backend python -m value_screener.cli bootstrap-financials --period 20260930
PYTHONPATH=backend python -m value_screener.cli bootstrap-financials --period 20260630
PYTHONPATH=backend python -m value_screener.cli recalculate-ncav
```

在Windows PowerShell中，需要先设置 `$env:PYTHONPATH='backend'`，并在**本机环境**设置对应数据库URL和Tushare Token；不要将真实密钥写入脚本或提交到GitHub。

## 6. 验证运行与常见问题

- `https://你的网站/api/health`：检查最近成功发布的扫描日期，超出5日返回告警状态；节假日误报需结合交易日历判断。
- 首页显示“尚未配置数据库”：检查Vercel `DATABASE_READ_URL`。
- 首页显示“数据库已连接但没有结果”：检查GitHub Actions运行日志，确认首次财务扫描成功。
- 股票数量偏少：检查Tushare行情接口、已有财报的覆盖率和独立财务补齐工作流。**数据缺口不等于没有符合筛选条件的股票。**
- 交易观察显示“历史行情不足”：运行独立的 `A-Share Recent Price History Backfill`，默认自动回填最近160自然日的未复权收盘价并在完成后重算。
- 回测页面没有收益：回测引擎尚未开发，不能将技术观察信号误认为已回测策略。

## 7. 算法提醒

账面 `NCAV=流动资产-全部负债`，覆盖率至少125%。压力测试再扣少数股东权益及对应流动资产折价，但不能替代对资产实际可回收性、受限资金、应收账龄和诉讼担保的核查。

由于源数据没有精确披露时间，为避免使用当日收盘后发布的财报，本程序保守规定：**只使用交易日期之前已披露的财报**。2026年三季报不得用于其实际公告之前的历史策略筛选。

最后，交易信号仅供研究，不自动下单、不保证收益。上线面向公众前，需核验数据源的商业授权与适用的合规要求。

## 多策略选股及回测第一阶段

网站新增 **/strategies 策略中心**：Net-Net继续作为唯一正式每日选股策略；
技术趋势仅供工程研究，超跌反转与质量动量处于待开发状态。策略ID、版本、
参数及所需数据统一登记于 `strategies/catalog.json`，可以按模板调整参数草稿，
但草稿**不等于已保存、执行或验证过的策略**。

本阶段单独增加：Neon容量**只读**审计工作流；2025年全市场Tushare历史日线
按月ZSTD Parquet归档原型；通用Python持仓/交易成本/T+1模拟内核及自动测试。
2025年样本**默认只保存在7天后过期的GitHub Actions临时Artifact**；
未配置私有对象存储前，不宣称已实现永久历史仓库。通过
`BACKTEST_ARCHIVE_UPLOAD=true` 与显式 `OBJECT_STORE_*` 配置后才允许上传。
程序不会把多年历史行情写进Neon，未来正式回测须先补齐历史ST、财报公告日、
复权与交易限制数据。详细说明见
[多策略回测开发规范](docs/MULTI_STRATEGY_BACKTEST.md)。
