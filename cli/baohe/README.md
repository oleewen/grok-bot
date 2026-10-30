# baohe CLI

只读抽取宝盒lite（zbox-lite）本地会话缓存，输出 JSON + Markdown 报告骨架（不含 LLM 总结）。

## 安装

```bash
cd cli/baohe
python3 -m pip install -e ".[dev]"
```

若本机 pip 较旧（如 21.x）报错，可改用：

```bash
python3 -m pip install -e ".[dev]" --no-build-isolation
```

或临时不安装、直接：

```bash
cd cli/baohe
PYTHONPATH=. python3 -m baohe --help
```

### macOS PATH 说明

`pip install -e` 会把 `baohe` 脚本装到 `~/Library/Python/3.9/bin`（版本号随本机 Python 变）。该目录常不在 PATH 上时，请用：

```bash
python3 -m baohe …
```

或把上述 `bin` 加进 PATH。

## 用法

```bash
baohe list
baohe list --days 7
baohe export 日志采集治理
baohe export 日志采集治理 --days 14 -o ~/Documents/grok-exports/baohe/demo
baohe recent --days 2 --limit 10
```

等价模块入口：

```bash
python3 -m baohe list
python3 -m baohe export 日志采集治理
python3 -m baohe recent --days 2 --limit 5
```

- 默认数据目录：`~/Library/Application Support/zbox-lite`
- 覆盖：`--data-dir` 或环境变量 `BAOHE_DATA_DIR`
- 默认输出根：`~/Documents/grok-exports/baohe/<slug>-<timestamp>/`
- `recent` 另写同批目录下的 `index.md`

退出码：`0` 成功；`2` 会话未找到或零命中消息；`1` 路径/IO 错误。

## 会话发现（list）

`baohe list` / `export` / `recent` 合并以下**本地**来源（去重按会话 id，同 id 保留更长名称）：

1. `localCache.json` → `search_history`
2. `localCache2.json` → `search_history`（若存在；部分群只出现在 cache2）
3. IndexedDB LevelDB 尽力挖掘：从 `*.log`（以及默认不超过约 48MB 的 `*.ldb`，或设 `BAOHE_SCAN_LDB=1`）中解析 `groupId`/`contact_id` 与附近的 `groupName`/`contact_name`/`title`（UTF-8 与 UTF-16-LE）。**不调用**任何未公开的网络 API。

因此打开过但未进入 search_history 的群，有机会仍出现在 `list` 中；消息正文仍依赖既有 LevelDB/forward 解析，可能仍为空。

可选环境变量：`BAOHE_MINE_LDB_FILES`（默认 60，按 mtime 取最近 ldb）、`BAOHE_SCAN_LDB=1`（扫全部 ldb）、`BAOHE_MINE_DM=1`（额外挖掘弱标题/单聊，较慢且易噪）。

## LevelDB 扫描策略

- **默认（WAL 优先）**：先扫 IndexedDB 下的 ``*.log``；只拷贝将要扫描的文件，不做整库 1GB copytree。
- **点名导出**：已知会话 ID 时，对 ``*.ldb`` 做便宜的字节预过滤（``session_id in file``），只拷贝/解析命中的表；锚定 ``"msgId"`` 提取 JSON，避免二进制括号 O(n²)。
- **无会话的宽扫**：设置 ``BAOHE_SCAN_LDB=1`` 时，``iter``/选文件会额外纳入不超过 32MB 的 ``*.ldb``。
- 全量无差别括号扫描会极慢，故默认不做。

## 局限

- **`--days` 与缺时间戳**：设置 `--days` 时会丢弃没有 `sendTime` 的缓存碎片；本机 IndexedDB 片段常无时间，可能导致 0 条。需要尽量多拿消息时先不加 `--days`。


- 本地 IndexedDB / `localCache.json` / `localCache2.json` **缓存非全量历史**；报告头会标明「本地缓存尽力而为，非全量历史」。会话列表已合并多源，消息条数仍可能不足。
- 只读：不登录远端、不改写宝盒文件。
- 图片/语音等非文本记为占位（如 `[图片]`）。
- 宝盒正在写 LevelDB 时，CLI 会先拷贝再读；仍失败时请关闭客户端后重试。

## 文档

- 设计：[`docs/2026-10-15-baohe-cli-design.md`](docs/2026-10-15-baohe-cli-design.md)
- 实现计划：[`docs/2026-10-15-baohe-cli-plan.md`](docs/2026-10-15-baohe-cli-plan.md)
