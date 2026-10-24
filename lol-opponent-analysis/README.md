# 英雄联盟对手分析

用拳头官方接口（Riot API）的对局数据分析一位对手，最后生成一个单文件 HTML 仪表板。

- `download_riot.py`：第一步，下载数据（在你自己的电脑上跑）
- `analyze.py` + `lolanalysis/`：第二步，原始数据 → CSV → 汇总 → 仪表板
- `strategy.example.json`：应对方案页的填写格式
- `data/patch_notes_26.20-26.21.json`：最近两个版本（26.20、26.21）官方更新公告 / 英雄联盟 Wiki 的摘录，
  每条带版本号和来源链接；`analyze.py` 默认读取，在“应对方案”页列出与他的英雄、我的英雄池和野区资源相关的改动
- `tests/`：只用于测试代码的合成数据和模拟服务器（不是真实对局）

两个脚本都只用 Python 自带的库，不需要 `pip install`。

---

## 第一步：下载数据（你来跑）

### 1. 准备

1. 安装 Python 3.8 或更高版本（<https://www.python.org/downloads/>；Windows 安装时勾选 “Add Python to PATH”）。
2. 申请密钥：用拳头账号登录 <https://developer.riotgames.com>，首页的 **DEVELOPMENT API KEY** 点 “Regenerate API Key”，复制 `RGAPI-` 开头的那串。
   开发者密钥 **24 小时过期**，过期了重新生成一个就行。
3. 把 `download_riot.py` 放进一个文件夹，在这个文件夹里打开终端（Windows：在文件夹地址栏输入 `powershell` 回车）。

### 2. 设置密钥（只在当前终端窗口有效，不会写进任何文件）

| 系统 | 命令 |
|---|---|
| Windows PowerShell | `$env:RIOT_API_KEY="RGAPI-你的密钥"` |
| Windows cmd | `set RIOT_API_KEY=RGAPI-你的密钥` |
| macOS / Linux | `export RIOT_API_KEY=RGAPI-你的密钥` |

### 3. 运行

```
python download_riot.py
```

（macOS / Linux 上可能要写 `python3`。）要查的对手已经默认填好：`原州市的努力的天才超级打野李承勇#VALOR`，服务器自动识别（识别不到按欧服西部 euw1）。
想查别人或换服务器：

```
python download_riot.py --riot-id "名字#TAG" --platform euw1 --count 20
```

脚本会：

1. account-v1 查 PUUID → league-v4 查当前单双排段位
2. match-v5 取最近 20 场单双排（queue 420）的比赛 ID，逐场下载对局详情和时间线
3. league-v4 查每场对面同位置玩家的当前段位（用来检验“同段位”；不想要加 `--no-opponent-ranks`）
4. Data Dragon 下载当前版本（和对局所在版本）的英雄、装备数据，当前版本的完整英雄技能数据
   `championFull.json`（技能冷却、消耗、距离），以及召唤师峡谷小地图 `map11.png`
5. 打包成 `lol_data_日期_时间.zip`

总共大约 65 次请求，正常 1–2 分钟跑完。遇到 429（请求太频繁）会按服务器给的等待时间自动暂停再继续；
某一场下载失败会跳过并记进 `manifest.json` 的 `failures`，不会整个停掉。

### 4. 把 zip 发回来

跑完最后一行会显示 zip 的位置，把这个 zip 发回来就行。

### 常见问题

| 现象 | 原因 / 办法 |
|---|---|
| `没有找到环境变量 RIOT_API_KEY` | 第 2 步没做，或者换了一个终端窗口。重新设置一次。 |
| `HTTP 401` / `HTTP 403` | 密钥过期或复制错了。到开发者网站重新生成。 |
| `找不到这个账号` | 游戏名或 Tag 不对。游戏名要一字不差（包括空格），Tag 不带 `#`。 |
| `SSL 证书校验失败`（macOS） | 运行一次 `/Applications/Python 3.x/Install Certificates.command`。 |
| 中途断了 | 加 `--out 上次的文件夹` 重新运行，已经下好的文件会跳过。 |

### zip 里有什么

```
lol_data_时间/
  manifest.json            每个文件的下载时间（UTC）、接口、大小、sha256；失败记录；不含密钥
  account.json             PUUID
  account_region.json      他所在的服务器（如果接口返回了）
  league_entries.json      当前段位（下载时，不是对局时）
  match_ids.json           比赛 ID（新到旧）
  matches/<比赛ID>.json     对局详情（match-v5），一场一个文件
  timelines/<比赛ID>.json   时间线（match-v5），一场一个文件
  lane_opponent_ranks.json 每场对面同位置玩家的当前段位
  ddragon/<版本>/           champion / item（中、英）；当前版本另有 championFull（技能冷却等）和 map11.png
  download_log.txt         运行日志
```

---

## 第二步：分析（拿到 zip 之后）

```
python3 -I analyze.py all 数据.zip --out 输出目录
```

分步跑（原始 JSON 只解析一次，之后都读 CSV）：

```
python3 -I analyze.py parse 数据.zip --out out     # 原始 JSON → out/csv/*.csv
python3 -I analyze.py calibrate --out out          # 区域划分核对
python3 -I analyze.py stats --out out              # 打印几十行汇总，写 out/summary.json
python3 -I analyze.py report --out out --strategy strategy.json   # 生成 out/dashboard.html
```

`out/csv/` 里的中间结果：

| 文件 | 内容 |
|---|---|
| `games.csv` | 每场一行：他的英雄、位置、红蓝方、胜负、时长、KDA、补刀、伤害占比、参团率、视野、对位、10/15 分钟差值、首件成装 |
| `participants.csv` | 每场 10 人：找双排、算同场对位平均 |
| `frames.csv` | 时间线每分钟一帧、每人一行：坐标、区域、经济、经验、补刀 |
| `kills.csv` | 所有击杀事件：时间、坐标、区域、击杀者/助攻/受害者、敌方参与人数 |
| `objectives.csv` | 小龙、虚空巢虫、先锋、男爵等 |
| `buildings.csv` | 防御塔/水晶 |
| `items.csv` | 他和对位的购买记录（已扣掉撤销的购买） |

### 口径

- **对比基准**：同一批对局里对面同位置玩家的平均值（同一场对局，MMR 接近）。这不是官方的段位平均；
  `lane_opponent_ranks.json` 用来检查这些对手的段位是否真的接近。
- **颜色**：绿 ▲ 好于基准、黄 ● 相差 ±10% 内、红 ▼ 差于基准、灰 ○ 无对比基准。
- **样本**：少于 10 场的结论都标“样本太少”。
- **位置**：时间线每分钟只记一次位置。由位置推出来的开野路线、抓人、反野都标“推测”并写出依据。
- **被抓**：击杀他的敌方英雄（击杀者 + 助攻）≥ 2 人。
- **成装**：Data Dragon 没有“传说装备”字段，按规则判定（见 `lolanalysis/staticdata.py`）。
- **坐标**：左下是蓝方基地、右上是红方。区域是近似划分，`calibrate` 会把位置点画到小地图上，
  并统计对线期上单/中单/下路的位置有多少落在自己那条线上，用来核对划分是否合理。

### 数据来源的约定

- 技能冷却、消耗：只从 zip 里的 Data Dragon 当前版本 `championFull.json` 读；装备价格和属性：只从 `item.json` 读。
- 野怪、小龙、巢虫、先锋、男爵的刷新时间：Data Dragon 里没有，来自官方更新公告和英雄联盟 Wiki（见 `data/`，带链接）。
- 对位胜率：只用你提供的 OP.GG 截图（分析环境访问不了 OP.GG），写明版本、段位范围和样本量；没有截图就不写胜率。

## 测试

```
python3 -I tests/run_tests.py --work /tmp/lol_test
```

`run_tests.py` 会：生成合成数据（不是真实对局）→ 启动模拟的 Riot API / Data Dragon（注入 429、没有 Retry-After 的 429、
503、404、过期密钥）→ 用 `download_riot.py` 下载 → `analyze.py all` 分析 → 和合成数据的真值逐项对照
（开野方向、成功的抓人、前 10 分钟死亡、首件成装（含撤销购买）、双排、死亡热点）。

单独生成合成数据：

```
python3 -I tests/make_fixture.py --out /tmp/fx/data --zip /tmp/fx/fixture.zip
```
