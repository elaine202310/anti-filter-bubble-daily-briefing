# Anti-Filter-Bubble Daily Briefing Pipeline

每天读取固定的 6 个 RSS/Atom 源，用规范化 URL 的 SHA-256 键去重，调用 Gemini 分类、摘要、评分，再生成带非偏好主题配额的简报。项目包含可验证的本机 SQLite 演示，以及可部署到 Vercel 的 Flask + PostgreSQL 订阅网站。

## 数据流与文件

`sources.json` → `parse_feed` → `data/live.sqlite3` → `GeminiClient` → 本地 JSON Schema 字段校验 → `rank_interest` / `rank_diverse` → `output/digest.md` → 邮件预览或 Gmail SMTP。

- `src/briefing_pipeline/core.py`：RSS/Atom 解析、UTC 时间、SQLite、排序和邮件。
- `src/briefing_pipeline/gemini.py`：Gemini REST 结构化输出、超时和有限重试。
- `src/briefing_pipeline/pipeline.py`：真实运行、每次运行报告、发送防重和盲标导出。
- `src/briefing_pipeline/evaluation.py`：通过文章 ID 连接人工评分和 SQLite 中真实模型预测。
- `src/briefing_pipeline/cli.py`：命令入口；`demo` 使用独立数据库和固定离线样本。
- `src/briefing_pipeline/dashboard.py`：本地网页，显示简报、来源、处理状态和评测结果，也可保存每日偏好类别。
- `src/briefing_pipeline/preferences.py`：验证并保存用户偏好，供网页和每日流水线共同读取。
- `src/briefing_pipeline/subscription.py`：单个本地订阅者的邮箱、每日同意状态和逐地址发送记录；凭据仍只在 `.env`。

本机版本支持 Python 3.9+ 且只依赖标准库。云端版本使用 Python 3.12、Flask 和 `psycopg`；运行时依赖在 `requirements.txt`。`.env`、数据库、日志和输出被 Git 忽略。

## 配置

```sh
cp .env.example .env
chmod 600 .env
```

在 `.env` 设置 `GEMINI_API_KEY`。当前已验证可用的具体模型为 `gemini-3.5-flash-lite`，调用的是 Google `generateContent` 接口。密钥只保存在本地，不要放进代码或聊天。配置 `MAX_ARTICLES_PER_RUN=3` 适合初试；每天实际需要处理的文章数可能超过免费额度，运行报告会记录处理数量和失败原因。每次模型请求隔 4 秒，出现认证、限流或超时错误会停止本轮后续模型调用，已有文章与运行报告仍保留。

发件和收件默认使用 `jiaohu368@gmail.com`。Gmail SMTP 需要 Google 应用专用密码；[Google 官方说明](https://support.google.com/mail/answer/185833)要求先启用两步验证。不要使用常规 Google 密码。准备好应用专用密码后在终端运行：

```sh
python3 scripts/configure_gmail.py
```

密码以隐藏输入写入本地 `.env`。配置脚本保持预览模式，真实发送只由独立 `send` 命令触发。Gmail 使用 `smtp.gmail.com:465` 和 SSL；SMTP 服务器接受消息代表提交成功，收件箱是否收到还需查看 Gmail。

## 运行和验证

本地网页在项目目录运行以下命令，然后打开 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)：

```sh
PYTHONPATH=src python3 -m briefing_pipeline.cli dashboard
```

网页只绑定 `127.0.0.1`。在“选择每日偏好类别”中勾选 1–8 个主题并保存，设置写入 `data/preferences.json`，下次 `run` 会使用它；保存时仅用已有模型结果重排简报预览，不调用模型，也不发送邮件。保留至少 20% 非偏好主题的规则不变，候选不足时显示实际比例。关闭终端会停止页面服务。可用 `--port 8766` 更换端口。

“邮件订阅”可填写收件邮箱。点“保存订阅设置”只保存地址与每日接收选择；点“确认并发送当前简报”才尝试真实 SMTP 发送，并在成功提交后保存设置。每日接收须单独勾选，取消按钮可关闭每日接收。订阅数据保存在被 Git 忽略的 `data/subscription.json`；发送记录存于 SQLite `subscription_deliveries`，同一天同一地址只尝试一次。旧的 CLI `send` 仍使用 `.env` 的 `SMTP_TO`，供手动维护；每日脚本改用 `send-subscribed`，未同意每日接收时会跳过发送。当前 macOS 后台任务仍受系统权限阻止，页面也明确提示；因此勾选每日接收不代表已经验证自动送达。

旧简报过期时，先点“生成今日简报（RSS + Gemini）”读取六个源并调用已配置的模型，再预览和发送。该按钮可能消耗 API 免费额度；页面保存设置和预览不会调用模型。

本机版是**单用户本地演示**。Vercel 版按邮箱分别保存偏好和每日接收选择，使用 PostgreSQL 持久化文章、订阅者和发送记录；它仍未实现邮箱验证或限流，因此应只用于作品集演示，不能作为面向公众的大规模邮件产品。

```sh
PYTHONPATH=src python3 -m briefing_pipeline.cli collect
PYTHONPATH=src python3 -m briefing_pipeline.cli run
PYTHONPATH=src python3 -m briefing_pipeline.cli send
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`collect` 只抓取并去重；`run` 抓取、处理最多 `MAX_ARTICLES_PER_RUN` 篇新文章、生成简报和邮件预览；`send` 只发送当天非空的真实模型简报，数据库中的 `deliveries` 防止当天重复提交。定时脚本 `scripts/daily.sh` 默认只生成预览；设置 `BRIEFING_SEND_EMAIL=true` 才会尝试真实发送，可交给 macOS `launchd` 或 cron 按日调度。2026-10-28 的真实发送已被 Gmail SMTP 接受，并在 Gmail 收件箱中核对了邮件和正文。

## Vercel deployment

The cloud deployment replaces the local database, local web server and macOS scheduler:

`Vercel Flask app` → `PostgreSQL` → `Gemini API` → `Gmail SMTP`; `Vercel Cron` calls `/api/cron` once each day and sends a personalized quota-based digest to each opted-in address.

1. Create a free PostgreSQL database through the Vercel Marketplace, for example Neon, and connect it to the Vercel project. It injects `DATABASE_URL` into the project.
2. Push this repository to GitHub, import it in Vercel, and set `GEMINI_API_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`, `DATABASE_URL`, `CRON_SECRET`, `ADMIN_SECRET`, and `PUBLIC_BASE_URL` in Vercel Project Settings → Environment Variables. Keep all values secret.
3. Deploy. The `app.py` Flask entry point creates tables on first use. Visit `/api/health` to check that the deployed function starts, then make an authenticated `POST /api/run` with `Authorization: Bearer <ADMIN_SECRET>` to collect the first cloud digest.
4. `vercel.json` schedules `/api/cron` at `21:00 UTC` daily, which is 07:00 Sydney time during standard time and 08:00 during daylight saving. Vercel Hobby Cron runs once per day and may start at any time during that UTC hour; use a paid plan if minute-level timing is required. Vercel supplies `Authorization: Bearer <CRON_SECRET>` to the Cron request.

The cloud app supports 1–8 category choices, a separately recorded daily-delivery choice, one manual send attempt per address per UTC day, and a cancellation link in each email. SMTP acceptance is recorded as `accepted`; it is not evidence that the recipient's inbox received the email. The Vercel path is prepared and unit-tested locally, but it has **not yet been deployed or connected to a cloud database**, so cloud collection and scheduled email delivery are not yet verified.

`output/collect_report.json` 和 `output/run_report.json` 记录每个源的抓取条数、模型成功和失败数、简报条数、邮件状态。`output/digest.md` 是最终简报；`output/email_preview.txt` 是可读预览；`output/ranking_comparison.json` 记录两种排序的候选和比例。重新运行时，已处理文章不会再次调用模型。失败条目需要在问题修复后显式执行 `python3 scripts/retry_failed.py` 才会重试。

## 兴趣排序与多样性配额

默认偏好主题为 `technology, science, politics`。兴趣分数 = 重要性分数（1–5）+ 偏好主题加分 1，同分按发布时间排序。多样性算法先取兴趣排序前 `DIGEST_SIZE` 篇，再以候选池中最高分的非偏好主题替换末位偏好主题，直到非偏好主题至少占 `ceil(简报条数×20%)`；候选不足时只能使用已有数量。`ranking_comparison.json` 的真实结果取决于当天候选池，程序不会写死 10%→20%。离线固定样本与真实 RSS 数据分别存放于 `data/demo.sqlite3` 和 `data/live.sqlite3`。

## 30 篇人工评测

评分量表：1 = 影响很小的单点更新；2 = 有限范围的重要更新；3 = 对相关群体有明显影响；4 = 影响范围较广；5 = 重大且广泛影响。人工标注者只看标题、链接、简介，独立填写 `human_importance`，不看模型分数。执行：

```sh
PYTHONPATH=src python3 -m briefing_pipeline.cli export-labels
# 在 output/human_labels.csv 中填写恰好 30 条人工评分
PYTHONPATH=src python3 -m briefing_pipeline.cli evaluate --csv output/human_labels.csv
```

导出只选已有真实模型预测的 30 篇文章，使用稳定文章 ID。评测命令通过该 ID 从 `data/live.sqlite3` 读取真实模型评分，拒绝空标签、缺少模型预测、混用不同模型；输出 `output/evaluation.json`，包含标签文件 SHA-256、逐篇对照、exact agreement（完全相同数/30）和 within ±1 point（分差不超过 1 的数量/30）。人工标签尚未填写时，不能报告准确率。五条合成测试向量只验证指标计算。

## 本地演示与已知限制

`PYTHONPATH=src python3 -m briefing_pipeline.cli demo` 处理固定的 19 篇合成文章，仅用关键词启发式模拟分析，不代表 Gemini 质量。正式简报必须以 `run` 的真实 RSS 和 Gemini 结果为证据。只读取 RSS 中的标题与简介，没有抓取全文；不同媒体更新频率和发布时间缺失会影响每日覆盖率。当前去重针对规范化后的同一 URL，跨媒体转载或不同 URL 的同文尚未做语义去重。免费 Gemini 档的额度会变化，按 [Google 官方限制页面](https://ai.google.dev/gemini-api/docs/rate-limits)和账号显示为准；免费档的提交内容可能用于改进服务，见[官方价格与数据说明](https://ai.google.dev/gemini-api/docs/pricing)。只向模型发送公开 RSS 标题和简介。

## 每日自动运行

Gmail 应用专用密码配置并完成一次真实发送验证后，可执行 `python3 scripts/install_daily_schedule.py`。它在 macOS 的 `~/Library/LaunchAgents/` 安装每天本地时间 **07:00** 运行的任务；任务执行 `run` 后再执行 `send`。本机已于 2026-10-28 安装并由 `launchctl print` 确认加载，但 **2026-10-29 首次自动运行失败**：macOS 拒绝 LaunchAgent 访问 `Documents` 下的项目，`logs/launchd.stderr.log` 记录 `Operation not permitted`，退出码为 126。自动交付目前未验证成功；需要将运行目录移出受保护的 `Documents`，或由用户在 macOS 隐私设置中授权相关进程访问。Mac 关机或睡眠时能否补跑取决于 `launchd` 行为，勿将它当成云端全年运行服务。发送状态记录在 SQLite `deliveries` 表，日志位于 `logs/launchd.*.log`。

本次实际运行证据和简历逐条核对见 `docs/verification.md`。
