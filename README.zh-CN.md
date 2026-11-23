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

注意：**每日行情与财报采集已解耦**。每日行情任务使用Neon已有财报筛选NCAV；独立财务工作流每周7天、每次最多40家公司，先补齐缺失报表，再根据实际披露日期增量更新。财务缺口保留显式风险标记。

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

## 3B. 每日行情和两阶段财报补齐（2026-11-23）

| 工作流 | 北京时间 | 默认参数 | 用途 |
|---|---|---|---|
| [A-Share Daily Net-Net Scan](.github/workflows/daily-scan.yml) | **周一至周五19:30** | `api_source=official` | 更新行情并发布NCAV筛选 |
| [A-Share Financial Statement Refresh](.github/workflows/financial-refresh.yml) | **每天23:00（含周六、周日）** | `api_source=official`, `budget=40` | 历史补齐，完成后按实际披露增量获取 |

在仓库 Settings → Secrets and variables → Actions → Variables 添加 `ENABLE_FINANCIAL_SCHEDULE=true` 才会开启定时财务任务。当前工作流同时支持可选变量 `FINANCIAL_SCHEDULE_BUDGET`；**若已设置该变量，请将其值设为40**，否则变量值会覆盖默认40。手动运行时可通过 `budget` 调整（程序允许0至50家公司）。定时任务默认调用 **Tushare官方HTTPS API**，不自动切换中转。

**无需手动切换阶段，也无需执行Neon数据库迁移：**

1. **补齐阶段（backlog）**：从最近一次行情筛出的低价、低PB、非ST股票中，识别缺少三张财报、报告期不一致或资产负债表期末超过280天的公司。每次最多40家；同一公司若已有有效报表，只下载缺失的报表种类；失败后至少6小时冷却。已保存完整且报告期一致的公司不再仅因过去30天而重复采集。
2. **实际披露驱动阶段（disclosure_incremental）**：上述缺口清零后，利用2,000积分可调用的 `disclosure_date` 查询最新两个已结束季度，**只接受 `actual_date` 为真实过去披露日期的记录**，不把计划披露日 `pre_date` 视为已披露。与Neon中已有的报告期及公告日比较，只有缺失新报告或有更新披露的公司才触发报表API请求，每次不超过40家；多余公司将在之后的日期继续排队。
3. **周末处理**：周末不调用行情API，复用Neon最后一个交易日的行情选股池；最近行情超过14个自然日会中止财务任务，以防旧名单长期使用。新增报表在下一次正式交易日扫描后才会反映于网站榜单。
4. **安全与验证**：仅使用带可核实披露日期的合并报表；超过 `disclosure_date` 单次6000条上限会停止并报警，防止遗漏。失败任务不覆盖正式选股快照，成功采集的数据继续保留。

**限制**：`disclosure_date`提供计划披露及实际披露日期，不是完整的逐条历史报表更正公告。最新两个季度之外的更正、上游延迟录入或缺失实际披露日期，仍可能需要专项核查，不能保证自动发现所有历史重述。两个工作流共用并发组，避免本仓库内同时占用同一Tushare令牌；仓库外其他任务不受约束。

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
PYTHONPATH=backend python -m value_screener.cli refresh-financials --budget 40
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
