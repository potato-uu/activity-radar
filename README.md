# 上海 BD 活动雷达

这是 BD 活动雷达 v2：直连公开活动源、年度大会种子日历，LLM 只负责统一评分/归一化；覆盖上海与高铁 2.5 小时周边未来 120 天活动，随后生成静态时间轴和微信推送样张。

## 当前边界

- 活动范围为上海主场与杭州、苏州、南京、宁波、无锡、合肥、嘉兴、南通；来源配置在 `config/sources.yaml`，只增不删。
- 评分规则在 `config/scoring.yaml`，获客线和资源线独立保存；供给侧密集活动会限制获客分，小型开放活动会扣分。
- D 级活动不入库；webinar 允许进入推送且会显式标出类型；`expected` 条目会标记日期待官宣。
- 不登录、不破解反爬、不绕验证码；每个 host 请求间隔至少 2 秒；不自动报名/购票，不做 Phase 2 参会作战卡。
- 本地推送使用已存在的 Hermes CLI：`hermes send --to weixin`。默认 dry-run，只有明确传入 `--send` 才会外发。
- GitHub Actions 负责研究、历史文件和 GitHub Pages。GitHub-hosted runner 不会继承本机 Hermes 会话，所以远端 job 只生成 `data/push-latest.txt`；微信外发仍需在本机或已配置 Hermes 的 runner 执行 `radar push --send`。

## 安装

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

本机运行需要在项目 `.env` 中设置兼容端点的 `CODEX_BASE_URL`（只写 URL，不含密钥）；密钥不会写入项目，而是从环境变量或只读 fallback 到 `~/.codex/auth.json` 的 `OPENAI_API_KEY` 字段。实现不会打印密钥。默认模型是 `gpt-5.5`，可用 `RADAR_MODEL` 覆盖。联网研究使用 Responses API 的 `{ "type": "web_search" }` 工具。

## 本地命令

```bash
# fixture 全流程，不联网、不外发（测试请传隔离 --root）
PYTHONPATH=src python -m activity_radar.cli run --fixture fixtures/sample_candidates.json

# 真实研究；可限定来源，写入 JSONL、site、push-latest 与 push-history
PLAYWRIGHT_BROWSERS_PATH="$PWD/.pw-browsers" PYTHONPATH=src python -m activity_radar.cli run --live --sources onepilot,calendar-seed

# 仅查看/发送当前样张；默认 dry-run
PYTHONPATH=src python -m activity_radar.cli push --mode full
PYTHONPATH=src python -m activity_radar.cli push --mode delta
PYTHONPATH=src python -m activity_radar.cli push --mode full --send

# 对上一次运行未评分的候选补评分并合并入库
PYTHONPATH=src python -m activity_radar.cli score --pending

# 由 Asia/Shanghai 当前时刻自动选择周日 full / 周三 delta；其它时刻 skip
PYTHONPATH=src python -m activity_radar.cli push --auto --send

# 手动补录；必须给出日期，避免无法去重
PYTHONPATH=src python -m activity_radar.cli add https://example.com/event \
  --name "上海 AI 沙龙" --date-start 2026-09-20 --event-type 沙龙

# 验收回测：2026-07-01 用 OnePilot 原始适配器捕获 Google 上海开发者大会
PLAYWRIGHT_BROWSERS_PATH="$PWD/.pw-browsers" PYTHONPATH=src python -m activity_radar.cli backtest \
  --live-source onepilot --as-of 2026-07-01

# fixture 兼容模式
PYTHONPATH=src python -m activity_radar.cli backtest \
  --fixture fixtures/google-developer-backtest.json --as-of 2026-07-01
```

## 数据与验证

- `data/events.jsonl`：事件历史，按 Spec schema 落盘。
- `data/source-health.json`：每个来源的扫描、命中和连续无命中次数。
- `logs/run.jsonl`：逐源调用、错误、usage 和成本字段；未配置单价时会明确记录 `api_cost_status=logged_unknown`，不会伪造金额。
- `site/index.html`：单文件静态时间轴，支持 Tier、类型、获客线/资源线筛选。

来源研究使用有界并行，单个来源超时不会阻塞其他来源。可通过环境变量调整：
`RADAR_SOURCE_TIMEOUT_SECONDS`（默认 180）、`RADAR_SOURCE_RETRIES`（默认 2）、`RADAR_DISCOVERY_CONCURRENCY`（默认 3）。
`source-health.json` 的 `last_result` 区分 `hit`、`empty`、`timeout`、`error`、`unavailable`、`blocked`，并记录 `reason`；`last_error` 仅表示最近一次历史错误时间，不代表当前运行仍失败。

命令速记：`radar score --pending` 会补评分并合并未评分候选；`radar push --auto` 按上海时间选择 full/delta，其它时刻安全跳过。

Playwright 浏览器必须安装到项目内，避免写入用户目录：

```bash
PLAYWRIGHT_BROWSERS_PATH="$PWD/.pw-browsers" python -m playwright install chromium
```

本机会话受 macOS 沙箱限制时，rendered 源会如实标记 `blocked`；不绕过该限制。

LaunchAgent 文件只保存在 `scripts/`，由外层会话决定是否安装：

```bash
scripts/install_launchagent.sh
scripts/uninstall_launchagent.sh
```

卸载命令为 `launchctl bootout gui/$(id -u)/com.stan.activity-radar.push`；本任务会话不自动加载。

```bash
PYTHONPATH=src pytest -q
python3 -m compileall -q src
git diff --check
```

## GitHub Pages

`.github/workflows/radar.yml` 周日 08:00 UTC（全量）与周三 00:00 UTC（增量）运行，并部署 `site/`。仓库需要配置 `CODEX_API_KEY` secret；如果使用其他兼容端点，设置 `CODEX_BASE_URL`。不要把 Hermes 私密会话令牌上传到 GitHub；外发通过本机 Hermes 适配器完成。
