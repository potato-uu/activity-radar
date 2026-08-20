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
