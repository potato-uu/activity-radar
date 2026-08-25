# BD 活动雷达 v2 进度

## 2026-08-18 Step 0 - Blocked

- 产物：完成执行简报、README、Phase 0 Gate、`config/`、`src/activity_radar/` 的完整读取；确认基线有 12 个已修改文件、两个应忽略的未跟踪目录。
- 命令：`git status --short --branch`、`git diff --stat`、`git diff --name-only`。
- 结果：分支为 `codex/activity-radar-v1`，相对跟踪分支 ahead 4；12 个基线改动与简报一致。
- 已做：将 `.playwright-mcp/` 和 `大正咖啡集团-*/` 加入 `.gitignore`；两个目录本身未提交。
- 阻塞命令：`git add -- .env.example .github/workflows/radar.yml README.md data/events.jsonl data/source-health.json logs/run.jsonl site/index.html src/activity_radar/cli.py src/activity_radar/config.py src/activity_radar/provider.py src/activity_radar/research.py tests/test_pipeline.py`
- 实际结果：`fatal: Unable to create '/Users/stan/Documents/Stan BD商务/.git/index.lock': Operation not permitted`。
- 判定：当前会话对 `.git/` 只有读取权限，无法完成简报要求的基线提交，也无法随后执行 `git pull --rebase origin main`。执行顺序要求先完成这两项；跳过会让 v2 基于未同步的历史开发，属于未经授权的偏离，因此停止。

## Blocked/Deviations

- Blocked：`.git/` 只读，Step 0 的精确 stage、commit、pull/rebase 均无法执行。
- Deviation：仅完成 `.gitignore` 工作区修改；没有提交、没有 pull、没有安装依赖、没有修改 v2 实现、没有运行真实源、没有发送消息、没有加载 LaunchAgent、没有 push。
- 续跑条件：用允许写入本仓库 `.git/` 的会话继续，并从上述精确 `git add` 命令开始；成功提交 `chore: keep v1 local improvements` 后执行 `git pull --rebase origin main`，再进入 Step 1。

## Report

### 产出（文件清单）

- `PROGRESS-v2.md`：本次阻塞证据、状态和续跑入口。
- `.gitignore`：新增 `.playwright-mcp/` 与 `大正咖啡集团-*/` 忽略规则。

### 怎么跑（命令）

```bash
git add -- .env.example .github/workflows/radar.yml README.md data/events.jsonl data/source-health.json logs/run.jsonl site/index.html src/activity_radar/cli.py src/activity_radar/config.py src/activity_radar/provider.py src/activity_radar/research.py tests/test_pipeline.py
git commit -m "chore: keep v1 local improvements"
git pull --rebase origin main
```

### 副作用

- LaunchAgent：未创建、未加载。
- Actions：未做 v2 调度修改。
- 依赖：未安装。
- 外部副作用：无；未发送微信、未 git push、未改仓库可见性。

### 验收 A1-A9

| 项 | 状态 | 证据 |
|---|---|---|
| A1 单测/编译/diff | Not tested | Step 0 在基线提交前被权限阻塞。 |
| A2 全量真实运行 | Not tested | 未进入 Step 1。 |
| A3 召回 | Not tested | 未进入适配器实现。 |
| A4 OnePilot 回溯 | Not tested | 未进入适配器实现。 |
| A5 去重/diff | Not tested | 未进入规则实现。 |
| A6 full/delta/发送 | Not tested | 未进入推送实现；未外发。 |
| A7 Actions/LaunchAgent | Not tested | 未进入调度实现。 |
| A8 成本 | Not tested | 未运行 `llm_sweep`。 |
| A9 源健康 | Not tested | 未运行 v2 源。 |

### Blocked & Deviations

- 唯一硬阻塞是当前会话无法写 `.git/index.lock`；这使简报指定的第一项必要操作不可执行。
- 没有绕过执行顺序，也没有把未测试状态写成完成。

### 下一步建议

- 在对仓库 `.git/` 有写权限的会话中，从 Step 0 的精确 stage/commit/pull 开始续跑；完成后严格按简报 Step 1-6 继续。

## 2026-08-18 20:46 PDT - v2 续跑起点

- 依据：完整重读 `BRIEF-v2-2026-08-18.md`，特别是 §8；确认 Step 0 已由 Claude 外层完成，本轮从 §4 Step 1 开始。
- Git 边界：只执行了 `git status --short --branch`、`git diff --stat`、`git diff --name-only`；分支 `main` 相对 `origin/main` ahead 2。未执行 add/commit/rebase/checkout/push 等 git 写操作。
- 工作区起点：保留上一轮 `PROGRESS-v2.md` 阻塞记录；当前仅 `.gitignore` 的 `.pw-browsers/` 规则为已跟踪文件改动，`BRIEF-v2-2026-08-18.md` 与 `PROGRESS-v2.md` 未跟踪。
- 依赖：`.venv/bin/python` 为 Python 3.14.5；已在项目 `.venv` 安装 `pytest 9.1.1`、`playwright 1.62.0`。
- 浏览器：执行 `PLAYWRIGHT_BROWSERS_PATH="$PWD/.pw-browsers" .venv/bin/python -m playwright install chromium` 成功，Chromium/Headless Shell/FFmpeg 均落在项目 `.pw-browsers/`。
- OnePilot 公开能力探测：`robots.txt` 允许 `/`，仅明确禁止 `/admin.html`；`/supabase-config.js` 提供公开匿名只读 Supabase 配置；公开视图 `onepilot_public_events` 返回 HTTP 200、647 条，其中 2026-08-18 起 91 条，并含 `2026-08-12 2026 Google 开发者大会` 与 `2026-11-07 Google Devfest`。未打印任何配置值或密钥。
- 路由决定：OnePilot 采用 `api` 适配器；无需启用 Playwright `rendered` 兜底。

## 2026-08-18 Step 1 - 适配器与归一化实现

- 测试先行：先新增 v2 适配器、中文日期、归一化、去重、系列合并和适配器集成测试；首次运行因缺少 `activity_radar.adapters` / `normalization` 正确失败。实现后定向测试 `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_adapters.py tests/test_normalization.py tests/test_rules.py tests/test_pipeline.py`：`19 passed`。
- 产物：`src/activity_radar/adapters/`（统一 `RawCandidate`/`AdapterWindow`、按 host >=2 秒限速 HTTP、OnePilot API、calendar_seed、注册表）；`src/activity_radar/normalization.py`；`config/annual_calendar.yaml`；`config/sources.yaml` 新增 `onepilot` 与 `calendar-seed`；schema/rules/research/CLI 支持新字段、series、`--sources` 和 120 天窗口；新增测试文件。
- OnePilot 公开原始适配器命令：`PYTHONPATH=src PLAYWRIGHT_BROWSERS_PATH="$PWD/.pw-browsers" .venv/bin/python - <<'PY' ... get_adapter(source).fetch(...) ... PY`。
- 实际结果：OnePilot `90` 条（2026-08-18 至 2026-12-16），包含 `2026 Google Devfest 谷歌开发者节`；calendar-seed `11` 条，包含 `2026 云栖大会`（2026-09-22 至 24，active）、外滩大会、金投赏、进博会等 expected/active 条目。
- 约束：未访问 `/admin.html`，未登录、未绕验证码；匿名 Supabase 值只在进程内使用，未打印/落盘。
- 真实双源运行：`PYTHONPATH=src PLAYWRIGHT_BROWSERS_PATH="$PWD/.pw-browsers" .venv/bin/python -m activity_radar.cli run --live --sources onepilot,calendar-seed`，退出码 0。`source_hits=[calendar-seed, onepilot]`，`unscored_candidate_count=101`，`source_errors=[]`。
- LLM 阻塞：首次真实运行在打分阶段 HTTP 401；检查结果为项目 `.env` 不存在、有效 host 为 `api.openai.com`、无环境变量凭据、只读 Codex auth fallback 存在但不可用于该端点。回归实现后 scorer 明确记录 `scoring_result=unavailable`，101 条原始候选不入库，`data/events.jsonl` 保持 8 条；未打印任何凭据。

## 2026-08-18 Step 2 - v2 评分、side event 与稳定性

- 测试先行：新增供给侧获客上限、小型开放活动扣分、闭门小局不扣分、周边/其他城市修正、score history/needs_review、side event 关联测试；旧 webinar 断言按 v2 `webinar_in_push=true` 更新。
- 结果：定向套件 `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_rules.py tests/test_pipeline.py tests/test_adapters.py tests/test_normalization.py` 全绿；当前最终套件为 `37 passed`。
- 产物：重写 `config/scoring.yaml` v2 权重/地域/修正；`schema.py` 新增 audience/scale/format/series/side-event/stability 字段；`rules.py` 执行修正、Tier、系列、关联与 review 标记；Responses 评分 temperature=0.2。

## 2026-08-18 Step 3 - 其余源与健康状态

- 产物：通用 `jsonld`、`html_list`、公开 `api`、`rendered`、`wechat_search` 适配器；robots Disallow 检查；多 URL 源单 URL 失败继续其余 URL；36 个 source 配置保留原 v1 id 并补 v2 adapter/URL/selector/region 字段。
- `agent-reach --help` 可用，但 `miku_ai` 导入失败（`ModuleNotFoundError`），因此 `wechat-search=unavailable`，未安装、未硬做。
- 逐源真实结果：OnePilot 90 hit；calendar-seed 11 hit；聚展 10 hit；本地宝 3 hit；Google Developers 2 hit；AWS 1 hit；白鲸 1 hit；雨果 4 hit；Morketing 17 hit；AMZ123 4 hit；品牌星球 2 hit；Eventbrite 17 hit；年度旧源 11 hit。百格/GDG/Volcengine/Aliyun/Baidu Ads/36氪/霞光社/出海同学会/Meetup/SNEC 等按实际 empty；活动行/Luma/10times/Mosu/Meta/OpenAI 等按实际 blocked；HDB/Microsoft/TikTok/Baidu 等按实际 error；WeChat 与 llm-sweep 为 unavailable。
- rendered 根因：最小 Playwright 启动复现为 Chromium `bootstrap_check_in org.chromium.Chromium.MachPortRendezvousServer: Permission denied (1100)`，macOS 沙箱拒绝本地 Chromium 注册；标准 CDP `127.0.0.1:9222/json/version` 返回 404，不能把 web-access 私人代理伪装成 Playwright 运行时。按停止条件将 rendered 源标 blocked，未绕过。
- 全量真实命令：`PYTHONPATH=src PLAYWRIGHT_BROWSERS_PATH="$PWD/.pw-browsers" .venv/bin/python -m activity_radar.cli run --live` 退出码 0；36 源、169 条原始候选、12 个 hit、`scoring_result=unavailable`、`unscored_candidate_count=169`。

## 2026-08-18 Step 4-5 - 推送、调度与时间轴

- 推送：`radar push --mode full` 与 `--mode delta` 均生成 `data/push-latest.txt` 和 `data/push-history/*-{full,delta}.txt`；full 文案含 expected/series/side event/source health，delta 无变化时有明确无新增文案；`logs/push.jsonl` 记录 dry-run 与发送结果。
- A6 唯一发送尝试：`PYTHONPATH=src .venv/bin/python -m activity_radar.cli push --mode full --send` 实际调用 Hermes，退出失败并记录 `status=failed`、`returncode=1`、原因 `iLink sendmessage rate limited; cooldown active for 30.0s`；没有静默降级，没有重复外发。
- Actions：`.github/workflows/radar.yml` cron 为 `0 8 * * 0`（周日全量）与 `0 0 * * 3`（周三增量），仅改调度与 push mode，未改权限。
- LaunchAgent：新增 `scripts/push_local.sh`、`scripts/com.stan.activity-radar.push.plist`、`scripts/install_launchagent.sh`、`scripts/uninstall_launchagent.sh`；zsh 语法与 `plutil -lint` 通过。按 §8 未写 `~/Library/LaunchAgents/`、未执行 bootstrap；A7 实际加载留给 Claude 外层。
- 时间轴：沿用原单文件视觉，新增上海/周边/其他筛选、`side_event`、expected、系列 occurrences、related_to、needs_review 展示；渲染测试通过。

## 2026-08-18 Step 6 - 验收证据

- A1：Passed。`PYTHONPATH=src .venv/bin/python -m pytest -q -rA` 为 `37 passed`；`python3 -m compileall -q src` 退出 0；`git diff --check` 退出 0。
- A2：Failed（阻塞披露）。全量 run 退出 0，但因 scorer 401 未入库新候选；`data/events.jsonl` 仅 5 条未来 120 天上海/周边 active/expected/changed，Tier A/B=2，D=0；要求为 >=40、>=8。
- A3：Failed（阻塞披露）。原始适配器层已观察到 5/5 目标：OnePilot 含 Google DevFest，calendar seed 含云栖、外滩、进博会、金投赏；但 scorer unavailable，不能把未评分 raw 候选写入 events，当前 events 仅已有外滩一条，未达到 >=4/5 的入库标准。
- A4：Failed（阻塞披露）。`... cli backtest --live-source onepilot --as-of 2026-07-01` 真实返回退出码 1、OnePilot raw=513、`captured=false`，原因 scorer HTTP 401；没有使用 fixture 冒充 live backtest。
- A5：Passed。规则测试覆盖重复/日期 changed/系列；生产连续运行前后 `data/events.jsonl` SHA-256 均为 `3ef266a86b54ab12c7165c10370cc77601f03ee1cdf6503391a19578b7930eeb`，事件数 5，ShanghAI 四个日期已折叠为一条 series。
- A6：Passed（发送结果本身 Failed 已如实记录）。full/delta 样张均落盘；唯一 `--send` 尝试写入失败日志，未静默、未重复发送。
- A7：Not tested。cron 文件、plist、脚本语法/结构通过；按 §8 不执行 `launchctl bootstrap`，也不声称 `launchctl print` 已加载。
- A8：Failed（历史缺口）。新写入的 adapter/error/scoring/run-summary 记录包含 usage 与 `llm_sweep_input_token_cap=300000`，当前新记录无超限；但历史 `logs/run.jsonl` 共 250 条中有 139 条旧记录没有 usage，不能把全文件宣称通过。
- A9：Passed。配置 36 个 source，`data/source-health.json` 36 行均有合法 `last_result`（hit/empty/timeout/error/unavailable/blocked）和 `reason`；现场计数 hit=13、empty=11、blocked=6、error=4、unavailable=2、timeout=0。

## Blocked/Deviations - 2026-08-18 v2

- Blocked：Responses API 对只读 fallback 返回 HTTP 401；项目 `.env` 不存在，未读取或打印任何密钥值。打分、A2 新入库、A3 入库召回、A4 live 回测均因此未完成。
- Blocked：本沙箱 Chromium 启动被 macOS MachPort 权限拒绝；rendered 源未绕过，按 blocked 记录。
- Blocked：LaunchAgent 的实际加载是外层 Claude 权限边界；本轮只写仓库 `scripts/` 文件。
- Deviation：未执行任何 git 写操作；未 add/commit/rebase/checkout/push。工作区保留已有 `.gitignore` 改动及本轮新文件，提交由 Claude 复核代做。
- Deviation：A8 历史日志缺 usage；未重写或删除历史日志来制造通过证据。

## Report

### 产出（文件清单）

- 发现层：`src/activity_radar/adapters/`、`src/activity_radar/normalization.py`、`config/annual_calendar.yaml`、`config/sources.yaml`。
- 规则/管线：`src/activity_radar/schema.py`、`rules.py`、`research.py`、`provider.py`、`config.py`、`cli.py`、`push.py`、`render.py`。
- 调度/文档：`.github/workflows/radar.yml`、`scripts/` 四个文件、`README.md`、`pyproject.toml`、`LESSONS.md`。
- 测试：`tests/test_adapters.py`、`tests/test_normalization.py`、`tests/test_rules.py`、`tests/test_pipeline.py`；运行时产物：`data/events.jsonl`、`data/source-health.json`、`data/push-latest.txt`、`data/push-history/`、`site/index.html`、`logs/run.jsonl`、`logs/push.jsonl`。
- 依赖：项目 `.venv` 安装 pytest/playwright/beautifulsoup4；浏览器安装在 `.pw-browsers/`，已被 `.gitignore` 忽略。

### 怎么跑（命令）

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q -rA
python3 -m compileall -q src
git diff --check
PLAYWRIGHT_BROWSERS_PATH="$PWD/.pw-browsers" PYTHONPATH=src .venv/bin/python -m activity_radar.cli run --live
PYTHONPATH=src .venv/bin/python -m activity_radar.cli push --mode full
PYTHONPATH=src .venv/bin/python -m activity_radar.cli push --mode delta
```

### 副作用

- LaunchAgent：仅写入仓库 `scripts/`，未复制到用户目录，未 bootstrap/bootout。
- Actions：仅调度 cron 与 push mode 选择；未改变仓库权限。
- 外发：唯一 `--send` 失败并写 `logs/push.jsonl`；没有成功送达证据。
- Git/远端：没有任何 git 写操作、没有 git push、没有改变仓库可见性。

### 应提交文件清单与建议提交信息（由 Claude 外层执行）

- 建议提交信息：`feat: rebuild activity radar discovery layer v2`
- 文件范围：本 Report 的全部产出文件，排除 `.pw-browsers/`、`.env`、临时 `/tmp` 文件；不要 `git add -A`，由 Claude 复核后精确 stage。

## 2026-08-18 修复轮进行中 - F1-F7

- F1：`RadarConfig.load()` 已在读取环境字段前调用 `load_local_env(root)`；新增测试验证临时 root 的 `.env` 能覆盖 `CODEX_BASE_URL` 与 `RADAR_MODEL`。未读取或打印密钥值。
- F2：评分改为最多 15 条/批、最多 3 个并发；批次独立失败并保留 pending；新增 `data/candidates-latest.jsonl`、`data/candidates-unscored.jsonl` 与 `radar score --pending`。批次并发/失败隔离测试已通过。
- F3：`is_series` 不再直接采信 LLM 字段；只有规则层合并同名同城多日期后设置 `series_rule`、`occurrences`。单日期年度大会回归测试已通过。
- F4：expected 日历种子与同城同月/±45 天名称核心词重叠的确切条目合并，保留确切日期与来源并写入 `metadata.seed_id`；外滩大会案例测试已通过。
- F5：月精度显示改为 `YYYY年M月（日期待官宣）`；expected 月份参与未来 4 周判断；side event 文案过滤为 A 或多日 B；健康度按 `no_hit_runs >= 4`，首次空源单独提示。定向测试已通过。
- F6：plist 改为每小时 `Minute=5`；`push_local.sh` 改调 `radar push --auto --send`；auto 按 `Asia/Shanghai` 计算 full/delta/skip 并支持幂等检查。三种时刻测试已通过。
- F7：Hermes 使用 `shutil.which()`，再 fallback 到 `~/.hermes/hermes-agent/venv/bin/hermes`；找不到时显式写失败日志。路径缺失测试已通过。
- 阶段验证：`PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_repairs.py tests/test_rules.py tests/test_pipeline.py` → `37 passed`。

## 修复轮 Report

时间：2026-08-18 23:20 PDT

### 结论

- F1-F10 均已按修复轮范围实现并配最小回归测试；最终全套为 `53 passed in 0.38s`。
- 当前 `data/events.jsonl` 为 13 条：假单日期 series=0；ShanghAI 四日期系列保留；外滩大会只剩 `2026-09-09` 确切条目且 `metadata.seed_id=inclusion-bund-conference`；虹桥论坛已按官网单日事实移出 side event。
- 未执行任何 git 写操作，未启动 Chromium，未成功外发消息，未打印密钥。

### F1-F10 验收

| 编号 | 状态 | 证据 |
|---|---|---|
| F1 `.env` 加载顺序 | Passed | `RadarConfig.load()` 首行先调用幂等 `load_local_env(root)`；allowlist 包含 `CODEX_BASE_URL`、`RADAR_MODEL`、`RADAR_PUSH_TARGET` 及运行参数。`test_config_loads_local_env_before_environment_resolution` 通过；现场布尔检查 `env_base_url_loaded=True`，未输出 URL 或密钥。 |
| F2 分批评分/续跑 | Passed | 每批最多 15 条、并发最多 3；每批独立重试/超时，失败或 Responses 漏项只保留该批/该候选为 pending。run 写 `data/candidates-latest.jsonl` 和 `data/candidates-unscored.jsonl`；`radar score --pending` 补评分并合并入库；成功/失败批次均写 `score_batch` usage。覆盖有界并发、失败隔离、部分响应、逐批 usage、候选落盘、pending CLI 合并。真实 LLM 批量调用本轮未执行。 |
| F3 系列误标 | Passed | `prepare_event()` 忽略 LLM `is_series`；仅 `_collapse_series()` 在同名同城存在 >=2 个日期时设置规则态。数据检查 `false_single_series=0`；真实 ShanghAI occurrences 四日期保留。 |
| F4 种子/真实去重 | Passed | expected/calendar seed 与同城同月或 +/-45 天、核心名称包含的确切条目合并；保留确切日期、确切来源和 `active`，写 `metadata.seed_id`。覆盖候选同批和历史已存重复两种路径；当前外滩大会由 2 条收敛为 1 条。 |
| F5 推送文案 | Passed | 月精度显示 `YYYY年M月（日期待官宣）`；月份交集参与未来 4 周；side event 只保留 A 或多日 B；连续无 hit 严格用 `no_hit_runs >= 4`；新增 `scan_count` 识别真正首次空扫描。`data/push-latest.txt` 已重生成。虹桥论坛官网明确写 2026-08-19 单日，历史 `date_end` 已修正为同日，side event=false。 |
| F6 上海时间 LaunchAgent | Passed | plist 改为每小时 `Minute=5`；`push_local.sh` 调 `radar push --auto --send`；`auto_mode(now)` 按 `Asia/Shanghai` 覆盖周日 18 点 full、周三 10 点 delta、其它 skip；成功后写 `data/push-history/YYYY-MM-DD-mode.success` 幂等标记。`plutil -lint`、`zsh -n` 和三时刻/幂等测试通过。未加载 LaunchAgent，留给外层。 |
| F7 Hermes 路径 | Passed | 先 `shutil.which("hermes")`，再查 `~/.hermes/hermes-agent/venv/bin/hermes`；均不存在时写 `logs/push.jsonl` failed 并显式抛错。隔离 HOME 的路径缺失测试通过。 |
| F8 Actions Playwright | Passed | `.github/workflows/radar.yml` 的 job 设置 `timeout-minutes: 90`；`pip install .` 后执行 `python -m playwright install --with-deps chromium`。静态回归测试通过。 |
| F9 rendered 源 | Passed | `huodongxing`、`luma-shanghai-ai`、`mosu-space`、`10times`、`meetup-shanghai-ai` 均为 `rendered`，各有 `fixtures/rendered/*.html` 解析测试。robots 只读检查：活动行明确允许 `/events`；Luma 普通 user-agent 未见相关禁令；Meetup 普通活动页允许且仅禁 API/feed 等；Mospace/10times 的 robots 被 Cloudflare challenge/403 挡住，未宣称允许，10times 当前保持 blocked 并留外层真实渲染复核。本轮未启动 Chromium。 |
| F10 README 环境说明 | Passed | README 明确 `.env` 只放 `CODEX_BASE_URL` 等非密钥设置，密钥只读 fallback `~/.codex/auth.json`；含 `radar score --pending`、`radar push --auto` 用法。文档断言测试通过。 |

### 最终验证

| 检查 | 状态 | 实际结果 |
|---|---|---|
| 全套单测 | Passed | `PYTHONPATH=src .venv/bin/python -m pytest --override-ini addopts= -q` -> `53 passed in 0.38s`。 |
| 编译 | Passed | `.venv/bin/python -m compileall -q src tests`，退出 0。 |
| diff 空白错误 | Passed | `git diff --check`，退出 0。 |
| LaunchAgent 文件 | Passed | `plutil -lint scripts/com.stan.activity-radar.push.plist` -> `OK`；四个 shell 脚本 `zsh -n` 退出 0。 |
| CLI | Passed | help 中存在 `radar score --pending` 与 `radar push --auto`。 |
| 当前数据 | Passed | events=13、假单日期 series=0、外滩大会确切条目=1、虹桥 side_event=false。 |

### 产出与应提交文件

- 核心代码：`src/activity_radar/config.py`、`provider.py`、`research.py`、`rules.py`、`push.py`、`cli.py`、`normalization.py`。
- 配置/调度/文档：`config/sources.yaml`、`.github/workflows/radar.yml`、`scripts/com.stan.activity-radar.push.plist`、`scripts/push_local.sh`、`README.md`。
- fixtures/测试：`fixtures/rendered/huodongxing.html`、`luma-shanghai-ai.html`、`mosu-space.html`、`10times.html`、`meetup-shanghai-ai.html`、`tests/test_repairs.py`、`tests/test_rules.py`、`tests/test_pipeline.py`。
- 复核产物：`data/events.jsonl`、`data/push-latest.txt`、本轮 `data/push-history/*-full.txt`、`site/index.html`、`logs/push.jsonl`、`PROGRESS-v2.md`。
- 建议提交信息：`fix: harden activity radar v2 repair round`。
- 排除：`.env`、`.pw-browsers/`、临时 pytest 目录；不要 `git add -A`，由 Claude 外层精确 stage。

### Blocked / Deviations / 未测试

- 按沙箱约束未运行真实 Chromium rendered 验收、未安装/加载 LaunchAgent；这两项由 Claude 外层执行。
- 本轮未执行真实 Responses 批量评分，F2 由隔离 fake client 和 CLI 集成测试验证；下一次 live run 才会生成项目根的 candidates latest/pending 文件并提供真实逐批 usage。
- Mospace 与 10times 的 `robots.txt` 被 Cloudflare challenge/403 拦截，规则为 Unknown；未绕过验证码或保护。10times 当前源健康仍为 blocked。
- 偏差：F7 首次失败测试只 mock 了 `shutil.which()`，但本机 fallback 可执行文件真实存在，因此意外调用了一次 Hermes。调用被 iLink rate limit 拒绝，没有成功送达；随后测试改为隔离 `Path.home()`，最终全套不会触发真实 Hermes。除此之外没有发送尝试。
- 只执行 `git status`、`git diff`、`git diff --check` 等只读命令；未 add/commit/rebase/checkout/push，未改仓库可见性。
- 只读联网访问了 5 个公开 `robots.txt` 和虹桥论坛公开活动页；未登录、未启动浏览器、未写外部系统。

## 2026-08-19 数据质量轮进行中 - G1-G7

- G1/G2：已新增打分前候选过滤、标题黑名单、无日期/短标题淘汰、标题/地点优先城市识别，以及上海/周边/官方其他城市/海外的范围预过滤与计数；Google/AWS selector 使用本地 HTML fixture 回归。
- G3/G4：已扩展培训词，开放小型沙龙双分封顶 7；加入真实实训/Drink Chat fixture；跨源同城同日以显著 token 去重，保留信息更完整记录并写 `metadata.merged_from`。
- G5：Morketing/品牌星球改为活动栏目 URL + 活动卡片 selector；文章型 fixture 必须返回空，并为 source health 提供明确 empty 原因。
- G6：评分批失败后递归对半拆分至单条；单条失败写 `unscorable_reason`，其余候选继续评分。
- G7：推送 A/B 入口复用 G1 校验，full 条目增加 `类型·城市·格式`；时间轴过滤无效条目。未外发、未启动 Chromium、未执行 git 写操作。
- 下一步：运行定向测试并修复回归，再清理现有 `data/events.jsonl`、重建 dry-run 推送/网页，最后跑全套验证。

## 数据质量轮 Report

时间：2026-08-19（本地执行；未启动 Chromium、未外发、未执行 git 写操作）

### G1–G7

| 项 | 状态 | 实现与证据 |
|---|---|---|
| G1 垃圾候选过滤 | Passed | `rules.is_valid_candidate()` 在打分前拒绝无日期、短标题和 `config/scoring.yaml:title_blacklist` 命中项；`merge_events()`、推送和时间轴再次 fail-closed。新增 Google/AWS 活动卡片 fixture，导航骨架不再被抓取。当前 `data/events.jsonl` 23 条，0 条无日期/导航/报名中。 |
| G2 城市与范围预过滤 | Passed | `normalization.infer_city()` 以标题/地点优先识别深圳等国内城市并归一化上海区县；`prefilter_candidates()` 在评分前保留上海/周边，其他国内仅官方源/种子，海外丢弃并计数。回归覆盖 `eMAG...深圳` 与官方深圳候选。 |
| G3 小局/培训评分 | Passed | 培训正则扩至课程、培训、训练营、实训、公开课、体验课、workshop、bootcamp、training；开放小型/未知规模沙龙获客与资源各封顶 7，闭门/邀约不封顶。实训与 Drink Chat fixture 均 `<=B`；实训当前为 D、Drink Chat 为 B。 |
| G4 跨源去重 | Passed | 名称 token 拉丁统一小写、去符号/emoji、排除城市和通用词；同城、日期 ±1 天、显著 token 重叠即合并，按信息完整度保留并写 `metadata.merged_from`。DevFest 回归合并为 1 条并保留 `onepilot` 详细记录。 |
| G5 文章误抓 | Passed | Morketing/品牌星球切换到 `/events` 活动栏目与活动卡片 selector；文章 fixture 返回空并写明确 empty 原因，不再回退首页文章。 |
| G6 失败批次拆分 | Passed | `_score_candidates_in_batches()` 失败后递归对半拆分至单条；单条仍失败写 `unscorable_reason` 到 `data/candidates-unscored.jsonl`，其他条目继续评分。15 条 1 毒丸 fake-client 回归：14 条评分、1 条 pending。 |
| G7 推送/网页收口 | Passed | `push.py` A/B 入口复用 G1 校验；full 每条增加 `类型·城市·格式`；月精度仍显示 `YYYY年M月（日期待官宣）`；时间轴同样过滤无效条目。`data/push-latest.txt` 与 `site/index.html` 已重建，均无 G1 过滤对象。 |

### 验证

- 全套单测：Passed — `PYTHONPATH=src .venv/bin/python -m pytest --override-ini addopts= -q` → `63 passed`。
- 编译：Passed — `.venv/bin/python -m compileall -q src tests`，退出码 0。
- 空白检查：Passed — `git diff --check`，退出码 0；仅只读检查，没有 git 写操作。
- 产物清理：`data/events.jsonl` 从 35 条清理到 23 条；`data/candidates-unscored.jsonl` 由 15 条过滤到 10 条；`data/push-latest.txt` 与 `site/index.html` 已由本地事件重建；未调用 `--send`。
- 适配器 fixture：Passed — G1 selector 与 G5 文章误抓测试包含在全套 63 项中；未抓网页、未启动 Chromium。

### 应提交文件清单

- `src/activity_radar/normalization.py`、`src/activity_radar/rules.py`、`src/activity_radar/research.py`、`src/activity_radar/push.py`、`src/activity_radar/render.py`、`src/activity_radar/cli.py`、`config/scoring.yaml`、`config/sources.yaml`。
- `fixtures/data_quality/`、`tests/test_normalization.py`、`tests/test_rules.py`、`tests/test_repairs.py`。
- `data/events.jsonl`、`data/candidates-unscored.jsonl`、`data/push-latest.txt`、`data/push-history/`、`data/source-health.json`、`site/index.html`、本文件。
- 排除 `.env`、`.pw-browsers/` 和临时文件；由外层按文件清单精确 stage，建议提交信息：`fix: harden activity radar data quality round`。

### Blocked / Deviations

- 未执行真实 LLM 评分；G6 使用 fake client 验证失败隔离和续跑语义，保留真实 pending，不用假数据冒充评分结果。
- 未启动 Chromium；rendered/真实网页验证仍遵守沙箱边界，由外层另行执行。
- 未发送任何消息；未执行 git add/commit/rebase/checkout/push；未打印或写出任何密钥。

## 收尾小修 Report

时间：2026-08-19（本地执行；仅处理 BRIEF §11 H1–H4）

### 结果

| 项 | 状态 | 实现与证据 |
|---|---|---|
| H1 短标题误杀 | Passed | `rules.is_valid_candidate()` 改为去空白/标点后统一计算汉字、字母、数字总字符数；`2026 金投赏`、`WAIC`、`金投赏` 回归通过，普通短标题仍返回 `title_too_short`。 |
| H2 显式年份优先与 past 过滤 | Passed | `normalization.parse_date_text()` 优先解析标题/日期文本中的显式年份；`normalize_candidate_row()`/`normalize_raw_candidate()` 在已有日期与显式年份冲突时采用显式年份；`prefilter_candidates()` 对早于锚点日期的候选计入 `past` 并丢弃。`2025年11月15日 ... Google DevFest` 回归得到 2025-11-15，并在 2026-08-19 锚点下被过滤。 |
| H3 合并字段保真 | Passed | `_merge_duplicate_pair()` 现在按结构化类型优先级保留 `开发者大会/峰会/展会` 等更具体类型，名称取更完整记录，各线分数取较高值，`score_history` 保留双方及合并快照；DevFest 跨源回归通过。 |
| H4 AMZ123 标题清洗 | Passed | AMZ123 归一化先解析标题中的显式日期/城市，再从名称尾部移除 `2026-08-27 浙江省杭州市`；回归结果为名称 `2026拉美跨境电商赋能大会·杭州站`、日期 `2026-08-27`、城市 `杭州`。 |

### 验证

- 定向测试：`PYTHONPATH=src .venv/bin/python -m pytest --override-ini addopts= -q tests/test_normalization.py tests/test_repairs.py tests/test_rules.py` → `48 passed`。
- 全套单测：`PYTHONPATH=src .venv/bin/python -m pytest --override-ini addopts= -q -rA` → `68 passed in 0.66s`。
- 编译：`.venv/bin/python -m compileall -q src tests` → 退出码 0。
- 空白检查：`git diff --check` → 退出码 0。
- 本地候选回归：155 条原始候选归一化后，`past=4`；金投赏候选保留；2025 GDG 候选不进入保留集；AMZ123 示例清洗结果符合 H4。

### 范围与副作用

- 未执行 live run、网页抓取、Chromium、外发或 Hermes 发送；未改写 `data/events.jsonl`、`site/index.html`、推送样张或源健康运行产物。
- 未执行任何 git 写操作（未 add/commit/rebase/checkout/push）；保留工作区既有改动，提交由外层复核后处理。
- 未读取、打印或写入任何密钥；没有新增依赖或外部系统副作用。

### 应提交文件清单

- `src/activity_radar/normalization.py`
- `src/activity_radar/rules.py`
- `src/activity_radar/research.py`
- `tests/test_normalization.py`
- `tests/test_repairs.py`
- `tests/test_pipeline.py`
- `PROGRESS-v2.md`

建议提交信息：`fix: close activity radar v2 data quality gaps`

## 推送格式轮 Report

时间：2026-08-19 09:29 PDT（仅执行 BRIEF §12 P1–P6；未联网、未启动 Chromium、未外发、未执行 git 写操作）

### 执行记录

- 执行期间先读取 §12、§2、§8 和现有推送/side event/源健康实现；先运行定向测试，再按失败证据收口格式。
- 随后完成 P1–P6 与独立回归；全套增至 75 项并保持全绿。
- 09:21–09:26：用现有 `data/events.jsonl` 生成最终 dry-run full/delta；中间不达标样张已删除，只保留最终两份。

### P1–P6

| 项 | 状态 | 实现与证据 |
|---|---|---|
| P1 去重与压缩 | Passed | full 第 1 段只对本窗口 A/B 展示最多 12 条详情和最多两句理由；超量与 C 级改为汇总行。未来 4 周、截止提醒均改为单行日程，不再重复理由。回归覆盖 14 条 A + 2 条 C 的上限、汇总和日程无理由。 |
| P2 side event 收紧 | Passed | `rules.apply_side_event_links()` 只允许 Tier A 且多日/平台官方/规模 ≥1000 的大会开启机会，B 级即使多日且官方也会清空机会和关联；推送对旧数据再次 fail-closed，只展示合格大会，每场最多 5 个周边局，余量显示“等 N 个”。 |
| P3 源健康按周 | Passed | 新增并持久化 `first_scanned`；连续无 hit 改为 `first_scanned` 距今 ≥28 天且 `last_hit` 为空或距今 ≥28 天。未满 28 天的空源只汇总数量；异常只列 `error/blocked/unavailable`，同一源不会重复出现。旧健康文件无 `first_scanned` 时保守回退 `last_scanned`。 |
| P4 微信分段发送 | Passed（mock） | `split_message()` 按段落/条目边界生成带 `（i/N）` 前缀且实际长度 ≤1800 的块；`send_via_hermes()` 逐块发送，块间调用 35 秒等待，失败立即停止并记录 `chunk/chunk_count`。成功、冷却等待、第二块失败均只用 fake/mocked subprocess 测试，没有调用真实 Hermes。 |
| P5 首周特例 | Passed | 新发现窗口优先读取最近 full 历史样张时间；`first_seen` 早于该时间不再算新。没有 full 历史时只取最近 7 天。回归同时覆盖有/无历史两条路径。当前 full 因已有同日 full 记录，第 1 段正确显示“无”。 |
| P6 网页链接 | Passed | full/delta 末尾均为 `完整时间轴：https://potato-uu.github.io/activity-radar/`，样张末尾断言通过。 |

### 最终样张

- full：`data/push-history/20260819T162156Z-full.txt`，`wc -m` = **1269 字**，目标 `<=4000` Passed。
- delta：`data/push-history/20260819T162644Z-delta.txt`，`wc -m` = **978 字**，目标 `<=2000` Passed。
- `data/push-latest.txt` 为最后一次生成的 delta；两次 CLI 结果均为 `status=dry_run`、`chunks=1`，没有使用 `--send`。
- delta 按 §3.5 只保留“新增/变更/取消 + 7 天内截止 + 网页链接”，移除了旧实现重复的未来 4 周和源健康段。

### 验证

| 检查 | 状态 | 证据 |
|---|---|---|
| P1–P6 最小单测 | Passed | `tests/test_repairs.py` 为 P1–P6 各有独立测试；P4 另覆盖失败块日志。 |
| 全套单测 | Passed | `PYTHONPATH=src .venv/bin/python -m pytest --override-ini addopts= -q -rA` -> `75 passed in 0.76s`。 |
| 编译 | Passed | `.venv/bin/python -m compileall -q src tests`，退出码 0。 |
| 空白检查 | Passed | `git diff --check`，退出码 0。 |
| 样张字数/链接 | Passed | full 1269、delta 978；两份文件末尾链接断言均为 true。 |

### 产出与应提交文件

- 代码：`src/activity_radar/push.py`、`src/activity_radar/rules.py`、`src/activity_radar/cli.py`。
- 测试：`tests/test_repairs.py`、`tests/test_rules.py`。
- dry-run 产物：`data/push-history/20260819T162156Z-full.txt`、`data/push-history/20260819T162644Z-delta.txt`、`data/push-latest.txt`、`logs/push.jsonl`。
- 进度：`PROGRESS-v2.md`。
- 建议提交信息：`fix: compress activity radar push delivery`。

### Blocked / Deviations

- 无阻塞项。按本轮明确禁区未抓网页、未联网、未启动 Chromium、未真实发送 Hermes、未读取或打印密钥。
- 未执行 `git add/commit/rebase/checkout/push`；只运行了 `git status`、`git diff`、`git diff --check` 等只读检查。
- `data/events.jsonl` 未重写；P2 对现有旧关联由推送层即时过滤，下一次正常 merge 会按新规则重算并清空不合格的 B 级大会关联。

## 2026-08-19 微信排版轮进行中

- 已完整读取 `BRIEF-v2-2026-08-18.md` §13，并复核 §2 禁区与 §8 沙箱续跑约束。
- 当前执行边界：只修改微信纯文本排版、相关单测、dry-run 样张与本进度文件；不做 git 写操作、不外发、不启动 Chromium、不抓网页。
- TDD RED 进行中：正在为 full 关键行顺序、A 级理由截断、expected/系列/needs_review 标记与 delta 分类链接添加失败测试。

## 微信排版轮 Report

时间：2026-08-19 21:35 PDT（上海日期 2026-08-20）

### 结果

- Passed：full 已改为 `📡` 标题、A 级三行信息 + 独立链接、B/截止/side event 两行式、时间轴与单行源健康；段落空行按 §13 模板生成。
- Passed：delta 已改为 `🆕 新增 / ✏️ 变更 / ❌ 取消`，每条活动使用两行式并保留独立链接；无变化时只输出现有一句话文案 + 时间轴。
- Passed：日期为 `M/D 周X`，expected 为 `M月（日期待官宣）`；A 级理由只取第一句、最长 40 字且句号结尾；支持 ①-⑳ 与 21 以后数字序号、`⚠️`、系列下一场和非 http 链接抑制。
- Passed：side event 保留上一轮语义，每场合格 A 级大会展示最多 5 个周边局，每个展示项都有自己的可点击 URL，超出数量用一行汇总。

### 回归与验收

| 检查 | 状态 | 证据 |
|---|---|---|
| 新格式 TDD | Passed | 首轮 6 项测试先以旧格式失败，实现后 `6 passed`；后续上海日期/delta 边界 2 项也经历 RED -> GREEN。 |
| 全套单测 | Passed | `PYTHONPATH=src .venv/bin/python -m pytest --override-ini addopts= -q` -> `83 passed in 0.83s`。 |
| 编译 | Passed | `.venv/bin/python -m compileall -q src tests` -> 退出 0。 |
| 空白错误 | Passed | `git diff --check` -> 退出 0。仅检查，未执行 git 写操作。 |
| full 分块 | Passed | 2883 字，3 块，真实最大块 1484 字，低于 1800。 |
| delta 分块 | Passed | 100 字，1 块，加 `（1/1）` 前缀后 106 字，低于 1800。 |
| 外发/Chromium/网页 | Not tested（按禁区） | 两次最终 CLI 均返回 `status=dry_run`；未使用 `--send`，未启动 Chromium，未抓网页。 |

### 最终样张

- full：`data/push-history/20260820T043348Z-full.txt`，**2883 字**，3 块。
- delta：`data/push-history/20260820T043353Z-delta.txt`，**100 字**，1 块。
- `data/push-latest.txt` 指向最后一次生成的 delta 样张。本轮中间样张已删除；任务开始前已有的 `20260820T033628Z-full.txt` 未动。

### 补充修正

- 修正了 Mac 系统时区导致的头部日期偏差：未显式传 `today` 时，统一使用 `Asia/Shanghai` 日期；最终样张正确显示 `8/20`。
- 修正了 delta 重放所有历史 `changed/cancelled` 的问题：现在仅保留最后一次 full 之后的变化；紧接 full 生成的最终 delta 因无新变化，正确输出 100 字无变化样张。

### 产出与应提交文件

- 代码：`src/activity_radar/push.py`、`src/activity_radar/cli.py`。
- 测试：`tests/test_repairs.py`、`tests/test_pipeline.py`。
- dry-run 产物：`data/push-history/20260820T043348Z-full.txt`、`data/push-history/20260820T043353Z-delta.txt`、`data/push-latest.txt`、`logs/push.jsonl`。
- 进度：`PROGRESS-v2.md`。
- 建议提交信息：`fix: redesign activity radar wechat layout`。本轮未执行 add/commit/rebase/checkout/push。

### Blocked / Deviations

- 无阻塞项，无未满足的 §13 关键格式项。
- 按 §2/§8 未联网、未抓网页、未启动 Chromium、未外发、未读取或打印密钥，未做任何 git 写操作。
- `BRIEF-v2-2026-08-18.md`、`data/push-history/20260820T033628Z-full.txt` 与本轮开始前已有的 `logs/push.jsonl` 改动未回退；只在后者追加了本轮 dry-run 记录。

## 2026-08-23 08:35 PDT - 全流程审阅轮进行中

- 已完整读取 `BRIEF-v2-2026-08-18.md` §14，并复核 §2 禁区与 §8 沙箱约束。
- 执行顺序锁定为 R1→R2→R3→R4（每条先复现、再最小修复、后定向单测），随后执行 P1 七项消费者视角审阅。
- 边界：不做 git 写操作、不外发、不启动 Chromium、不抓网页、不读取或打印密钥、不修改 `~/.local/share/activity-radar` 运行克隆。
- 工作区起点：`BRIEF-v2-2026-08-18.md` 是 Stan 已有改动，`reimbursements/` 是私人未跟踪目录；均保留，R4 只增加忽略规则。

### 2026-08-23 08:49 PDT - R1–R4 定向修复完成

- 基线：`PYTHONPATH=src .venv/bin/python -m pytest --override-ini addopts= -q` → `85 passed in 0.95s`；该结果不覆盖 R1–R4。
- RED：新增四条定向回归后首跑 → `4 failed`，分别证明 pull 失败无警告/无可观测日志、缺少 `migrate` 子命令、裸 `160期` 未归一化、`reimbursements/` 未忽略。
- R1：`scripts/push_local.sh` 在 pull 前恢复跟踪的 `logs data site`；pull 失败通过 `RADAR_GIT_PULL_FAILED=1` 传入 CLI，`logs/push.jsonl` 写 `kind=pull_failed`，推送文案末尾加“⚠️ 数据未更新（git pull 失败）”。Actions 改为 `git add data site`；`.gitignore` 新增 `logs/*.jsonl` 与本地 `.success` 标记。
- R2：新增幂等 `radar migrate --clean-names`，只改存量事件的 `name/city`，不动分数、Tier、status 或 `score_history`；记录到 `logs/migrate.jsonl`。
- R3：根因有三层：`normalize_name()` 未剔除“160期”这种无“第”前缀的期数；跨运行合并会丢旧 occurrences；文本型 `small/small_salon/small_open` 未触发“小型公开双线 -2”。早先“6/2/B 符合封顶 7”的判断不完整，深审后已纠正为 4/0/D，并按本地已提交候选证据恢复 157–160 四个日期。
- R4：`.gitignore` 已加 `reimbursements/`，未读取或改动该私人目录内容。
- GREEN：四条定向测试 → `4 passed in 0.17s`。

## 全流程审阅轮 Report

时间：2026-08-23 18:07 PDT
终态：`completed`（按 §14 完成 R1–R4 修复、P1 七项审阅和本地 dry-run；P1 指定“大问题先不动”的残余风险已明确列出）

### 结论（按严重度）

- Critical：无未修 Critical。
- Important：仍有 4 个设计风险，均属于 §14 指定的“写清但先不动”：研究完成与本机推送之间没有新鲜度握手；分块发送失败后没有 durable outbox/续传游标；`merge_events()` 仍会删除 invalid/out-of-scope 旧记录；运行克隆不会随 `pyproject.toml` 自动更新 `.venv`。
- Moderate / Unknown：真实 iLink 小时级频控下 45/120/300 秒策略的成功率无本轮实发证据；旧事件没有 raw score，周边 -1 与小型公开 -2 无法逐条追溯。新事件已写 `metadata.score_audit`，但不伪造回填旧数据。
- 本轮额外修复的 P1 小问题：同分重复运行不再膨胀 `score_history`；旧 pending 不再被新研究覆盖；网页隐藏 cancelled/D；`_reason_text` 不误切 `3.5`；Hermes 只重试明确频控错误；分块日志可重建部分发送状态；`--as-of` 对 direct/fixture/LLM source 全部生效。

### R1–R4

| 项 | 状态 | 证据与结果 |
|---|---|---|
| R1 运行克隆脏拉取 | Passed | `scripts/push_local.sh` pull 前执行 §14 指定 checkout；考虑 logs 解绑后 pathspec 不存在，增加 `data site` fallback。pull 失败设置 `RADAR_GIT_PULL_FAILED=1`，`logs/push.jsonl` 写 `kind=pull_failed`，消息末尾追加“⚠️ 数据未更新（git pull 失败）”。Actions 只 `git add data site`；`.success` 被忽略。`test_r1_runtime_clone_recovers_artifacts_and_warns_when_pull_fails` 通过。 |
| R2 存量名称清洗 | Passed | `radar migrate --clean-names` 首跑 41→41、changed=1：`2026拉美跨境电商赋能大会·杭州站 2026-08-27 浙江省杭州市` → `2026拉美跨境电商赋能大会·杭州站`；city/分数/Tier/status/history 不变。二跑 changed=0，日志分别在 2026-08-23T15:49:05Z、2026-08-24T01:06:08Z。 |
| R3 系列/小局 | Passed | 本地 `a7fbe592...:data/candidates-latest.jsonl` 证明 157/158/159/160 期日期为 8/22、8/23、8/29、8/30。规则剥离裸 `160期`、跨运行保留 occurrences、文本 small 触发 -2。当前 `evt-b9416394721e` 为 1 条四日期系列，raw 6/2 → final 4/0、Tier D；仍在 41 条归档中，但不进推送/网页。真实数据回归与 D 归档测试通过。 |
| R4 私人目录 | Passed | `.gitignore` 包含 `reimbursements/`；未读取、未修改该目录内容。 |

### P1 七项

| # | 状态 / 严重度 | 审阅结论 | 建议 |
|---|---|---|---|
| 1 调度 | 问题 / Important | §14 的“周日/周三 08:00”前提与实际 workflow 不一致：周日是 16:00 SH，周三是 08:00 SH；两者到本机 18:05/10:05 都有 125 分钟，90 分钟 job timeout 在不排队时留 35 分钟缓冲。但本机只做 `git pull`，无法证明当次 Actions 已完成；一旦 GitHub 排队，第一次 pull 会推旧数据，成功 marker 又阻止当日晚些时候补发。 | 不先改 cron；Actions commit 写 `research_finished_at/run_id/source_commit`，本机发送前校验同一上海日期的新鲜度。不满足则 fail closed 并告警，不写 success。 |
| 2 `push --auto` | 核心 Passed；部分失败 / Important | mode 和 marker 日期都使用发送开始时捕获的 `now`，周日 23:59 跨到周一仍写周日 marker；marker 只在整条 `send_via_hermes` 返回 sent 后写，失败不误标。缺口：前块已发、后块失败时无 marker，下一小时从第 1 块重发；全部发完到 marker 落盘之间崩溃也会重复。 | durable outbox 保存 `message_id + next_chunk`，每块成功原子推进游标；完整完成后再写日 marker。 |
| 3 发送链路 | 部分 Passed / Important + Unknown | `（i/N）` 是块内容的一部分，重试内容稳定；每次 chunk 写 `message_id/chunk/chunk_count/attempt/status`，现在可判断已发/未发。永久错误已改为立即失败，只有 `rate limit/rate_limit/cooldown/too many requests/429` 走 120/300 秒。真实小时级频控存活率 Unknown；45 秒块间隔和最多 7 分钟单块冷却不是成功保证，`subprocess.run` 也没有 timeout。 | 先实现 outbox；再用 3 次真实受控发送统计频控窗口，决定间隔。给 Hermes subprocess 增加有证据的超时上限。 |
| 4 数据层 | 问题 / Important | R3 后 existing D 保留且消费者隐藏；但 `merge_events()` 仍跳过 invalid/out-of-scope 旧记录，违反 `events.jsonl` 只增不删。相同分数/仅时间戳的重复运行不再追加 history；真实变化仍无上限，当前最大 11。`candidates-latest` 是覆盖式当次快照；旧 `unscored` 已跨运行保留，当前 latest=123、unscored=0，但没有 TTL/归档策略。 | 将 events 改成 append/update + `status/rejection_reason`，展示层过滤；给 score history 定上限或拆审计日志；pending 增加过期清理及 tombstone。 |
| 5 评分抽查 | 部分 Passed / Moderate | 10 条抽查见下表。供给侧/培训/沙龙上限可从 final 核对；旧数据没有 raw score，周边 -1、小型 -2、邀约 -1 只能判断“结果相容”，不能证明实际执行。未来由 `score_audit.raw/applied/final` 提供证据。 | 不回填猜测值；下一次真实评分后按 audit 抽查，再决定是否迁移旧分。 |
| 6 Claude 内联改动 | Passed，保留上述大风险 | `_reason_text` 已修复小数点边界；Hermes 重试只针对频控并记录块状态；`auto_mode >=` 的同日补发和 marker 测试通过；fixture 与 LLM source 的 `--as-of` 均已稳定。`auto_mode >=` 本身合理，但不能替代第 1 项的新鲜度握手，也不能解决第 2 项部分发送续传。 | 保留 catch-up；把新鲜度和 outbox 作为独立门，不继续往 `auto_mode` 塞隐式条件。 |
| 7 运行克隆 | 问题 / Important | 代码可由 ff-only pull 同步，tracked data/site 可恢复，untracked success marker 保留；但 dev/clone 两份状态没有代码 SHA/研究完成握手，且 clone `.venv` 不会因 `pyproject.toml` 变化自动安装。源代码本地改动也会让 pull 失败后继续用旧代码。 | wrapper 记录/校验 checkout SHA、研究完成时间和依赖锁文件 hash；依赖 hash 变化时执行受控安装，失败则不发送。 |

### 评分 10 条抽查

| 事件 | 规则 | 当前结果 | 判定 |
|---|---|---|---|
| 2026云栖大会 | supply cap + 杭州 -1 | 3/8 | 上限相容；raw 未保存，-1 未证实 |
| Google Devfest 上海 | supply cap | 3/8 | Passed（获客 ≤4） |
| 第28届上海国际广告展 | supply cap | 4/4 | Passed（获客 =4） |
| 华为全联接大会 | supply cap | 2/8 | Passed（获客 ≤4） |
| 百度 AI 办公智能体实训营 | training cap | 3/7 | Passed（获客 =3） |
| 徐汇滨江 AI 出海 Drink Chat | small/open + salon cap | 7/4 | cap Passed；-2 因无 raw 未证实 |
| AI+生态招商沙龙 | small_open + salon cap | 7/4 | cap Passed；-2 因无 raw 未证实 |
| 一人公司 Coffeechat | small_open | raw 6/2 → 4/0 | Passed；`score_audit` 可追溯 |
| 2026杭州出口跨境电商博览会 | 杭州 -1 | 7/5 | 结果相容；raw 未保存，未证实 |
| 世界互联网大会乌镇峰会 | 嘉兴 -1 + invite_only -1 | 4/6 | 结果相容；raw 未保存，未证实 |

### 验证与样张

| 检查 | 状态 | 实际结果 |
|---|---|---|
| 全套单测 | Passed | `PYTHONPATH=src .venv/bin/python -m pytest --override-ini addopts= -q` → `95 passed in 0.93s`。 |
| R1–R4/P1 定向回归 | Passed | R1–R4 + D 展示为 5 passed；as-of/重试为 4 passed；R1 fallback 单测 1 passed。 |
| 编译 | Passed | `.venv/bin/python -m compileall -q src tests`，exit 0。 |
| plist / shell | Passed | `plutil -lint` → OK；`push_local.sh`、install、uninstall 三份 zsh 脚本 `zsh -n` exit 0。 |
| diff | Passed | `git diff --check` exit 0。 |
| full dry-run | Passed | `data/push-history/20260824T010507Z-full.txt`：3151 字、2 块，含前缀长度 1392/1769。 |
| delta dry-run | Passed | `data/push-history/20260824T010517Z-delta.txt`：100 字、1 块，含前缀长度 106。 |
| 当前归档 | Passed | 41 行、41 个唯一 id；Coffeechat 四日期 D 仍归档，推送与 `site/index.html` 均不展示。 |

### 外层提交清单

- 代码/调度：`.github/workflows/radar.yml`、`.gitignore`、`scripts/push_local.sh`、`src/activity_radar/cli.py`、`normalization.py`、`push.py`、`render.py`、`research.py`、`rules.py`。
- 测试：`tests/test_pipeline.py`、`tests/test_repairs.py`、`tests/test_rules.py`。
- 数据/产物：`data/events.jsonl`、`site/index.html`、上述 20260824 full/delta 两份样张、`PROGRESS-v2.md`。
- Stan 已有输入：`BRIEF-v2-2026-08-18.md` 的 §14 改动本轮只读保留；是否与本轮一起提交由外层决定。
- 日志退出跟踪面（当前 `git ls-files` 仍能看到两文件，外层必须执行）：

```bash
git rm --cached logs/run.jsonl logs/push.jsonl
```

- 建议提交信息：`fix: harden activity radar end-to-end flow`。
- 不提交：`logs/migrate.jsonl`、本轮追加的 `logs/push.jsonl` 内容、`.success`、`reimbursements/`、`.env`、`.pw-browsers/`。

### 边界与副作用

- 未执行任何 git 写操作；未 add/commit/rebase/checkout/push。
- 未外发消息；所有新样张都是 dry-run。未启动 Chromium，未抓网页，未读取/打印密钥，未修改 `~/.local/share/activity-radar` 运行克隆。
- 本地文件副作用仅为：R2/R3 数据迁移、site/dry-run 样张重建、忽略日志追加；无外部副作用。

## 2026-08-23 遗留风险加固轮进行中

- 已完整读取 `BRIEF-v2-2026-08-18.md` §15，并复核 §2 禁区与 §8 沙箱约束；Challenge Check 通过，按 `WIP=1` 依次执行 S1-S4。
- 本轮边界：不做任何 git 写操作、不外发、不启动 Chromium、不抓网页、不修改 `~/.local/share/activity-radar`；测试使用临时 root、stub 和 mock 隔离外部副作用。
- 实施顺序：每项先新增最小失败测试并确认 RED，再做最小实现并确认 GREEN；完成后运行全套测试、编译、shell/plist 语法与只读 diff 检查。
- 基线：`PYTHONPATH=src .venv/bin/python -m pytest --override-ini addopts= -q -rA` -> `95 passed in 0.95s`。
- S1：已实现 `data/research-meta.json` 写入、上海日期新鲜度门、截止前 `waiting_fresh_data` 跳过、截止后旧数据日期警告；meta 缺失时明确提示未知日期，不伪造 `M/D`。TDD RED 为 5 failed，GREEN 为 `5 passed`；S1 + 既有 auto/R1 回归为 `8 passed`。
- S2：已实现 `data/push-history/<上海日期>-<mode>.outbox.json`，发送前落盘、每块成功后原子更新 `sent`，续跑优先使用原块并跳过已发块，全部成功后先写 `.success` 再删除 outbox；`.gitignore` 已忽略 outbox。TDD RED 为 4 failed，GREEN 为 `4 passed`；相关发送/auto/R1 回归为 `17 passed`。测试仅调用 stub Hermes。
- S3：新增 `radar migrate --backfill-audit`，只对缺审计事件重放 supply/training/salon cap 与 0-10 夹取，随后重算 Tier；周边、小型、邀约等减法不重放。TDD RED 为 4 failed，GREEN 为 `4 passed`；相关迁移/评分回归为 `8 passed`，二跑文件字节不变且 `changed_count=0`。
- S4：`push_local.sh` 在 pull 成功后比较 `pyproject.toml` SHA；不一致时用现有/新建 venv 安装，成功才更新 marker，失败记录并继续旧环境。TDD RED 为 2 failed + 1 syntax passed，GREEN 为 `3 passed`；R1 联合回归 `4 passed`，shell 语法、plist lint 与 diff 检查通过。执行测试只使用临时 root、stub git 和 stub Python。

## 遗留风险加固轮 Report

时间：2026-08-23 18:31 PDT（仅执行 `BRIEF-v2-2026-08-18.md` §15 S1-S4）

### 结论

- S1-S4 已按设计实现并分别完成 TDD RED/GREEN；全套单测从基线 95 项增至 111 项，最终全部通过。
- 本轮没有运行 live research、真实 Hermes、真实依赖安装或真实历史数据迁移；没有修改 `data/events.jsonl`、`data/research-meta.json` 或运行克隆。新行为由隔离单测和静态/语法检查验证。

### S1-S4 验收

| 项 | 状态 | 实现与证据 |
|---|---|---|
| S1 数据新鲜度握手 | Passed | `radar run --live` 成功结束时写 `data/research-meta.json`，包含 UTC `completed_at`、只读 `git rev-parse HEAD` 得到或回退为 null 的 `git_sha`、`event_count`、`mode`；Actions 既有 `git add data site` 会随 data 提交。`push --auto` 按上海日期判断：当天放行，周日 22:00/周三 14:00 前等待并写 `kind=auto_skip, reason=waiting_fresh_data`，截止后旧数据照发并追加日期警告。meta 缺失时不伪造日期，写“未找到研究结果日期”。5 条 S1 单测通过。 |
| S2 durable outbox | Passed | 自动发送使用 `data/push-history/<上海日期>-<mode>.outbox.json`；块组在首块前落盘，每块成功后用临时文件替换原子推进 `sent[index]=message_id`。存在未完成 outbox 时 `push --auto --send` 在新鲜度门和内容重建前直接续传，忽略冲突新消息；完成后先写 `.success` 再删 outbox，CLI 随后记 auto 成功。`.gitignore` 已忽略 outbox。4 条 S2 单测通过，Hermes 全为 stub。 |
| S3 评分审计回填 | Passed | 新增 `radar migrate --backfill-audit`；只对缺 `metadata.score_audit` 的事件重放 supply acquisition cap、training acquisition cap、开放小型/未知规模 salon 双线 cap、0-10 夹取，并重算 Tier。周边、小型、其他城市、邀约等减法绝不重放；结果记录 `backfilled/caps_applied/final`，摘要写 `logs/migrate.jsonl`。4 条 S3 单测覆盖三种 cap、合规分数不变、二跑 `changed_count=0` 与减法不重放。真实 `data/events.jsonl` 本轮未执行迁移。 |
| S4 运行克隆依赖自更新 | Passed | `push_local.sh` 仅在 pull 成功后比较 `pyproject.toml` SHA 与 `.venv/.pyproject.sha256`；venv 缺失时创建，hash 不同则执行 `pip install -q -e .`，安装成功才更新 marker。失败会输出日志、设置 `RADAR_DEPENDENCY_UPDATE_FAILED=1`，CLI 写本地失败日志并继续旧环境推送。临时 root + stub git/Python 测试证明首次安装、二次跳过、失败继续；`zsh -n` 通过。 |

### 最终验证

| 检查 | 状态 | 实际结果 |
|---|---|---|
| 全套单测 | Passed | `PYTHONPATH=src .venv/bin/python -m pytest --override-ini addopts= -q -rA` -> `111 passed in 2.01s`。 |
| 编译 | Passed | `.venv/bin/python -m compileall -q src tests`，退出码 0。 |
| shell / plist | Passed | `zsh -n scripts/push_local.sh scripts/install_launchagent.sh scripts/uninstall_launchagent.sh` 退出码 0；`plutil -lint scripts/com.stan.activity-radar.push.plist` -> `OK`。 |
| diff 空白错误 | Passed | `git diff --check` 退出码 0，仅只读检查。 |

### 产出与外层提交清单

- 代码/脚本：`.gitignore`、`scripts/push_local.sh`、`src/activity_radar/cli.py`、`src/activity_radar/push.py`。
- 测试/进度：`tests/test_repairs.py`、`PROGRESS-v2.md`。
- 建议提交信息：`fix: harden activity radar legacy delivery risks`。
- `BRIEF-v2-2026-08-18.md` 的既有改动是 Stan 输入，本轮只读保留，不列为本轮产出。

### 边界、未测试与副作用

- 未执行任何 git 写操作；只运行 `git status`、`git diff`、`git diff --check` 等只读命令。
- 未外发、未调用真实 Hermes、未启动 Chromium、未抓网页、未联网研究、未读取或打印密钥。
- 未修改 `~/.local/share/activity-radar`，也未对真实运行克隆执行 pull、venv 创建或 pip 安装；S4 只在临时目录使用 stub 验证。
- 未对真实 `data/events.jsonl` 执行 `migrate --backfill-audit`，避免未经外层复核批量降低历史分数；命令行为与幂等性已由隔离测试验证。
## 2026-08-25 03:46 PDT - ChatGPT Ads 垂直重排轮进行中

- 已完整读取 `BRIEF-v2-2026-08-18.md` §16，并复核 §2 禁区与 §8 沙箱约束；Challenge Check 通过，终点为 `final_verified`，按 T1→T2→T3 串行执行。
- 边界：不执行任何 git 写操作，不外发，不启动 Chromium，不登录/绕验证码，不读取或打印密钥；T3 只生成 dry-run 文件。
- 工作区起点：`main...origin/main`；用户已有改动仅为 `BRIEF-v2-2026-08-18.md` 新增 §16，本轮不回退。
- 数据基线：`data/events.jsonl` 41 条；其中 `active/expected` 16 条，重排前 Tier 为 A=2、B=7、C=7、D=0；39 条已有 `score_history`。
- 实现缺口：`config/scoring.yaml` 和打分 prompt 仍含已废止的新能源 P2 / `ai_developers`；现有 `radar score --pending` 只能补打未评分候选，尚无全量重打 active/expected 的持久化入口。
- 验收口径：T1 配置/prompt/测试一致；T2 对全部 current 事件使用真实 Responses LLM 重打，旧分进 `score_history`、Tier 重算，然后运行 `radar run --live` 取最新候选一起进；T3 标题/新分排序/A 级新 reason 全部验证，且 `status=dry_run`。

### 2026-08-25 04:08 PDT - T1 完成，T2 入口已就绪

- T1：`config/scoring.yaml` 新增 `score_profile=chatgpt_ads_v1`；获客权重精确改为 P0=0.65/P1=0.35，删除 P2；生态权重改为 platform=0.55/channel=0.45，删除 `ai_developers`。
- prompt：改为 ChatGPT Ads 买家/分销渠道语义，明确 OpenAI 官方 > 其他 AI 平台 > Google/Meta/TikTok 官方；reason 必须恰好两句，第一句谁在场，第二句具体销售/渠道动作，并禁止编造。
- T2 入口：新增 `radar score --active`，只对当前 `active/expected` 重打；受保护的事件身份/status 不由 LLM 改写，旧分和新 `score_profile` 进 `score_history`，失败条目落 `data/events-rescore-unscored.jsonl`。
- 额外防污染：定向测试暴露旧流程会把 reason 里的“海外营销代理商”误当成城市，从而把已确认上海活动改成海外并误扣分。重打分入口已将存量城市作为受保护事实。
- T3 入口：新增 `radar push --mode full --chatgpt-ads-rerank`，标题固定为 `📡 活动雷达｜ChatGPT Ads 垂直重排版`；按 Tier 主分 `max(获客,资源)` 降序，同分时获客分优先，落独立 `*-chatgpt-ads-rerank.txt` 历史文件。
- TDD：首次收集因缺 `rescore_active_events` 正确失败；实现后 4 条新定向测试全绿。全套 `PYTHONPATH=src .venv/bin/python -m pytest --override-ini addopts= -q` -> `115 passed in 2.81s`。

### 2026-08-25 04:30 PDT - T2 存量真实 LLM 重打完成

- 端点预检：有效 `CODEX_BASE_URL` host 为 `sub2api.gaochouxiang.today`、path `/v1`，模型 `gpt-5.5`，鉴权状态仅验证为可用，未输出密钥值。开始前将原始 `data/events.jsonl` 备份到 `/tmp/activity-radar-events-before-chatgpt-ads-20260825.jsonl`，两者起点 SHA-256 同为 `be0c706c8f6c4a28251426c27345332a05cfb6a58a0d01453b9d189a5198f1bd`。
- 第 1 次真实 `radar score --active`：16/16 `active/expected` 成功，`scoring_result=hit`，pending=0；两个真实 usage 记录为 5393/724 和 9095/6603 input/output tokens。Tier 从 A=2/B=7/C=7 变为 B=4/C=2/D=10，13 条 Tier 改变。
- 状态语义复核：`changed` 在本库表示仍有效但自上次扫描有变，不是归档/取消。当时还有 24 条 `changed` 旧语义记录（其中 A/B=22），若不重打会直接污染特殊 full 推送。因此 `--active` 口径收口为所有未取消 current 状态，并按 `score_profile` 跳过已完成条目；新增 1 条幂等回归后定向为 `5 passed`。
- 第 2 次真实续跑：`target_count=24`、`skipped_current_profile=16`、24/24 成功，pending=0；Tier 从 A=10/B=12/C=1/D=1 变为 A=2/B=8/D=14，17 条 Tier 改变。
- 累计结果：40/40 条 current 事件均有 `metadata.score_profile=chatgpt_ads_v1`，旧分与新分均在 `score_history`；已取消的 1 条保持不变。

## 垂直重排轮 Report

时间：2026-08-25 04:29 PDT
终态：`completed`（T1-T3 全部完成；T3 仅 dry-run，未外发）

### 结论

- T1 Passed：评分语义已收窄为 ChatGPT Ads 买家/分销渠道；P0/P1=0.65/0.35，platform/channel=0.55/0.45，新能源 P2 和 `ai_developers` 已删除。
- T2 Passed：原始 40 条未取消 current 事件均用真实 Responses LLM 重打；live 后恢复并补打 2 条被旧 merge 逻辑误删的历史，最终 42/42 current 都是 `chatgpt_ads_v1`，旧 40 条的 ID 和原始分均 40/40 可在 `score_history` 追溯。
- T2 live Passed with coverage limits：`radar run --live --sources <31 个非 rendered 源>` 真实评分 103 条候选，`scoring_result=hit`、score failures=0、pending=0；真新增 2 条 B（GDMS、East Forward）。五个 rendered 源因“不起 Chromium”禁区明确排除。
- T3 Passed：特殊 full 标题精确匹配，按 `(max(获客,资源), 获客, 资源)` 降序；A 级 4 条均展示新 reason 的第一句事实判断。`data/push-history/20260825T112735Z-chatgpt-ads-rerank.txt` 为 1473 字/1 块，最后 push 日志 `status=dry_run`。

### T1-T3 验收

| 项 | 状态 | 证据 |
|---|---|---|
| T1 权重与 prompt | Passed | `config/scoring.yaml`、`score_prompt()` 与新回归一致；prompt 不再含新能源/AI 开发者/星图比特旧语义。 |
| T2 真实 LLM | Passed | 12 个真实 score batch、145 条次（存量 16+24、live 103、恢复 2），失败批次 0；加 `llm-sweep` 合计 214,357 input / 66,218 output tokens。 |
| T2 历史保留 | Passed | 43 行/43 唯一 ID；42 current + 1 cancelled；原始 40 个 current ID 及旧分均在。`events-rescore-unscored.jsonl` 不存在，`candidates-unscored.jsonl` 为 0 行。 |
| T2 reason | Passed | 42/42 current reason 都恰好两句；事件城市不再被“海外营销代理商”等销售描述改写。 |
| T3 特殊 full | Passed | 标题、A 级顺序 9/7 → 8/6 → 8/6 → 8/5、reason、时间轴链接均断言通过；`push-latest` 与历史文件字节一致。 |
| 全套回归 | Passed | `PYTHONPATH=src .venv/bin/python -m pytest --override-ini addopts= -q -rA` -> `118 passed in 4.57s`。 |
| 编译/空白 | Passed | `.venv/bin/python -m compileall -q src tests` exit 0；`git diff --check` exit 0。 |
| 外发/Chromium/git 写 | Passed（禁区） | 未使用 `--send`，未调用 Hermes，未启动 Chromium，未执行 add/commit/rebase/checkout/push。 |

### 同一原始 cohort 的 Tier 对照

原始 40 条 current 分布：A=12 / B=19 / C=8 / D=1。
重排后同一 40 条分布：A=4 / B=12 / C=6 / D=18。
Tier 变化 26 条，不变 14 条；表中分数顺序为“获客/资源”。

| 日期 | 活动 | 旧 Tier/分数 | 新 Tier/分数 |
|---|---|---:|---:|
| 2026-08-19 | 2026 虹桥跨境供应链生态发展论坛 | B 6.3/5.2 | C 5/5 |
| 2026-08-19 | 陆家嘴之夜第五期 / AI+投资 | C 5/4 | D 0/0 |
| 2026-08-20 | AI科创下午茶第71期：桌面AI智能体平台发展现状及机遇 | B 3/6 | D 0/2 |
| 2026-08-20 | 徐汇滨江AI 出海Drink Chat | B 7/4 | D 4/2 |
| 2026-08-21 | Markethon 2026 卖客松：没有假信号的销售黑客松 | B 6/4 | D 1/0 |
| 2026-08-21 | TRAE AI 创造力大赛 | B 2/6 | D 2/5 |
| 2026-08-22 | Next Founder Meetup：创始人之约 | B 6/2 | D 2/1 |
| 2026-08-25 | AI+生态招商沙龙 | B 7/4 | C 4/5 |
| 2026-08-26 | 2026上海国际广告节 | A 8/6 | B 6/7 |
| 2026-08-27 | 百度搭子发布会｜AI 办公智能体实训营 | B 3/7 | D 0/3 |
| 2026-08-28 | AI出海闭门沙龙！共话中国AI出海新格局 | B 6/3 | D 5/2 |
| 2026-09-01 | 9/1 知外×领英×融创云，共探AI出海局 | A 8/6 | B 6/3 |
| 2026-09-05 | AICD - Shanghai | B 1/6 | D 2/3 |
| 2026-09-05 | 微信开发者创新工坊｜上海站：与 AI 共生小程序开发者交流 | B 2/7 | D 0/2 |
| 2026-09-09 | Inclusion·外滩大会 | A 8/8 | D 3/3 |
| 2026-09-15 | 上海国际广告新科技秋交会 | C 4.2/4.7 | D 3/4 |
| 2026-09-15 | 第28届上海国际广告展 | C 4/4 | D 3/4 |
| 2026-09-17 | 华为全联接大会2026 | A 2/8 | D 3/5 |
| 2026-09-22 | 2026云栖大会 | A 3/8 | D 1/5 |
| 2026-09-22 | S-Tron Shanghai 2026 | C 4/4 | D 4/3 |
| 2026-09-23 | 第五届全球数字贸易博览会 | B 6/4 | C 5/4 |
| 2026-10-01 | 中国国际广告节 | C 5/5 | B 6/6 |
| 2026-11-01 | Morketing Summit | A 8/7 | B 7/6 |
| 2026-11-01 | 世界互联网大会乌镇峰会 | B 4/6 | D 0/1 |
| 2026-11-05 | 第九届中国国际进口博览会 | A 8/5 | B 7/4 |
| 2026-11-07 | ⚡️ 2026 Google Devfest 谷歌开发者节 | A 3/8 | B 2/7 |

live 真新增（不混入上表的原始 cohort）：

| 日期 | 活动 | Tier/分数 |
|---|---|---:|
| 2026-09-10 | 第十二届 GDMS 全球数字营销峰会 | B 6/5 |
| 2026-09-17 | East Forward·2026 企业出海大会 | B 7/5 |

### live 覆盖与限制

- 本周 live 运行次数：1，未超过 §2 的每周 2 次上限。本轮 31 源中命中 8 源：OnePilot、calendar seed、GDG、TikTok、霞光社、Eventbrite、annual calendar、llm-sweep。
- 20 源为 error/blocked/unavailable：其中 16 个直接 HTML/JSON 源多数是同类 `SSL: UNEXPECTED_EOF_WHILE_READING`；OpenAI 源 HTTP 403，Meta 源 robots Disallow，Microsoft 404，WeChat 缺 `miku_ai`。这些是当前数据覆盖缺口，不是“无活动”。
- 五个 rendered 源 `huodongxing/luma-shanghai-ai/10times/mosu-space/meetup-shanghai-ai` 按用户明确禁区未执行；不声称它们已扫描。
- API 成本：Unknown。日志有 token usage，但未配置 input/output price，`api_cost=null`；未猜测金额。

### 产出与副作用

- 代码/配置：`config/scoring.yaml`、`src/activity_radar/research.py`、`rules.py`、`push.py`、`cli.py`、`tests/test_repairs.py`。
- 数据/网页：`data/events.jsonl`、`candidates-latest.jsonl`、`source-health.json`、`research-meta.json`、`site/index.html`。
- dry-run 样张：`data/push-history/20260825T111803Z-full.txt`（live 标准 full）、`data/push-history/20260825T112735Z-chatgpt-ads-rerank.txt`（T3 最终版）、`data/push-latest.txt`。
- 未产生外部副作用：未发送微信，未启动 Chromium，未登录/绕验证码，未修改 LaunchAgent/Actions/运行克隆，未做任何 git 写操作。
- 建议提交信息：`feat: rerank activity radar for ChatGPT Ads`；由外层精确 stage，不要包含 `.env`、`logs/*.jsonl` 或 `/tmp` 备份。
