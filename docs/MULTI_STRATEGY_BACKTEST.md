# 价值雷达：多策略与历史回测开发规范

## 2026-11-23：第一阶段（工程实施）

原有每日选股继续只运行 `netnet_v1`。该策略已有生产数据，**没有可信的历史投资回测收益**。多策略框架以 `strategies/catalog.json` 为单一策略注册表：策略ID、版本、生命周期、数据要求、可配置参数（含范围）；网站的策略中心、详情页、回测实验室共享同一个注册表。

新增策略要先登记 `planned`，实现独立的纯Python选股规则和测试后进入 `research`，通过真实历史数据与交易执行验收、代码审核后才能改为 `live`。修改版本不得静默覆盖旧回测记录。不能将未实现的配置草稿作为已经启动的自动选股工作流，也不接受任意用户输入Python/SQL并在服务器执行。

### 1. 现有Neon容量审计（不写数据库）

工作流 `A-Share Neon Capacity Audit` 读取 `pg_database_size` 和每个public表的堆/索引/总大小，仅输出容量统计到GitHub Actions日志。**pg_database_size并不等于Neon提供商的账单配额**；实际容量仍应以Neon控制台为准。此工作流可手动重复执行，不修改权限或表结构。

### 2. 2025年历史OHLCV / Parquet + Neon私有对象存储

已完成2025年工程原型：243个交易日、1,313,898条记录、12个ZSTD Parquet、合计24,164,978字节（2026-11-23 GitHub Actions run 45171163127）。**该次运行尚未配置永久对象存储，原始GitHub Artifact已经删除**。这些压缩文件当前不能直接在云端恢复，成功率与存储金额需要在实际连接后复核。

仓库现已集成Neon Object Storage S3数据面，支持 `check`、`archive`、`restore` 三种手动运行模式：

1. `check`：连接已存在的Neon私有Bucket；真实执行一个小文件写入、下载核对SHA-256、删除，验证Neon分支URL和凭证有效。**不调用Tushare，不写Neon PostgreSQL。**
2. `archive`：先执行 `check`；再提取指定完整年度全市场历史日线到按月ZSTD Parquet，检查数据和本地文件SHA-256、完成工程冒烟测试，然后逐月上传。每份远端文件均进行Head大小+元数据SHA校验和GET实际下载SHA-256校验；最后才发布年度manifest。已发布年度如果再次获得不同的manifest，拒绝静默覆盖。可跳过已验证的月份以恢复中断。
3. `restore`：**不调用Tushare**，从Neon下载年度manifest和12份Parquet，逐一对SHA-256、大小与记录清单进行验证，之后使用恢复的文件执行5/10/20交易日工程冒烟测试。该操作才是长期存储真正可读、可复核的验收条件。

工作流名称保留为 `A-Share 2025 Parquet History Prototype`，但已可以选择已完成的其他年度。所有模式均为手动触发，不在每次推送时自动消耗Tushare配额。**公开仓库仅上传不含行情原文的manifest Artifact**，绝不上传完整市场Parquet或密钥。

#### Neon账户的首次配置（需要用户在Neon及GitHub授权）

**先确认现有Neon PostgreSQL项目所用的分支和区域**。Neon Object Storage不是Postgres表空间：如果账户所在区域未支持该功能，应先停止操作，不擅自新建会产生费用的项目。参考 Neon 文档： https://neon.com/docs/storage/get-started 。

1. 登录Neon Console → 对应项目 → **Object Storage / Buckets**，在与现有数据库一致的分支创建Bucket `a-share-backtest-private`，访问类型选 `private`，不可使用 `public_read`。
2. **Connect → Storage** 复制完整分支S3 Endpoint（`https://br-....storage.c-....REGION.aws.neon.tech`）及地区。**Settings → Credentials** 为该分支单独生成仅含 `storage:read` 和 `storage:write` 的凭证；`token_id` 映射S3 Access Key ID，`s3_secret_access_key` 映射S3 Secret Access Key。不要在ChatGPT、README、Actions日志或前端页面粘贴密钥。
3. 在GitHub仓库 **Settings → Secrets and variables → Actions → Secrets** 设置：
   - `NEON_STORAGE_ENDPOINT`：完整HTTPS分支Endpoint
   - `NEON_STORAGE_ACCESS_KEY_ID`：Neon提供的 `token_id`
   - `NEON_STORAGE_SECRET_ACCESS_KEY`：Neon提供的 `s3_secret_access_key`
4. 在 **Actions → Variables** 设置：
   - `NEON_STORAGE_BUCKET=a-share-backtest-private`
   - `NEON_STORAGE_REGION`：Neon给出的区域（例如 `us-east-2`）；留空则从Endpoint提取
   - `NEON_STORAGE_PRIVATE_CONFIRMED=true`：**仅在Neon控制台确认Bucket访问类型确为private后**设置。此变量是人工确认，代码不能通过普通S3 Head操作核验Neon的Bucket控制面权限。
5. GitHub → Actions → **A-Share 2025 Parquet History Prototype** → Run workflow：先选择 `operation=check`；成功后选择 `operation=archive, year=2025`。只有日志出现 `NEON_STORAGE_STATUS uploaded_and_verified` 才算真正归档；再选择 `operation=restore, year=2025`，必须出现 `NEON_STORAGE_STATUS restored_and_verified` 并完成 `BACKTEST_ENGINEERING_SMOKE`。
6. 不要同时将真实S3密钥或Neon数据库连接密码加入Vercel客户端环境变量。这些仓库Secrets只供GitHub Actions Python服务端任务使用。

代码的S3客户端强制**path-style寻址**、HTTPS分支Endpoint、签名V4，并固定历史前缀 `a-share-backtest/v1`。2025年度结构为：
```
a-share-backtest-private/
  a-share-backtest/v1/prices/year=2025/month=01.parquet
  ...
  a-share-backtest/v1/prices/year=2025/month=12.parquet
  a-share-backtest/v1/prices/year=2025/manifest.json
```
manifest永远最后上传；所有12个月必须验证通过才会发布，避免使用部分历史数据。不同版本的数据集不应该覆写v1，需要明确迁移和版本控制。真正扩大到2020—2026年前，还需要补齐复权、历史ST及历史披露财报。

### 3. 策略无关交易内核

`backend/value_screener/backtest_engine.py` 包含通用的日线策略执行内核。
- T日**收盘**产生信号、T+1开盘尝试买入；同一天不能按收盘信号在开盘成交。
- 按交易日持仓5/10/20日；T+1限制，同日不得先买后卖；遇停牌/无成交量不强制成交；保守地不在涨停开盘买入、不在跌停开盘卖出。
- 默认要求历史涨跌停价格，不提供时不可执行；工程冒烟测试使用 `strict_tradeability=False`，结果被明确标记为非正式历史业绩。
- A股整手数量、最大持仓、现金、买卖费用、税费、滑点均以参数控制。默认佣金/税费只是**工程假设**，不是最新法规报价；正式交易费用需按历史日期核验。
- 期末未平仓按最后收盘价估值，不在不存在的未来交易日强制卖出；无行情时沿用上日价格并输出过期估值计数。
- CI使用合成价测试T+1、限价阻断、停牌、无前视、整手、现金不透支及指标。
- `backtest_smoke.py`在2025真实Parquet上，仅验证5/10/20日回测内核的运行一致性，**不把未复权、缺ST/停牌/涨跌停数据的示例结果发布到网站**。

### 4. 正式多策略上线前置条件

- 2020年至今真实的历史股票池（包含已经退市的证券）；历史ST状态按时点获取（当前Tushare `stock_st` 权限不足），不能用今天的ST状态倒填2020年。
- 复权与分红、停牌、涨跌停、每笔成本与A股T+1、股票池交易限制均有可追溯数据。
- 对财务策略，2020年至今的财报原始数据须保存实际披露日期、合并口径及历史修订版本；**不能将当前Neon上的最新报表直接投射到过去**。
- 回测结果按strategy_id、version、params、dataset_manifest_sha、代码版本、费用假设和日期范围保存，才能复核重跑；未来采用独立的 `backtest_runs`、`backtest_trades`、`backtest_equity` 表，审查容量后再创建。
- 数据质量不通过就标记为不可发布，不展示“收益很好”的模拟图。回测进程独立于Net-Net每日生产任务，失败不影响现有快照。

### 5. 后续开发

首先在Neon控制台核对容量审计值并决定是否具备保留近期行情的安全余量；其次将2025已校验Parquet接入私有对象存储并演练下载校验；之后分批补2020—2024及2026历史OHLCV/复权、限制、退市和ST历史；最后开发支持策略配置、真实历史筛选、排名、组合回测、历史结果可追溯的正式回测页面。
