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

注意：每天默认最多补30家公司的三张报表，属于**逐步补齐数据**，不是第一天就能完整扫描数千家上市公司。如果你的Tushare权限支持VIP批量报表，可以使用下文所述的财报批量初始化。

## 3A. Tushare官方API与第三方中转API双模式

程序保留原有官方地址 `https://api.tushare.pro`，**正式定时任务默认仍使用官方HTTPS API**。同时支持你提供的第三方中转 `http://tus.sxdata.top/tu_share/`，复用同一HTTP JSON请求结构（`api_name`、`token`、`params`、`fields`），不需要安装Tushare Python SDK。中转能否访问各个财务接口以实际测试结果为准，不能假设其绕过官方积分权限。

**安全须知：** 中转地址目前是HTTP而非HTTPS，Token可能在网络传输中被截获，且由第三方服务器处理。程序不会自动把 `TUSHARE_TOKEN` 转发给中转地址，也不会在原始API返回40203时自动切换中转。若确认信任中转服务，建议使用独立的中转Token；如确需使用同一Token，须自行明确承担明文传输风险。

1. GitHub仓库 → **Settings → Secrets and variables → Actions → Secrets**：新增 `TUSHARE_RELAY_TOKEN`，填中转服务实际要求的Token；原有 `TUSHARE_TOKEN` 不变。
2. GitHub Actions → [A-Share Daily Net-Net Scan](.github/workflows/daily-scan.yml) → **Run workflow**。
3. 将 `api_source` 设置为 `relay`，`check_api_only` 设为 `true`，先仅检查 `trade_cal` 和 `stock_basic`，**不写入Neon**。
4. 如果连接测试通过，再用 `api_source=relay` 和 `check_api_only=false` 运行完整扫描。出现接口缺失或数据不完整时会失败，不伪造选股结果。
5. 想恢复官方接口，将 `api_source` 切回 `official`。工作日北京时间19:30的自动任务始终按默认 `official` 执行。

默认中转网址已内置；如果服务方提供HTTPS或更换域名，可在GitHub Actions的 **Variables** 添加 `TUSHARE_RELAY_URL`。仅在显式选择中转的运行中生效。

本机CLI可选用环境变量（不要提交真实Token）：

```bash
# 官方模式（默认）：TUSHARE_TOKEN + DATABASE_URL
PYTHONPATH=backend python -m value_screener.cli check-api

# 中转模式：显式提供 TUSHARE_API_SOURCE=relay、
# TUSHARE_RELAY_TOKEN 和 TUSHARE_ALLOW_HTTP=1
PYTHONPATH=backend python -m value_screener.cli check-api
```

`check-api` 不写数据库，也不需要 `DATABASE_URL`。 `scan`、`bootstrap-prices`、`bootstrap-financials` 均采用相同的数据源选择配置。官方Token和中转Token都只能放在机密变量中，不能放在GitHub代码、URL参数或公开日志中。

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
PYTHONPATH=backend python -m value_screener.cli scan --financial-budget 50
```

如果有Tushare Pro VIP全市场财务接口权限，建议再执行：

```bash
PYTHONPATH=backend python -m value_screener.cli bootstrap-financials --period 20260930
PYTHONPATH=backend python -m value_screener.cli bootstrap-financials --period 20260630
PYTHONPATH=backend python -m value_screener.cli scan --financial-budget 0
```

在Windows PowerShell中，需要先设置 `$env:PYTHONPATH='backend'`，并在**本机环境**设置对应数据库URL和Tushare Token；不要将真实密钥写入脚本或提交到GitHub。

## 5A. AKShare独立测试模式（保留Tushare Pro）

- GitHub Actions → [AKShare Experimental Test](.github/workflows/akshare-test.yml) → Run workflow。
- `codes`：默认 `000910,600051,002462`，可以填1–8只沪深A股股票代码。
- `mode=smoke`：仅测试AKShare联网获取样本股票的价格、市值及三张财务报表，生成 `akshare-test-results.json` Artifact；**不需要Tushare Token、不改动Neon数据库、不改变正式筛选结果**。
- `mode=publish`：需GitHub Secret `DATABASE_URL` 有写入权限，且为北京时间工作日15:45之后；将实验结果写入同一Neon库的隔离记录 `job_type=akshare_test`、`strategy=akshare_test_v1`。在Vercel网站 `/akshare-test` 页面查看。
- **行情口径**：优先从AKShare `stock_individual_info_em` 获取指定股票的“最新”和“总市值”，而不是抓全市场push2接口。样本行情没有可独立核实的交易日标签，PB也可能缺失；这些问题会明确标记，**不能当作收盘行情或交易信号**。
- **财报口径**：采用东方财富 `stock_balance_sheet_by_report_em`、`stock_profit_sheet_by_report_em`、`stock_cash_flow_sheet_by_report_em`，仅接受含实际公告日期且公告日早于筛选日期的记录。合并口径等项目缺失时标记为待核实；不会凭空填报表数字。
- Github托管机器访问国内行情源时可能遭遇IP限流（例如 `Push2RateLimitError`）或网页字段变化。如发生此情况，**视为供应商接口失败，不会产生虚假的股票数据**。可以在国内网络环境用同一代码手工运行对照测试。
- 本模式不是全A股扫描，也不会自动代替每天19:30的Tushare正式任务。

本机独立测试（不需数据库）：

```bash
pip install -r backend/requirements-akshare.txt
PYTHONPATH=backend python -m value_screener.akshare_test --codes 000910,600051,002462
```

如需将实验结果写入Neon（会更新实验页，但不会修改正式Tushare榜单），额外设置 `DATABASE_URL` 并添加 `--publish` 参数。**建议先运行smoke核查数据质量，再执行publish。**

## 6. 验证运行与常见问题

- `https://你的网站/api/health`：检查最近成功发布的扫描日期，超出5日返回告警状态；节假日误报需结合交易日历判断。
- 首页显示“尚未配置数据库”：检查Vercel `DATABASE_READ_URL`。
- 首页显示“数据库已连接但没有结果”：检查GitHub Actions运行日志，确认首次财务扫描成功。
- 股票数量偏少：检查Tushare接口权限、财报覆盖率及每日获取预算。**数据缺口不等于没有符合筛选条件的股票。**
- 买入信号显示“历史行情不足”：先运行 `bootstrap-prices` 回填至少50个交易日。
- 回测页面没有收益：回测引擎尚未开发，不能将技术观察信号误认为已回测策略。

## 7. 算法提醒

账面 `NCAV=流动资产-全部负债`，覆盖率至少125%。压力测试再扣少数股东权益及对应流动资产折价，但不能替代对资产实际可回收性、受限资金、应收账龄和诉讼担保的核查。

由于源数据没有精确披露时间，为避免使用当日收盘后发布的财报，本程序保守规定：**只使用交易日期之前已披露的财报**。2026年三季报不得用于其实际公告之前的历史策略筛选。

最后，交易信号仅供研究，不自动下单、不保证收益。上线面向公众前，需核验数据源的商业授权与适用的合规要求。
