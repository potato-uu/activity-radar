# 上海 BD 活动雷达

这是 Spec v1 的 Phase 1 实现：按来源调用带联网搜索的 OpenAI Responses API，生成上海未来 60 天活动候选；规则层负责去重、日期/地点 diff、双线 Tier；随后生成静态时间轴和四段微信推送样张。

## 当前边界

- 活动范围固定为上海；来源配置在 `config/sources.yaml`，只增不删。
- 评分规则在 `config/scoring.yaml`，获客线和资源线独立保存。
- D 级活动不入库；webinar 默认只进时间轴，不进推送。
- 不做浏览器爬虫、反爬对抗、自动报名/购票、Phase 2 参会作战卡。
- 本地推送使用已存在的 Hermes CLI：`hermes send --to weixin`。默认 dry-run，只有明确传入 `--send` 才会外发。
- GitHub Actions 负责研究、历史文件和 GitHub Pages。GitHub-hosted runner 不会继承本机 Hermes 会话，所以远端 job 只生成 `data/push-latest.txt`；微信外发仍需在本机或已配置 Hermes 的 runner 执行 `radar push --send`。

## 安装

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

`CODEX_API_KEY`、`OPENAI_API_KEY` 或 `LM_API_KEY` 三者任一可用即可；本机开发时也会只读 fallback 到 `~/.codex/auth.json` 的 `OPENAI_API_KEY` 字段。实现不会打印密钥。默认模型是 `gpt-5.5`，可用 `RADAR_MODEL` 覆盖。联网研究使用 Responses API 的 `{ "type": "web_search" }` 工具。

## 本地命令

```bash
# fixture 全流程，不联网、不外发
PYTHONPATH=src python -m activity_radar.cli run --fixture fixtures/sample_candidates.json

# 真实研究、写入 JSONL、生成 site/index.html 和推送样张
PYTHONPATH=src python -m activity_radar.cli run --live

# 仅查看/发送当前样张；默认 dry-run
PYTHONPATH=src python -m activity_radar.cli push
PYTHONPATH=src python -m activity_radar.cli push --send

# 手动补录；必须给出日期，避免无法去重
PYTHONPATH=src python -m activity_radar.cli add https://example.com/event \
  --name "上海 AI 沙龙" --date-start 2026-09-20 --event-type 沙龙

# 验收回测：2026-07-01 应提前捕获 Google 上海开发者大会
PYTHONPATH=src python -m activity_radar.cli backtest \
  --fixture fixtures/google-developer-backtest.json --as-of 2026-07-01
```

## 数据与验证

- `data/events.jsonl`：事件历史，按 Spec schema 落盘。
- `data/source-health.json`：每个来源的扫描、命中和连续无命中次数。
- `logs/run.jsonl`：逐源调用、错误、usage 和成本字段；未配置单价时会明确记录 `api_cost_status=logged_unknown`，不会伪造金额。
- `site/index.html`：单文件静态时间轴，支持 Tier、类型、获客线/资源线筛选。

```bash
PYTHONPATH=src pytest -q
python3 -m compileall -q src
git diff --check
```

## GitHub Pages

`.github/workflows/radar.yml` 每周一 00:00 UTC 运行，并部署 `site/`。仓库需要配置 `CODEX_API_KEY` secret；如果使用其他兼容端点，设置 `CODEX_BASE_URL`。不要把 Hermes 私密会话令牌上传到 GitHub；外发通过本机 Hermes 适配器完成。
