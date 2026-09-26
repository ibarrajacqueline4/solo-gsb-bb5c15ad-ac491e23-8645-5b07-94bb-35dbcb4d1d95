# 播客转写字幕分段 API（Podcast Subtitle Segmentation API）

接收播客转写后按时间排列的词序列，在硬约束下用**动态规划**求出总分最低的字幕分段方案，
同时返回分段 JSON 与 SRT 字幕文本；另提供**广告插播点**接口，在词间静音处选出
总静音最长、满足间隔约束的插播位置，**说话人校正**接口，在人工锁定约束下
以最小代价重指派说话人，**章节时刻对齐**接口，按章节 cue 文本在同一说话人
连续词中的命中把章节锚定到词时间轴，**重复口播片段**接口，检测词序列
完全相同的两处口播片段，**敏感短语遮罩预览**接口，把命中敏感短语的字符
逐字替换为 `█` 并回报每处命中位置，**预告片片段组合**接口，在词区间
互不重叠与时长预算约束下选出必选说话人全覆盖、总评分最高的候选片段组合，
**播放变速规划**接口，把连续同一说话人的词合并为语速块，在目标最长
播放时长约束下选出总变速代价最小的语速方案，以及**叠话统计报告**接口，
对允许乱序与交叠的词序列统计各说话人发声覆盖、至少两人同时发声的
叠话区间与各说话人对的总重叠毫秒数，以及**修订稿重对齐逐词时间**接口，
按 `(text, speaker)` 在原稿与修订稿间求最长保序匹配，命中词沿用原时间、
未命中修订词在相邻锚点间隔内均分时间，以及**访谈问答索引**接口，把相邻
同一说话人的词合并为发言轮次，识别以问号结尾的提问，在其他说话人、始于
问题结束后且间隔受限的回答中做保序一对一配对，生成可回听的问答索引，
以及**响度增益规划**接口，在真峰值不削波与相邻帧增益变化受限的硬约束下，
为 1～300 个按时间排列的测量帧选择命中目标响度、总代价最小的整数 dB 增益。
基于 Python 3.12 + FastAPI。

## 一条命令启动

```bash
docker compose up --build
```

该命令自动完成镜像构建、依赖安装（`requirements.txt`）并启动服务，
服务监听并映射到宿主机 **`http://localhost:8000`**（API 地址）。

必要配置：**无外部依赖**（不需要数据库、缓存或消息队列等任何旁置服务），
也无需手工初始化——Python 3.12、FastAPI、uvicorn 等全部依赖在镜像构建阶段
自动安装，容器启动即完成初始化并就绪。唯一占用的是宿主机 **8000 端口**
（`docker-compose.yml` 已内置 `8000:8000` 端口映射与 `/health` 健康检查；
如宿主机 8000 被占用，仅需把映射改为 `"<空闲端口>:8000"`）。

- 分段接口：`POST http://localhost:8000/segments`
- 广告插播点接口：`POST http://localhost:8000/ad-breaks`
- 说话人校正接口：`POST http://localhost:8000/speaker-reconcile`
- 章节时刻对齐接口：`POST http://localhost:8000/chapter-align`
- 重复口播片段接口：`POST http://localhost:8000/duplicate-passages`
- 敏感短语遮罩预览接口：`POST http://localhost:8000/redact-preview`
- 预告片片段组合接口：`POST http://localhost:8000/trailer-plan`
- 播放变速规划接口：`POST http://localhost:8000/playback-plan`
- 叠话统计报告接口：`POST http://localhost:8000/crosstalk-report`
- 修订稿重对齐逐词时间接口：`POST http://localhost:8000/transcript-retime`
- 访谈问答索引接口：`POST http://localhost:8000/qa-pairs`
- 响度增益规划接口：`POST http://localhost:8000/gain-plan`
- 健康检查：`GET http://localhost:8000/health`
- 交互式 API 文档（Swagger UI）：`http://localhost:8000/docs`

> 不想用 Docker 时也可：`pip install -r requirements.txt && uvicorn app.main:app --host 0.0.0.0 --port 8000`

## 请求

`POST /segments`，`Content-Type: application/json`

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 1800, "text": "大家好。",       "speaker": "A"},
    {"start_ms": 1800, "end_ms": 6100, "text": "今天聊聊",       "speaker": "A"},
    {"start_ms": 6100, "end_ms": 7000, "text": "动态规划。",     "speaker": "A"},
    {"start_ms": 7000, "end_ms": 9100, "text": "我同意你的看法。", "speaker": "B"}
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `start_ms` | 非负整数 | 词起始时间（毫秒） |
| `end_ms` | 非负整数 | 词结束时间（毫秒，须不早于 `start_ms`） |
| `text` | 非空字符串 | 词文本；**段文本为词文本原样拼接，不插入空格** |
| `speaker` | 非空字符串 | 说话人标识 |

## 分段规则（硬约束）

每一段必须同时满足：

1. **换人必断**：段内所有词的 `speaker` 相同，说话人变化处强制断开；
2. **长度**：段文本不超过 **18 个 Unicode 字符**（按 code point 计数，`😀` 算 1 个）；
3. **时长**：首词 `start_ms` 到末词 `end_ms` 在 **1000～6000 毫秒**之间（含边界）；
4. 段至少包含一个词。

输入还必须满足：词按时间排列（`start_ms` 非倒序）、相邻词时间不重叠
（允许端点相接，即下一个词的 `start_ms` 等于上一个词的 `end_ms`），否则返回 **422**。

## 计分规则（动态规划最小化）

对**非末段**累计分数（末段不参与扣分）：

- 末词的文本**不以** `。`、`！`、`？` 结尾：**+2 分**；
- 段时长不足 **2000 毫秒**：**+1 分**。

在所有总分相同的方案中，取**断点序列字典序最小**者。断点序列只记录“词之间切开
的位置”（除第一段外每段首词的序号，不含固定的起点 0 与终点 n），按标准字典序
比较——因此同分时自然优先段数更少、首个断点更靠前的方案。例如断在词 8 与词 12
之后：断点序列 `(8,)` 小于 `(12,)`。

## 响应示例（200）

```json
{
  "segments": [
    {
      "index": 0,
      "start_word": 0,
      "end_word": 0,
      "start_ms": 0,
      "end_ms": 1800,
      "duration_ms": 1800,
      "speaker": "A",
      "text": "大家好。",
      "char_count": 4
    },
    {
      "index": 1,
      "start_word": 1,
      "end_word": 2,
      "start_ms": 1800,
      "end_ms": 7000,
      "duration_ms": 5200,
      "speaker": "A",
      "text": "今天聊聊动态规划。",
      "char_count": 9
    },
    {
      "index": 2,
      "start_word": 3,
      "end_word": 3,
      "start_ms": 7000,
      "end_ms": 9100,
      "duration_ms": 2100,
      "speaker": "B",
      "text": "我同意你的看法。",
      "char_count": 8
    }
  ],
  "total_score": 1,
  "srt": "1\n00:00:00,000 --> 00:00:01,800\n大家好。\n\n2\n00:00:01,800 --> 00:00:07,000\n今天聊聊动态规划。\n\n3\n00:00:07,000 --> 00:00:09,100\n我同意你的看法。\n"
}
```

> `start_word` / `end_word` 均为 **0-based、含端点**的词序号。
> 上例总分 1：第 1 段仅 1800ms（不足 2000ms）记 1 分，它以 `。` 结尾不记标点分；
> 第 2 段为非末段但以 `。` 结尾且时长达标，记 0 分；末段不记分。
> 词 0 不能与词 1 合段（0→6100ms 共 6100ms 超上限）；若词 1 单独成段，
> “今天聊聊”不以句末标点结尾会被记 2 分，故 DP 选择把词 1、2 合为一段（总分 1）。

### curl 示例

```bash
curl -s http://localhost:8000/segments \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":1800,"text":"大家好。","speaker":"A"},{"start_ms":1800,"end_ms":6100,"text":"今天聊动态规划。","speaker":"A"},{"start_ms":6100,"end_ms":8200,"text":"我同意。","speaker":"B"}]}'
```

## 广告插播点：`POST /ad-breaks`

在相邻词之间的静音处选择 `break_count` 个插播点，使选中静音的总毫秒数最大。
`words` 字段与 `/segments` 完全相同，并沿用同一套词序与时间校验（422 错误码一致）。

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 1800, "text": "大家好。",   "speaker": "A"},
    {"start_ms": 2600, "end_ms": 5200, "text": "今天聊聊",   "speaker": "A"},
    {"start_ms": 5900, "end_ms": 6800, "text": "动态规划。", "speaker": "A"},
    {"start_ms": 7600, "end_ms": 9700, "text": "下期再见。", "speaker": "B"}
  ],
  "break_count": 2,
  "min_silence_ms": 500,
  "min_spacing_ms": 1000
}
```

字段说明（`words` 之外）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `break_count` | 整数，1～5 | 需要选出的插播点个数，必须恰好选满 |
| `min_silence_ms` | 正整数 | 候选静音时长下限：相邻词间隔达到该值才成为候选 |
| `min_spacing_ms` | 非负整数 | 任意相邻选中插播点的最小时间间隔 |

### 选择规则

1. **候选**：仅取相邻词之间静音时长 `>= min_silence_ms` 的位置，插播时间为
   静音区间的**整数中点** `(silence_start_ms + silence_end_ms) // 2`；
2. **约束**：恰好选出 `break_count` 个候选，任意相邻选中时间至少相隔
   `min_spacing_ms`；
3. **目标**：选中静音的总毫秒数最大；同分时取**时间序列字典序最小**的方案；
4. 不存在满足全部约束的方案时返回 **422**（`infeasible_ad_breaks`）。

### 响应示例（200）

```json
{
  "breaks": [
    {
      "index": 0,
      "time_ms": 2200,
      "silence_start_ms": 1800,
      "silence_end_ms": 2600,
      "silence_ms": 800
    },
    {
      "index": 1,
      "time_ms": 7200,
      "silence_start_ms": 6800,
      "silence_end_ms": 7600,
      "silence_ms": 800
    }
  ],
  "total_silence_ms": 1600
}
```

> 上例三处候选静音为 800ms（中点 2200）、700ms（中点 5550）、800ms（中点 7200），
> 两两间隔均满足 `min_spacing_ms=1000`；总静音最大的组合是首尾两处（1600ms）。
> `breaks` 始终按时间升序返回，`index` 为 0-based 序号。

### curl 示例

```bash
curl -s http://localhost:8000/ad-breaks \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":1800,"text":"大家好。","speaker":"A"},{"start_ms":2600,"end_ms":5200,"text":"今天聊聊","speaker":"A"},{"start_ms":5900,"end_ms":6800,"text":"动态规划。","speaker":"A"},{"start_ms":7600,"end_ms":9700,"text":"下期再见。","speaker":"B"}],"break_count":2,"min_silence_ms":500,"min_spacing_ms":1000}'
```

### 无可行插播方案

候选不足 `break_count` 个，或候选间距离无法满足 `min_spacing_ms` 时，返回 422，
并附带候选数与请求的插播点数：

```json
{
  "error": {
    "code": "infeasible_ad_breaks",
    "message": "不存在满足全部约束的插播方案",
    "break_count": 2,
    "candidate_count": 1
  }
}
```

## 说话人校正：`POST /speaker-reconcile`

在**严格满足人工锁定**、且不改词序/文本/时间的前提下，为每个词重新指派说话人，
使总成本最小。`words` 字段与 `/segments` 完全相同，并沿用同一套词序与时间校验
（422 错误码一致）。

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 1800, "text": "大家好。",   "speaker": "A"},
    {"start_ms": 1800, "end_ms": 3600, "text": "我是小明。", "speaker": "A"},
    {"start_ms": 3600, "end_ms": 5400, "text": "谢谢。",     "speaker": "B"},
    {"start_ms": 5400, "end_ms": 7200, "text": "下期再见。", "speaker": "A"}
  ],
  "locks": [{"word_index": 2, "speaker": "B"}],
  "switch_cost": 2
}
```

字段说明（`words` 之外）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `locks` | 数组，可空/可省略 | 人工锁定项，每项含 `word_index`（0-based 词序号）与非空 `speaker`；该词必须指派为该说话人 |
| `switch_cost` | 整数，0～10 | 相邻换人的单位代价 |

### 校正规则

1. **候选说话人**：取原词 `speaker` 与锁定值的并集，**至多 8 个**，超限返回 422
   （`too_many_speakers`）；锁定可以引入原序列中不存在的新说话人；
2. **硬约束**：锁定词严格取锁定值；词序、文本、时间一律不变；
3. **目标**：最小化 `改动原 speaker 的词数 + switch_cost × 相邻换人次数`；
4. **同成本裁决**：取整条说话人序列**字典序最小**者；
5. `locks` 为空数组或省略时即对原标注做平滑（例如开关成本较高时，孤立的
   单人插话会被并入相邻说话人）；
6. 锁定索引越界（含负数）、同一 `word_index` 重复锁定均返回 422。

### 响应示例（200）

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 1800, "text": "大家好。",   "speaker": "A"},
    {"start_ms": 1800, "end_ms": 3600, "text": "我是小明。", "speaker": "A"},
    {"start_ms": 3600, "end_ms": 5400, "text": "谢谢。",     "speaker": "B"},
    {"start_ms": 5400, "end_ms": 7200, "text": "下期再见。", "speaker": "B"}
  ],
  "changed_word_indices": [3],
  "total_cost": 3
}
```

> 上例锁定词 2 为 `B`。保留原标注 `AABA` 需 2 次相邻换人（成本 4）；把词 3 也
> 改为 `B`（`AABB`）只需 1 次改动 + 1 次换人（成本 3）；全部改为 `B`（`BBBB`）
> 需 3 次改动（成本也是 3）——同成本取字典序最小的 `AABB`。
> `changed_word_indices` 为说话人被改动的词序号（0-based，升序）。

### curl 示例

```bash
curl -s http://localhost:8000/speaker-reconcile \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":1800,"text":"大家好。","speaker":"A"},{"start_ms":1800,"end_ms":3600,"text":"我是小明。","speaker":"A"},{"start_ms":3600,"end_ms":5400,"text":"谢谢。","speaker":"B"},{"start_ms":5400,"end_ms":7200,"text":"下期再见。","speaker":"A"}],"locks":[{"word_index":2,"speaker":"B"}],"switch_cost":2}'
```

## 章节时刻对齐：`POST /chapter-align`

把按章节顺序排列的章节锚定到词时间轴：每条章节 `cue` 在**同一说话人连续词**的
拼接文本中查找全部 Unicode 字符命中（允许跨词、允许重叠），每章选一个命中，使
各章实际时刻与预计起点 `approx_ms` 的**绝对时间偏差之和最小**。`words` 字段与
`/segments` 完全相同，并沿用同一套词序与时间校验（422 错误码一致）。

服务地址：`POST http://localhost:8000/chapter-align`

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 500,  "text": "大家好",   "speaker": "A"},
    {"start_ms": 600,  "end_ms": 1200, "text": "今天聊",   "speaker": "A"},
    {"start_ms": 1300, "end_ms": 2000, "text": "动态规划", "speaker": "A"},
    {"start_ms": 2200, "end_ms": 2800, "text": "明天见",   "speaker": "B"}
  ],
  "chapters": [
    {"title": "开场", "cue": "大家", "approx_ms": 0},
    {"title": "主题", "cue": "动态", "approx_ms": 1000}
  ],
  "min_gap_ms": 500
}
```

字段说明（`words` 之外）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `chapters` | 非空数组 | 按章节顺序排列；每项含非空 `title`、非空 `cue`、非负整数 `approx_ms`（预计起点，毫秒） |
| `min_gap_ms` | 正整数 | 相邻章节实际时刻的最小间隔（毫秒）；该约束蕴含时刻严格递增 |

### 对齐规则

1. **候选命中**：对每章 `cue`，把每个**同一说话人连续区间**内所有词的文本原样
   拼接（不插空格），枚举 `cue` 的全部 Unicode 字符命中（按 code point 比较，
   `😀` 算 1 个字符）。命中**允许跨词**，但不允许跨越说话人边界；同一文本中的
   重叠出现也各自算一条命中（如 `cue="aa"` 在 `"aaa"` 中有 2 条命中）；
2. **候选时刻**：取**命中首字符所在词**的 `start_ms`；
3. **选择约束**：为每章恰选 1 条命中，相邻章节候选时刻严格递增且至少相隔
   `min_gap_ms`（正间隔即保证严格递增）；
4. **目标**：最小化 `Σ |实际时刻 - approx_ms|`；
5. **同分裁决**：取各章**命中起始全局字符位**序列字典序最小者。全局字符位为
   命中首字符在“全部 `words` 文本原样拼接”中的 0-based code point 偏移；
6. 任一章没有任何命中，或无法为所有章节组成完整方案时返回 **422**
   （`infeasible_chapter_alignment`）。

### 响应示例（200）

```json
{
  "chapters": [
    {
      "index": 0,
      "title": "开场",
      "time_ms": 0,
      "word_index": 0,
      "char_start": 0,
      "char_end": 2
    },
    {
      "index": 1,
      "title": "主题",
      "time_ms": 1300,
      "word_index": 2,
      "char_start": 0,
      "char_end": 2
    }
  ],
  "total_deviation_ms": 300
}
```

> `chapters` 按输入章节顺序返回，`index` 为 0-based 章序号。
> `word_index` 为命中首字符所在词序号（0-based）；`char_start`/`char_end` 为该词内
> Unicode 字符偏移区间 `[char_start, char_end)`：命中完全落在一个词内时覆盖整条
> cue（上例即“大家”“动态”）；**跨词命中时区间截断到命中首字符所在词的词末**，
> 即 `char_end` 等于该词字符数（候选时刻仍取该词 `start_ms`）。
> 上例总偏差 300：`|0-0| + |1300-1000|`。

### curl 示例

```bash
curl -s http://localhost:8000/chapter-align \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":500,"text":"大家好","speaker":"A"},{"start_ms":600,"end_ms":1200,"text":"今天聊","speaker":"A"},{"start_ms":1300,"end_ms":2000,"text":"动态规划","speaker":"A"},{"start_ms":2200,"end_ms":2800,"text":"明天见","speaker":"B"}],"chapters":[{"title":"开场","cue":"大家","approx_ms":0},{"title":"主题","cue":"动态","approx_ms":1000}],"min_gap_ms":500}'
```

### 无法组成完整方案

任一章 cue 无命中（含只在说话人边界两侧“拼接”才出现的情形），或受
`min_gap_ms` 限制无法为全部章节选出严格递增时刻时，返回 422：

```json
{
  "error": {
    "code": "infeasible_chapter_alignment",
    "message": "不存在满足时刻严格递增且相邻至少相隔 min_gap_ms 的完整章节对齐方案",
    "chapter_count": 2,
    "min_gap_ms": 1000
  }
}
```

某章无命中时错误体携带 `chapter_index`（最早无命中的章序号，0-based）。

## 重复口播片段：`POST /duplicate-passages`

以每个词的 `(text, speaker)` 组成符号序列，查找**两处词序列完全相同**的连续
口播片段：两处词索引区间不重叠，且两处首词的 `start_ms` 至少相隔
`min_gap_ms`。只返回词数最多的一对。`words` 字段与 `/segments` 相同
（至多 20000 词），并沿用同一套词序与时间校验（422 错误码一致）。

服务地址：`POST http://localhost:8000/duplicate-passages`

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 800,  "text": "欢迎收听",       "speaker": "A"},
    {"start_ms": 800,  "end_ms": 1600, "text": "小宇宙",         "speaker": "A"},
    {"start_ms": 1600, "end_ms": 2400, "text": "播客",           "speaker": "A"},
    {"start_ms": 2400, "end_ms": 4000, "text": "今天聊动态规划。", "speaker": "B"},
    {"start_ms": 5000, "end_ms": 5800, "text": "欢迎收听",       "speaker": "A"},
    {"start_ms": 5800, "end_ms": 6600, "text": "小宇宙",         "speaker": "A"},
    {"start_ms": 6600, "end_ms": 7400, "text": "播客",           "speaker": "A"}
  ],
  "min_words": 2,
  "min_gap_ms": 1000
}
```

字段说明（`words` 之外）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `min_words` | 整数，2～20 | 重复片段的最短词数；最佳片段不足该词数时返回 `match: null` |
| `min_gap_ms` | 非负整数 | 两处首词 `start_ms` 的最小间隔（毫秒，含边界） |

### 检测规则

1. **匹配单位**：整词——以 `(text, speaker)` 为符号，两处连续符号序列完全相同
   才算重复，**单词文本不拆分**；
2. **约束**：两处词索引区间不重叠（端点相接允许）；两处首词的 `start_ms`
   至少相隔 `min_gap_ms`；
3. **目标**：只返回词数最多的一对；同长度时按两处起始词索引的二元组
   `(first.start_word, second.start_word)` 字典序裁决；
4. `words` 至多 **20000** 个词，超限返回 422（`invalid_request`）；
5. 不存在词数 ≥ `min_words` 的合法片段对时返回 **200**，`match` 为 `null`。

### 响应示例（200）

```json
{
  "match": {
    "word_count": 3,
    "text": "欢迎收听小宇宙播客",
    "first":  {"start_word": 0, "end_word": 2, "start_ms": 0,    "end_ms": 2400},
    "second": {"start_word": 4, "end_word": 6, "start_ms": 5000, "end_ms": 7400}
  }
}
```

> `start_word` / `end_word` 均为 **0-based、含端点**的词序号；`first` 为起始词
> 索引较小的一处。`text` 为共同文本：词 `text` 原样拼接，不插空格。
> 上例词 0～2 与词 4～6 的 `(text, speaker)` 序列完全相同，首词相隔 5000ms。

### curl 示例

```bash
curl -s http://localhost:8000/duplicate-passages \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":800,"text":"欢迎收听","speaker":"A"},{"start_ms":800,"end_ms":1600,"text":"小宇宙","speaker":"A"},{"start_ms":1600,"end_ms":2400,"text":"播客","speaker":"A"},{"start_ms":2400,"end_ms":4000,"text":"今天聊动态规划。","speaker":"B"},{"start_ms":5000,"end_ms":5800,"text":"欢迎收听","speaker":"A"},{"start_ms":5800,"end_ms":6600,"text":"小宇宙","speaker":"A"},{"start_ms":6600,"end_ms":7400,"text":"播客","speaker":"A"}],"min_words":2,"min_gap_ms":1000}'
```

### 无足够长的重复片段

最佳片段词数不足 `min_words`（或根本没有重复）时返回 200 且 `match` 为 `null`：

```json
{"match": null}
```

## 敏感短语遮罩预览：`POST /redact-preview`

把每个**同一说话人连续区间**内所有词的 `text` 原样拼接（不插空格），在其中查找
全部敏感短语的精确命中，把命中字符**逐字替换为 `█`**（每个字符一个 `█`）后返回
完整词序列，并回报每处命中的位置。`words` 字段与 `/segments` 相同
（至多 2000 词），并沿用同一套词序与时间校验（422 错误码一致）。

服务地址：`POST http://localhost:8000/redact-preview`

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 800,  "text": "记得使用", "speaker": "A"},
    {"start_ms": 800,  "end_ms": 1400, "text": "优惠码",   "speaker": "A"},
    {"start_ms": 1400, "end_ms": 2000, "text": "八八八",   "speaker": "A"},
    {"start_ms": 2200, "end_ms": 3400, "text": "别错过",   "speaker": "B"}
  ],
  "phrases": ["使用优惠码", "八八八"]
}
```

字段说明（`words` 之外）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `phrases` | 非空字符串数组，1～50 个 | 敏感短语列表；空字符串、超过 50 个或缺省均返回 422 |

### 遮罩规则

1. **匹配**：在每个同一说话人连续区间的拼接文本中查找短语的精确命中
   （按 Unicode code point 比较，`😀` 算 1 个字符）。命中**允许跨词**，但
   **不得跨越说话人边界**；同一文本中的重叠出现各自算一条候选命中
   （如短语 `aa` 在 `aaa` 中有 2 条候选）；
2. **重叠裁决**：候选按**起点靠前优先、同起点短语较长优先**的顺序贪心选出
   互不重叠的命中——被跳过的候选都与某个优先级更高的已选命中重叠；
3. **替换**：仅把选中字符逐字替换为 `█`（3 字符短语即替换为 3 个 `█`），
   词序、时间、说话人一律不变；
4. `words` 至多 **2000** 个词，超限返回 422（`invalid_request`）；
5. 无任何命中时返回 **200**：`words` 与输入一致、`hits` 为空数组。

### 响应示例（200）

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 800,  "text": "记得██", "speaker": "A"},
    {"start_ms": 800,  "end_ms": 1400, "text": "███",    "speaker": "A"},
    {"start_ms": 1400, "end_ms": 2000, "text": "███",    "speaker": "A"},
    {"start_ms": 2200, "end_ms": 3400, "text": "别错过", "speaker": "B"}
  ],
  "hits": [
    {"index": 0, "phrase": "使用优惠码", "start_word": 0, "end_word": 1, "start_char": 2, "end_char": 3},
    {"index": 1, "phrase": "八八八",     "start_word": 2, "end_word": 2, "start_char": 0, "end_char": 3}
  ]
}
```

> `hits` 按命中位置升序，`index` 为 0-based 序号。`start_word` / `end_word` 为
> 命中首、末字符所在词序号（0-based、含端点）；`start_char` / `end_char` 为命中
> 在首词、末词内的 Unicode 字符偏移区间 `[start_char, ...)` / `(..., end_char)`：
> 命中完全落在一个词内时 `start_word == end_word`；跨词命中覆盖中间词的全部字符。
> 上例“使用优惠码”横跨词 0、1：词 0 的字符区间 `[2, 4)` 与词 1 整体被替换；
> “别错过”属于另一说话人，不参与匹配。

### curl 示例

```bash
curl -s http://localhost:8000/redact-preview \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":800,"text":"记得使用","speaker":"A"},{"start_ms":800,"end_ms":1400,"text":"优惠码","speaker":"A"},{"start_ms":1400,"end_ms":2000,"text":"八八八","speaker":"A"},{"start_ms":2200,"end_ms":3400,"text":"别错过","speaker":"B"}],"phrases":["使用优惠码","八八八"]}'
```

### 无命中

```json
{
  "words": [
    {"start_ms": 0, "end_ms": 800, "text": "记得使用", "speaker": "A"}
  ],
  "hits": []
}
```

## 预告片片段组合：`POST /trailer-plan`

从 1～20 个候选片段中选出**词区间互不重叠、总时长不超 `budget_ms`** 的组合，
使 1～3 个**必选说话人**各自至少在某个选中片段中出现一次。候选片段必须是
**单一说话人的连续词区间**，时长取末词 `end_ms` 减首词 `start_ms`。
`words` 字段与 `/segments` 相同，并沿用同一套词序与时间校验（422 错误码一致）。

服务地址：`POST http://localhost:8000/trailer-plan`

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 800,  "text": "开场白",   "speaker": "A"},
    {"start_ms": 800,  "end_ms": 1600, "text": "欢迎收听", "speaker": "A"},
    {"start_ms": 1600, "end_ms": 2400, "text": "今天聊聊", "speaker": "B"},
    {"start_ms": 2400, "end_ms": 3200, "text": "动态规划", "speaker": "B"},
    {"start_ms": 3200, "end_ms": 4000, "text": "下期再见", "speaker": "A"}
  ],
  "candidates": [
    {"start_word": 0, "end_word": 1, "score": 5},
    {"start_word": 2, "end_word": 3, "score": 7},
    {"start_word": 4, "end_word": 4, "score": 3}
  ],
  "budget_ms": 2400,
  "required_speakers": ["A", "B"]
}
```

字段说明（`words` 之外）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `candidates` | 数组，1～20 个 | 候选片段；每项含 `start_word`/`end_word`（0-based、含端点的词序号）与非负整数 `score` |
| `budget_ms` | 非负整数 | 选中片段的总时长上限（毫秒） |
| `required_speakers` | 非空字符串数组，1～3 个 | 必选说话人，每个至少在选中片段中出现一次；重复项按去重处理 |

### 组合规则

1. **候选合法性**：候选词序号须在词序列范围内、`start_word <= end_word`，且区间内
   所有词的 `speaker` 相同（否则返回 422，详见错误表）；候选时长为
   `words[end_word].end_ms - words[start_word].start_ms`；
2. **约束**：选中片段的词区间互不重叠（端点相接允许）；总时长 `<= budget_ms`；
   每个必选说话人至少在某个选中片段中出现一次（非必选说话人的片段可自由加入）；
3. **目标**：先**最大化总评分**，同分**最小化总时长**，再按**原时间顺序排列的
   候选序号序列**字典序取最小者（序号即 `candidates` 数组中的 0-based 下标）；
4. 不存在满足全部约束的组合时返回 **422**（`infeasible_trailer_plan`）。

### 响应示例（200）

```json
{
  "clips": [
    {
      "index": 0,
      "candidate_index": 1,
      "speaker": "B",
      "score": 7,
      "start_word": 2,
      "end_word": 3,
      "source_start_ms": 1600,
      "source_end_ms": 3200,
      "duration_ms": 1600,
      "trailer_start_ms": 0,
      "trailer_end_ms": 1600
    },
    {
      "index": 1,
      "candidate_index": 2,
      "speaker": "A",
      "score": 3,
      "start_word": 4,
      "end_word": 4,
      "source_start_ms": 3200,
      "source_end_ms": 4000,
      "duration_ms": 800,
      "trailer_start_ms": 1600,
      "trailer_end_ms": 2400
    }
  ],
  "total_score": 10,
  "total_duration_ms": 2400
}
```

> `clips` 按源时间升序返回，`index` 为 0-based 序号，`candidate_index` 为原候选
> 序号。`source_start_ms`/`source_end_ms` 为片段在原始时间轴上的源时刻（首词
> `start_ms`、末词 `end_ms`）；`trailer_start_ms`/`trailer_end_ms` 为预告片内
> 时刻——从 0 起按片段时长依次累加（上例第一段 0～1600ms、第二段 1600～2400ms）。
> 上例中候选 0（A，1600ms/5 分）与候选 1（B，1600ms/7 分）组合需 3200ms 超预算，
> 覆盖 A、B 的最优组合为候选 1 + 候选 2（10 分、2400ms）。

### curl 示例

```bash
curl -s http://localhost:8000/trailer-plan \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":800,"text":"开场白","speaker":"A"},{"start_ms":800,"end_ms":1600,"text":"欢迎收听","speaker":"A"},{"start_ms":1600,"end_ms":2400,"text":"今天聊聊","speaker":"B"},{"start_ms":2400,"end_ms":3200,"text":"动态规划","speaker":"B"},{"start_ms":3200,"end_ms":4000,"text":"下期再见","speaker":"A"}],"candidates":[{"start_word":0,"end_word":1,"score":5},{"start_word":2,"end_word":3,"score":7},{"start_word":4,"end_word":4,"score":3}],"budget_ms":2400,"required_speakers":["A","B"]}'
```

### 无可行组合

必选说话人在任何候选中都不出现，或满足覆盖与预算约束的组合不存在时，返回 422：

```json
{
  "error": {
    "code": "infeasible_trailer_plan",
    "message": "不存在满足全部约束的预告片片段组合",
    "candidate_count": 3,
    "budget_ms": 2399
  }
}
```

## 播放变速规划：`POST /playback-plan`

把**连续同一说话人**的词合并为语速块（至多 **20** 块），为每块从
**100% / 125% / 150%** 中选一种语速：块内自首词 `start_ms` 至末词 `end_ms`
整段变速，**块间静音保持原时长**。在总播放时长不超 `max_duration_ms` 的
约束下，选出总变速代价最小的语速方案。`words` 字段与 `/segments` 相同，
并沿用同一套词序与时间校验（422 错误码一致）。

服务地址：`POST http://localhost:8000/playback-plan`

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 1000, "text": "开场白",   "speaker": "A"},
    {"start_ms": 1000, "end_ms": 2000, "text": "欢迎收听", "speaker": "A"},
    {"start_ms": 3000, "end_ms": 4000, "text": "今天聊聊", "speaker": "B"},
    {"start_ms": 4000, "end_ms": 5000, "text": "动态规划", "speaker": "B"},
    {"start_ms": 5500, "end_ms": 6500, "text": "下期再见", "speaker": "A"}
  ],
  "max_duration_ms": 5700
}
```

字段说明（`words` 之外）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `max_duration_ms` | 非负整数 | 目标最长播放时长（毫秒）：各块播放时长与块间静音之和不得超过它 |

### 变速规则

1. **语速块**：连续同一说话人的词合成一块，块数为说话人连续区间数，至多
   **20** 块，超限返回 422（`too_many_blocks`）；块原始时长为
   末词 `end_ms` 减首词 `start_ms`（块内词间静音一并变速）；
2. **播放时长**：每块整段变速，播放时长为 `ceil(原时长 × 100 / 语速)`；
   块间静音（前块末词 `end_ms` 到后块首词 `start_ms`）保持原时长；
   播放时间轴从 0 起按块播放时长与块间静音依次累加；
3. **约束**：总播放时长（各块播放时长 + 块间静音）`<= max_duration_ms`；
4. **目标**：最小化总变速代价 `Σ 原时长 × (语速 - 100)²`；同代价取
   **总播放时长最长**者，再取**语速序列字典序最小**者（100 < 125 < 150）；
5. 全部块取 150% 仍超时（或块间静音本身已超目标）时返回 **422**
   （`infeasible_playback_plan`）。

### 响应示例（200）

```json
{
  "blocks": [
    {
      "index": 0,
      "start_word": 0,
      "end_word": 1,
      "speaker": "A",
      "source_start_ms": 0,
      "source_end_ms": 2000,
      "duration_ms": 2000,
      "speed": 125,
      "playback_duration_ms": 1600,
      "playback_start_ms": 0,
      "playback_end_ms": 1600
    },
    {
      "index": 1,
      "start_word": 2,
      "end_word": 3,
      "speaker": "B",
      "source_start_ms": 3000,
      "source_end_ms": 5000,
      "duration_ms": 2000,
      "speed": 125,
      "playback_duration_ms": 1600,
      "playback_start_ms": 2600,
      "playback_end_ms": 4200
    },
    {
      "index": 2,
      "start_word": 4,
      "end_word": 4,
      "speaker": "A",
      "source_start_ms": 5500,
      "source_end_ms": 6500,
      "duration_ms": 1000,
      "speed": 100,
      "playback_duration_ms": 1000,
      "playback_start_ms": 4700,
      "playback_end_ms": 5700
    }
  ],
  "total_cost": 2500000,
  "total_duration_ms": 5700
}
```

> `blocks` 按时间升序返回，`index` 为 0-based 块序号，`start_word` / `end_word`
> 为 0-based、含端点的词序号。`source_start_ms`/`source_end_ms` 为块在原始
> 时间轴上的起止（首词 `start_ms`、末词 `end_ms`）；`playback_start_ms`/
> `playback_end_ms` 为播放时间轴上的起止——从 0 起按块播放时长与块间静音
> 依次累加（上例块 0 播放 0～1600ms，静音 1000ms 后块 1 播放 2600～4200ms，
> 再静音 500ms 后块 2 播放 4700～5700ms）。
> 上例全部 100% 需 6500ms 超目标；(125, 125, 100) 的播放时长为
> 1600+1600+1000=4200ms，加静音 1500ms 恰好 5700ms，代价
> `2000×625 + 2000×625 + 0 = 2500000` 为可行方案中最小。

### curl 示例

```bash
curl -s http://localhost:8000/playback-plan \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":1000,"text":"开场白","speaker":"A"},{"start_ms":1000,"end_ms":2000,"text":"欢迎收听","speaker":"A"},{"start_ms":3000,"end_ms":4000,"text":"今天聊聊","speaker":"B"},{"start_ms":4000,"end_ms":5000,"text":"动态规划","speaker":"B"},{"start_ms":5500,"end_ms":6500,"text":"下期再见","speaker":"A"}],"max_duration_ms":5700}'
```

### 无可行变速方案

全部块取 150% 仍超过目标时长（或块间静音本身已超目标）时，返回 422：

```json
{
  "error": {
    "code": "infeasible_playback_plan",
    "message": "不存在满足全部约束的播放变速方案",
    "block_count": 3,
    "max_duration_ms": 4834
  }
}
```

## 叠话统计报告：`POST /crosstalk-report`

面向多说话人交叠场景：统计**各说话人发声覆盖**、至少两人同时发声的
**叠话区间**、以及**各说话人对的总重叠毫秒数**。与上述接口不同，本接口的
`words`（结构相同，至多 **2000** 词）**允许乱序与交叠**，不校验词序，
仅逐词校验 `end_ms >= start_ms`；不同说话人至多 **20** 名。
**零时长词**（`end_ms == start_ms`）不参与任何统计。每个词视为
**左闭右开**区间 `[start_ms, end_ms)`，端点相接不计重叠。

服务地址：`POST http://localhost:8000/crosstalk-report`

```json
{
  "words": [
    {"start_ms": 1000, "end_ms": 3000, "text": "没错",     "speaker": "A"},
    {"start_ms": 0,    "end_ms": 2000, "text": "我觉得",   "speaker": "A"},
    {"start_ms": 1500, "end_ms": 2500, "text": "对对对",   "speaker": "B"},
    {"start_ms": 4000, "end_ms": 5000, "text": "下期再见", "speaker": "B"}
  ]
}
```

### 统计规则

1. **发声覆盖**：同一说话人所有词区间取**并集**，重叠部分只计一次，
   `covered_ms` 为并集总毫秒数（仅含零时长词的说话人为 0）；
2. **叠话区间**：至少两人同时发声的时间段，按**活跃说话人集合**的变化
   切分，仅合并相邻且集合相同的片段；每段给出 `start_ms` / `end_ms` /
   `duration_ms` 与按标识排序的 `speakers`；
3. **说话人对重叠**：每段叠话时长计入其活跃集合的**全部两人组合**——
   三人同时发声的时长分别计入三对；仅列出 `overlap_ms > 0` 的对，
   按 `(speaker_a, speaker_b)` 升序返回；
4. 某词 `end_ms < start_ms` 返回 422（`invalid_time_range`，`word_index`
   为**输入序**）；不同说话人超过 20 名返回 422（`too_many_speakers`）；
   `words` 为空或超过 2000 个返回 422（`invalid_request`）。

### 响应示例（200）

```json
{
  "speakers": [
    {"speaker": "A", "covered_ms": 3000},
    {"speaker": "B", "covered_ms": 2000}
  ],
  "segments": [
    {"index": 0, "start_ms": 1500, "end_ms": 2500, "duration_ms": 1000, "speakers": ["A", "B"]}
  ],
  "pair_overlaps": [
    {"speaker_a": "A", "speaker_b": "B", "overlap_ms": 1000}
  ]
}
```

> `speakers` 按标识排序：A 的词区间 `[0,2000)` 与 `[1000,3000)` 并集为
> `[0,3000)` 共 3000ms（重叠的 1000ms 只计一次）；B 为
> `[1500,2500)` ∪ `[4000,5000)` 共 2000ms。
> 叠话区间为 `[1500,2500)`：`[1500,2000)` 与 `[2000,2500)` 的活跃集合
> 都是 `{A,B}`，端点相接且集合相同，合并为一段；`[4000,5000)` 只有 B 一人，
> 不计入。`segments` 按时间升序、`index` 为 0-based 序号；无叠话时
> `segments` 与 `pair_overlaps` 均为空数组。

### curl 示例

```bash
curl -s http://localhost:8000/crosstalk-report \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":1000,"end_ms":3000,"text":"没错","speaker":"A"},{"start_ms":0,"end_ms":2000,"text":"我觉得","speaker":"A"},{"start_ms":1500,"end_ms":2500,"text":"对对对","speaker":"B"},{"start_ms":4000,"end_ms":5000,"text":"下期再见","speaker":"B"}]}'
```

## 修订稿重对齐逐词时间：`POST /transcript-retime`

人工修订后的词序列（只给 `text`/`speaker`、没有时间）需要重新挂回原稿时间轴：
按整词 `(text, speaker)` 在原稿 `words` 与修订稿 `revisions` 之间求**数量最多的
保序匹配**（最长公共子序列 LCS，不要求连续），命中词直接沿用所匹配原稿词的原
`start_ms`/`end_ms`；未命中的**连续修订词**在相邻锚点间隔内**均分**时间。
`words` 与其他接口一样按时间排列且互不重叠（沿用同一套 422 校验），两侧各不超过
**300** 词。

服务地址：`POST http://localhost:8000/transcript-retime`

```json
{
  "words": [
    {"start_ms": 1000, "end_ms": 1500, "text": "大家好", "speaker": "A"},
    {"start_ms": 2000, "end_ms": 2500, "text": "今天",   "speaker": "A"},
    {"start_ms": 3000, "end_ms": 3500, "text": "再见",   "speaker": "B"},
    {"start_ms": 4000, "end_ms": 4500, "text": "拜拜",   "speaker": "B"}
  ],
  "revisions": [
    {"text": "嗯",   "speaker": "A"},
    {"text": "今天", "speaker": "A"},
    {"text": "那个", "speaker": "A"},
    {"text": "再见", "speaker": "B"},
    {"text": "拜拜", "speaker": "B"}
  ]
}
```

字段说明（`words` 之外）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `revisions` | 非空数组，1～300 项 | 修订词序列，每项只含非空 `text` 与非空 `speaker`（无时间字段） |

> `words` 同样为 1～300 个；任一侧为空或超过 300、修订项缺 `text`/`speaker`
> 或为空字符串均返回 422（`invalid_request`）。

### 对齐规则

1. **匹配单位**：整词 `(text, speaker)`——文本与说话人都相同才算命中
   （同一文本但说话人不同不匹配），单词文本不拆分；
2. **目标一（最多）**：在两侧之间求索引严格递増的保序匹配，使命中词对数量最大
   （即 LCS 长度）；
3. **目标二（同分裁决）**：数量相同时取**匹配索引对序列**
   `((原稿 i, 修订 j), ...)` **字典序最小**者——逐对比较，先比原稿索引 `i`、
   `i` 相同再比修订索引 `j`；
4. **命中词**：保留所匹配原稿词的原 `start_ms` / `end_ms`；
5. **未命中连续词均分**：每段夹在相邻命中锚点之间的未命中修订词（一个 run）
   均分锚点间隔。`c` 个词均分长度 `span`：每词时长 `span // c` 毫秒，余数
   `span % c` **从前往后**分配（前若干词各多 1 毫秒），词间端点相接、末词恰好
   结束于右锚点；
6. **虚拟锚点**：第一个命中词之前的 run，左锚点取**原稿首词 `start_ms`**、
   右锚点取首个命中词的 `start_ms`；最后一个命中词之后的 run，左锚点取末命中词
   的 `end_ms`、右锚点取**原稿末词 `end_ms`**；
7. **无匹配**：没有任何命中时，全部修订词在
   `[原稿首词 start_ms, 原稿末词 end_ms)` 全长内均分；
8. 某 run 的可用间隔不足每词 1 毫秒（`span < 词数`）时返回 **422**
   （`infeasible_retime`），并在错误体中指出该 run 的**修订词范围**。

### 响应示例（200）

```json
{
  "words": [
    {"index": 0, "start_ms": 1000, "end_ms": 2000, "text": "嗯",   "speaker": "A", "source_word_index": null},
    {"index": 1, "start_ms": 2000, "end_ms": 2500, "text": "今天", "speaker": "A", "source_word_index": 1},
    {"index": 2, "start_ms": 2500, "end_ms": 3000, "text": "那个", "speaker": "A", "source_word_index": null},
    {"index": 3, "start_ms": 3000, "end_ms": 3500, "text": "再见", "speaker": "B", "source_word_index": 2},
    {"index": 4, "start_ms": 4000, "end_ms": 4500, "text": "拜拜", "speaker": "B", "source_word_index": 3}
  ],
  "matched_count": 3
}
```

> `words` 按修订顺序返回完整逐词时间；`index` 为修订词序号（0-based）。
> `source_word_index` 为命中原稿词序号（0-based），未命中为 `null`。
> 上例“今天/再见/拜拜”三命中并沿用原时间；“嗯”是首个命中词之前的 run，在
> 虚拟锚点 `[首词 start=1000, “今天” start=2000)` 内占满 1000ms；“那个”在
> `[“今天” end=2500, “再见” start=3000)` 内占 500ms；末命中词即原稿末词，
> 其后没有 run。`matched_count` 为命中词对数。

### curl 示例

```bash
curl -s http://localhost:8000/transcript-retime \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":1000,"end_ms":1500,"text":"大家好","speaker":"A"},{"start_ms":2000,"end_ms":2500,"text":"今天","speaker":"A"},{"start_ms":3000,"end_ms":3500,"text":"再见","speaker":"B"},{"start_ms":4000,"end_ms":4500,"text":"拜拜","speaker":"B"}],"revisions":[{"text":"嗯","speaker":"A"},{"text":"今天","speaker":"A"},{"text":"那个","speaker":"A"},{"text":"再见","speaker":"B"},{"text":"拜拜","speaker":"B"}]}'
```

### 无匹配时均分全长

两侧没有任何 `(text, speaker)` 相同的词时，全部修订词在原稿全长内均分。
例如原稿全长 `[0, 2000)`、3 个修订词：`span=2000, base=666, rem=2`，余数
从前往后分，得到 667、667、666 毫秒：

```json
{
  "words": [
    {"index": 0, "start_ms": 0,    "end_ms": 667,  "text": "丙", "speaker": "A", "source_word_index": null},
    {"index": 1, "start_ms": 667,  "end_ms": 1334, "text": "丁", "speaker": "A", "source_word_index": null},
    {"index": 2, "start_ms": 1334, "end_ms": 2000, "text": "戊", "speaker": "A", "source_word_index": null}
  ],
  "matched_count": 0
}
```

### 锚点间隔不足

某段连续未命中修订词的可用间隔不足每词至少 1 毫秒时，返回 422 并指出修订词
范围（0-based、含端点）。下例两命中词“甲”(`end=500`)、“乙”(`start=501`)
之间只有 1ms，却要塞入 2 个修订词（需 2ms）：

```json
{
  "error": {
    "code": "infeasible_retime",
    "message": "修订词第 1～2 个（共 2 个）的相邻锚点间隔仅 1 毫秒，不足每词至少 1 毫秒（需 2 毫秒）",
    "revision_start_index": 1,
    "revision_end_index": 2,
    "revision_word_count": 2,
    "available_ms": 1,
    "required_ms": 2
  }
}
```

## 访谈问答索引：`POST /qa-pairs`

为访谈转写生成**可回听的问答索引**：先把词序列按**相邻同一说话人**合并成
发言轮次，识别其中的**提问**，再从满足说话人与时间间隔条件的**回答**轮次中
做**保序一对一配对**。`words` 字段与 `/segments` 相同，但至多 **300** 个词，
并沿用同一套词序与时间校验（422 错误码一致）。

服务地址：`POST http://localhost:8000/qa-pairs`

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 500,  "text": "你觉得", "speaker": "A"},
    {"start_ms": 500,  "end_ms": 1000, "text": "行吗？", "speaker": "A"},
    {"start_ms": 1000, "end_ms": 1500, "text": "我觉得", "speaker": "B"},
    {"start_ms": 1500, "end_ms": 2000, "text": "可以",   "speaker": "B"}
  ],
  "max_gap_ms": 1000
}
```

字段说明（`words` 之外）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `max_gap_ms` | 非负整数 | 回答首词 `start_ms` 与问题末词 `end_ms` 的最大间隔（毫秒，含边界） |

### 轮次合并与提问识别

1. **发言轮次**：相邻且 `speaker` 相同的词合并为一轮（即说话人连续区间），
   轮次文本为各词 `text` **原样拼接（不插空格）**；轮次起止时间取首词
   `start_ms` 与末词 `end_ms`；
2. **提问**：轮次文本**去掉尾部空白**（`str.rstrip`，覆盖空格/制表符/换行/
   全角空格等 Unicode 空白）后，以全角 `？` 或半角 `?` 结尾者；其余轮次
   （含去空白后为空者）均可作为**回答**候选。返回的轮次 `text` 始终保留
   原始拼接结果（不去空白）。

### 配对规则

一条提问 `q` 与一条回答 `a` 构成候选对，当且仅当同时满足：

1. **其他说话人**：`a.speaker != q.speaker`；
2. **始于问题结束后**：`a.start_ms >= q.end_ms`（输入词互不重叠，端点相接
   允许；该条件已蕴含二者在时间上不重叠）；
3. **间隔不超过窗口**：`a.start_ms - q.end_ms <= max_gap_ms`（间隔非负，
   故 `max_gap_ms = 0` 时仅端点相接可配）。

在**全部候选**上求一个**保序一对一匹配**：每条提问至多配一条回答、每条回答
至多配一条提问，配对双方按轮次顺序都严格递增。优化目标依次为：

1. **配对数最多**；
2. 配对数相同时，**Σ 回答文本字符数**（Unicode code point，按返回的原样
   拼接文本计）**最大**；
3. 仍相同时，取 `(问题首词序号, 回答首词序号)` **配对序列字典序最小**者——
   逐对比较，先比问题首词序号，相同再比回答首词序号。

### 响应示例（200）

```json
{
  "pairs": [
    {
      "index": 0,
      "question": {
        "start_word": 0, "end_word": 1,
        "start_ms": 0, "end_ms": 1000,
        "speaker": "A", "text": "你觉得行吗？"
      },
      "answer": {
        "start_word": 2, "end_word": 3,
        "start_ms": 1000, "end_ms": 2000,
        "speaker": "B", "text": "我觉得可以"
      }
    }
  ],
  "unpaired_question_start_words": []
}
```

> `pairs` 按问题时间（首词序号）升序返回，`index` 为 0-based 序号；
> `question` / `answer` 各自给出**词区间**（`start_word`/`end_word`，0-based、
> 含端点）、**时间区间**（`start_ms`/`end_ms`，首词 `start_ms` 与末词
> `end_ms`）、`speaker` 与原样拼接的 `text`。
> `unpaired_question_start_words` 为**未配上回答的提问首词序号**（0-based、
> 升序）。

### curl 示例

```bash
curl -s http://localhost:8000/qa-pairs \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":500,"text":"你觉得","speaker":"A"},{"start_ms":500,"end_ms":1000,"text":"行吗？","speaker":"A"},{"start_ms":1000,"end_ms":1500,"text":"我觉得","speaker":"B"},{"start_ms":1500,"end_ms":2000,"text":"可以","speaker":"B"}],"max_gap_ms":1000}'
```

### 无候选 / 无提问

没有任何候选对（如提问后无其他说话人、间隔都超过 `max_gap_ms`）时返回
**200**，`pairs` 为空数组；所有提问首词序号进入
`unpaired_question_start_words`。根本没有提问轮次时，两个字段均为空数组：

```json
{"pairs": [], "unpaired_question_start_words": [0]}
```

```json
{"pairs": [], "unpaired_question_start_words": []}
```

## 响度增益规划：`POST /gain-plan`

为一段音频的逐帧响度测量结果选择**整数 dB 增益**：在**真峰值不削波**与
**相邻帧增益变化受限**两条硬约束下，使增益后响度尽量贴近目标响度，同时
抑制增益抖动。输入 **1～300** 个按时间排列且互不重叠的测量帧（允许端点
相接），每帧给出积分响度与真峰值。

服务地址：`POST http://localhost:8000/gain-plan`

```json
{
  "frames": [
    {"start_ms": 0,    "end_ms": 1000, "loudness_lufs": -14, "true_peak_dbfs": -2},
    {"start_ms": 1000, "end_ms": 2000, "loudness_lufs": -20, "true_peak_dbfs": -10}
  ],
  "target_lufs": -16,
  "max_step_db": 2
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `frames` | 非空数组，1～300 项 | 按时间排列且互不重叠的测量帧 |
| `frames[].start_ms` | 非负整数 | 帧起始时间（毫秒） |
| `frames[].end_ms` | 非负整数 | 帧结束时间（毫秒），须**严格大于** `start_ms` |
| `frames[].loudness_lufs` | 整数，-80～0 | 帧积分响度（LUFS） |
| `frames[].true_peak_dbfs` | 整数，-80～0 | 帧真峰值（dBFS/dBTP） |
| `target_lufs` | 整数，-40～0 | 目标响度（LUFS） |
| `max_step_db` | 整数，0～12 | 相邻帧增益差绝对值上限（dB） |

> 帧时间还必须按时间排列（`start_ms` 不倒序）、互不重叠（下帧
> `start_ms >= 上帧 end_ms`，端点相接允许），否则返回 422。

### 增益规则

为每帧选择一个 **-12～12 的整数 dB 增益** `gain_db`，满足：

1. **峰值安全（不削波）**：`true_peak_dbfs + gain_db <= 0`，即每帧增益不
   超过 `-true_peak_dbfs`；因此真峰值为 0 的帧只能衰减（`gain_db <= 0`）；
2. **平滑**：相邻帧增益差的绝对值 `|gain_db[i] - gain_db[i-1]| <= max_step_db`
   （`max_step_db = 0` 时全部帧增益相同）；
3. **目标**：最小化总成本

   ```
   Σ_i |loudness_lufs[i] + gain_db[i] - target_lufs|
     + 2 · Σ_{i=1}^{n-1} |gain_db[i] - gain_db[i-1]|
   ```

   即“各帧增益后响度与目标的绝对偏差之和”加“2 倍相邻增益绝对变化量之和”；
4. **同分裁决**：总成本相同时，取整条**增益序列字典序最小**者
   （-12 < -11 < … < 11 < 12），即优先更早的帧取更小（更偏衰减）的增益；
5. 可行解始终存在（例如全部帧取 -12 必然满足两条硬约束），故本接口没有
   业务域“无可行方案”错误，只有输入非法时返回 422。

### 响应示例（200）

```json
{
  "frames": [
    {
      "index": 0,
      "gain_db": -2,
      "resulting_loudness_lufs": -16,
      "resulting_true_peak_dbfs": -4
    },
    {
      "index": 1,
      "gain_db": -2,
      "resulting_loudness_lufs": -22,
      "resulting_true_peak_dbfs": -12
    }
  ],
  "total_cost": 6
}
```

> `frames` 按输入帧顺序返回，`index` 为 0-based 帧序号，`gain_db` 为该帧
> 选中的整数增益；`resulting_loudness_lufs` / `resulting_true_peak_dbfs`
> 分别为增益后的响度与真峰值（后者保证 `<= 0`）。
> 上例帧 0 响度 -14、峰值 -2，理想增益 -2（且增益后峰值恰为 -4，安全）；
> 帧 1 响度 -20、峰值 -10，理想增益 +4，但受相邻差不超过 2 的限制，且
> 序列 (-2,-2) / (0,0) / (2,2) 的总成本同为 6，字典序最小取 (-2,-2)：
> 响度偏差 `|−16+16| + |−22+16| = 6`，相邻变化 0，总成本 6。

### curl 示例

```bash
curl -s http://localhost:8000/gain-plan \
  -H 'Content-Type: application/json' \
  -d '{"frames":[{"start_ms":0,"end_ms":1000,"loudness_lufs":-14,"true_peak_dbfs":-2},{"start_ms":1000,"end_ms":2000,"loudness_lufs":-20,"true_peak_dbfs":-10}],"target_lufs":-16,"max_step_db":2}'
```

### 非法输入

`frames` 为空或超过 300 个、帧数/响度/峰值/目标/步长超出取值范围或字段类型
不合法（小数值、字符串等非整数）、时间为负或 `end_ms <= start_ms`、帧时间
倒序或重叠时，均返回 **422**：结构/范围类错误为 `invalid_request`（携带
`errors`），时间类错误分别为
`invalid_time_range` / `time_out_of_order` / `overlapping_time_range`
（携带出错的 `frame_index`）。

```json
{
  "error": {
    "code": "overlapping_time_range",
    "message": "第 1 个帧的 start_ms(500) 早于前一个帧的 end_ms(1000)，相邻帧时间不允许重叠",
    "frame_index": 1
  }
}
```

## 错误响应（HTTP 422）

统一形如：`{"error": {"code": "...", "message": "...", ...}}`

| `code` | 触发条件 | 附加字段 |
| --- | --- | --- |
| `invalid_request` | 请求体结构/字段类型不合法（含空 `words`、`switch_cost` 越界、`min_gap_ms` 越界、空 `chapters`/`title`/`cue`、`min_words` 越界、`/duplicate-passages` 的 `words` 超过 20000 个、`/redact-preview` 与 `/crosstalk-report` 的 `words` 超过 2000 个、`phrases` 为空/超过 50 个/含空字符串、`candidates` 为空/超过 20 个/含负 `score`、`budget_ms` 为负、`required_speakers` 为空/超过 3 个/含空字符串、`max_duration_ms` 为负；`/transcript-retime` 的 `words`/`revisions` 为空或超过 300 个、修订项缺 `text`/`speaker` 或为空字符串；`/qa-pairs` 的 `words` 超过 300 个、`max_gap_ms` 缺失/为负/非整数；`/gain-plan` 的 `frames` 为空/超过 300 个，`loudness_lufs`/`true_peak_dbfs` 超出 -80～0、`target_lufs` 超出 -40～0、`max_step_db` 超出 0～12 或上述字段非整数） | `errors` |
| `invalid_time_range` | 某个词 `end_ms < start_ms`（`/crosstalk-report` 中 `word_index` 为输入序）；`/gain-plan` 中为某帧 `start_ms < 0` 或 `end_ms <= start_ms`（`frame_index` 为输入序） | `word_index` / `frame_index` |
| `time_out_of_order` | 相邻词 `start_ms` 倒序（`/gain-plan` 中为相邻帧 `start_ms` 倒序，附加 `frame_index`） | `word_index` / `frame_index` |
| `overlapping_time_range` | 某词 `start_ms` 早于前词 `end_ms`（重叠）（`/gain-plan` 中为相邻帧时间重叠，附加 `frame_index`） | `word_index` / `frame_index` |
| `infeasible_segmentation` | 不存在满足全部约束的分段 | `last_word_index` |
| `infeasible_ad_breaks` | 不存在满足全部约束的插播方案 | `break_count`、`candidate_count` |
| `lock_index_out_of_range` | 锁定词序号越界（含负数） | `word_index` |
| `duplicate_lock` | 同一词序号被重复锁定 | `word_index` |
| `too_many_speakers` | 候选说话人（原标注 ∪ 锁定值）超过 8 个，或 `/crosstalk-report` 的不同说话人超过 20 名 | `speaker_count` |
| `infeasible_chapter_alignment` | 某章 cue 无命中，或无法组成满足间隔约束的完整章节方案 | 无命中时为 `chapter_index`；否则为 `chapter_count`、`min_gap_ms` |
| `candidate_index_out_of_range` | 预告片候选词序号越界（含负数） | `candidate_index` |
| `candidate_range_inverted` | 预告片候选 `start_word > end_word` | `candidate_index` |
| `candidate_speaker_mixed` | 预告片候选词区间横跨多个说话人 | `candidate_index` |
| `infeasible_trailer_plan` | 不存在满足全部约束的预告片片段组合 | `candidate_count`、`budget_ms` |
| `too_many_blocks` | 同一说话人连续块数超过 20 | `block_count` |
| `infeasible_playback_plan` | 不存在满足目标最长播放时长约束的变速方案 | `block_count`、`max_duration_ms` |
| `infeasible_retime` | `/transcript-retime` 某段连续未命中修订词的相邻锚点间隔不足每词 1 毫秒 | `revision_start_index`、`revision_end_index`、`revision_word_count`、`available_ms`、`required_ms` |

### 无可行分段

当任何切分方式都无法满足硬约束时，返回 422，并在 `last_word_index` 中给出
**最早无法到达的前缀末词序号（0-based）**：即最小的满足“前 k 个词无法被任何合法
分段完整覆盖”的 k，其末词序号为 `k-1`。

```bash
curl -s http://localhost:8000/segments \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":800,"text":"太短了","speaker":"A"}]}'
```

```json
{
  "error": {
    "code": "infeasible_segmentation",
    "message": "不存在满足全部约束的分段方案",
    "last_word_index": 0
  }
}
```

（该词仅 800ms 且无法与前后词组合成 ≥1000ms 的段。）

## 算法说明

两趟动态规划 + 贪心重建，**O(n) 时间、O(n) 空间**：

1. **段可行性枚举**：每段至多 18 个词（每词至少 1 个字符），以任一词为段首时
   可行段末词至多 18 个，且利用字符数、时长随段长单调的性质提前剪枝；
2. **前向 DP**：`fwd[t]` = 恰好覆盖前 t 个词的最小总分；
3. **后向 DP**：`bwd[i]` = 覆盖后缀 `[i,n)` 的最小总分；
4. **贪心重建**：最优总分为 `fwd[n]`。从边界 0 出发，在每个当前边界枚举可行下一段：
   - 若该段直达末尾（成为末段）且总分为最优，则直接结束——它的断点序列是任何
     “继续切分”方案的严格前缀，标准字典序下最小；
   - 否则在所有能以最优总分补全后缀的候选中，取**最小的下一个断点**。
   每步贪心都在保持总分最优的前提下让断点序列的当前元素最小，归纳可得全局
   字典序最小；无任何候选则该前缀不可达。

> 早期版本曾把整条断点序列保存在 DP 状态里，会退化为 O(n²)；当前实现每状态
> 只存一个分数，字典序裁决全部推迟到重建阶段，10 万词约数秒、内存约 60MB。

广告插播点选择（`ad_breaks.py`）同样为动态规划，**O(m·k) 时间/空间**
（m 为候选数、k = break_count ≤ 5）：候选静音区间的中点随词序严格递增，
先用双指针扫出每个候选之后第一个满足 `min_spacing_ms` 的下标，再按
“恰选 j 个”分层做后缀最大 DP，最后从左向右贪心重建——每步取仍能达成
最优总静音的最小下标，即得字典序最小的时间序列。

说话人校正（`speaker_reconcile.py`）也是动态规划，**O(n·m) 时间/空间**
（m 为候选说话人数，≤ 8）：`bwd[i][k]` 表示词 i 指派为第 k 个候选说话人时
后缀 `[i, n)` 的最小代价（含词 i 的改动代价与其后全部换人代价），锁定词只
允许取锁定值。转移利用“不换人代价为 0、换人统一为 `switch_cost`”的结构，
每行预求最小值与次小值即可 O(m) 完成；重建阶段从左向右贪心，每步在仍能
达到最优总成本的候选中取字典序最小的说话人，归纳可得整条序列字典序最小。

章节时刻对齐（`chapter_align.py`）为后向 DP + 贪心重建，**O(H·log H) 时间、
O(H) 空间**（H 为各章命中候选总数）。每章候选按全局字符位升序枚举，候选时刻
随词序非降；`suf[j][p]` 表示第 j 章从其第 p 个候选（含）起为后缀 `[j, C)` 能
取得的最小绝对偏差之和，行间转移用二分（`bisect_left`）定位下一章第一个满足
`min_gap_ms` 的候选，并对每行从右向左扫出后缀最小值；重建时逐章从左向右枚举，
第一个满足间隔且“本章偏差 + 后缀最小”恰等于剩余预算的候选即全局字符位最小的
可行选择，归纳可得命中起始全局字符位序列字典序最小。

重复口播片段检测（`duplicate_passages.py`）为**确定性索引算法**：后缀数组 +
LCP 分组 + 答案二分，**最坏 O(n log² n) 时间、O(n) 额外空间**（n 为词数，
≤ 20000；全部数组按词数分配，**不按时间轴毫秒跨度分配**）。以 `(text, speaker)`
为符号，排序去重压缩为整数 id；倍增法 O(log n) 轮构造后缀数组（每轮一次
O(n log n) 排序），Kasai O(n) 求 LCP——`lcp(i,j) ≥ L` 当且仅当两后缀落在同一
“相邻 LCP 均 ≥ L”的分组内。起点对 `(i, j)` 的可用长度为 `min(lcp, j-i)`
（区间不重叠要求片段长不超过 `j-i`），并要求首词 `start_ms` 间隔达标；
词时间非降，故用双指针 O(n) 预求 `g[i]`（首个 `start_ms ≥ start_ms[i] +
min_gap_ms` 的词下标），把长度 `L` 下的合法条件化为纯下标约束
`j ≥ f(i) = max(i+L, g[i])`；`f` 随 `i` 非降，故一组起点集合 `S` 可行当且仅当
`f(min S) ≤ max S`，单趟分组扫描 O(n) 完成一次可行性判定。可行性随长度单调，
二分最大可行长度（O(n log n)）；重建时按组内升序列表，首词升序枚举、取组内
第一个 `≥ f(i)` 的次词，即得字典序最小起点对；最优长度不足 `min_words` 时
返回 `match: null`。

敏感短语遮罩预览（`redact_preview.py`）为按字符起点保留优先候选 + 顺序贪心选取，
**O(P·T + T) 时间、O(T) 额外工作内存**（P 为短语数 ≤ 50、T 为全量拼接文本
总字符数，另含短语自身 O(短语总长度)）。对每个同一说话人连续区间开长度等于
区间字符数的数组，对每条短语用 `str.find` 枚举全部出现起点（每次从 `pos + 1`
继续，允许重叠出现），每个字符起点只保留（长度降序、短语输入序号升序的）
最优候选，其余重叠候选即时丢弃；随后按起点顺序单趟扫描，起点不早于已选命中
右端即选中该起点保留的候选——同起点只留最优，被跳过的起点都与某个优先级更高
（起点更早）的已选命中重叠，与“全量候选排序后单趟扫描”结果一致。额外内存不
随重叠命中总数增长：高重复文本（如长串 `a` 配短语 `a`/`aa`/…）下重叠候选可达
短语数 × 文本长度，全量存表排序会随之放大内存。命中区间再经二分定位首末词，
换算为首末词序号与两端词内字符偏移，最后仅把命中字符逐字替换为 `█`。

预告片片段组合（`trailer_plan.py`）为排序分层 DP + 帕累托前沿 + 贪心重建，
**O(m·2^k·F) 时间/空间**（m 为候选数 ≤ 20、k 为必选说话人数 ≤ 3、F 为单状态
帕累托前沿长度）。候选按 `(start_word, end_word, 原序号)` 排序，二分预求每个
候选之后第一个词区间不重叠的位置；状态 `(i, mask)` 记录从第 i 个候选起、尚待
覆盖集合为 mask 时可达的 `(总评分, 总时长)` 帕累托前沿（同分只留最短时长，
被支配点剪除），转移为“跳过”与“选上”两支的合并；答案取状态 `(0, 全集)`
前沿中时长不超预算、评分最大（同分时长最短）的点；重建时从左向右枚举下一个
被选候选，余项恰落在下一状态前沿内即可行，每步取原候选序号最小者，归纳可得
时间顺序候选序号序列的字典序最小者。

播放变速规划（`playback_plan.py`）为折半枚举（meet-in-the-middle），
**O(3^(m/2)·m) 时间/空间**（m 为语速块数 ≤ 20，m = 20 时约 12 万个组合）。
每块仅 3 种语速，但块时长为任意整数时 `(播放时长, 代价)` 的帕累托前沿会
指数膨胀（归并剪枝几乎不生效），直接对块做 DP 不可行，故把块序列对半分开：
每种组合用三进制编码记录语速选择（编码大小顺序与语速序列字典序一致）；
右半枚举全部 `(播放时长, 代价, 编码)`，按 `(时长, 编码)` 排序后扫出每个
前缀的最优右半（代价最小、同代价时长最长、再同取编码最小）；左半按语速
序列字典序枚举，用二分在右半中找到时长不超剩余预算的前缀最优并合并，
全局按 `(总代价, -总时长)` 取最小且严格更优才替换——打平时先枚举到的
左半序列字典序更小，同一左半下右半又已取字典序最小，故最终结果即字典序
最小的语速序列。全部块取 150% 仍超时（或块间静音本身已超目标）即无可行
方案。

叠话统计报告（`crosstalk_report.py`）为事件扫描线，
**O(n log n + E·k + M·k²) 时间、O(n + M + k²) 空间**（n 为词数 ≤ 2000、
E 为不同边界时刻数 ≤ 2n、M 为输出段数 ≤ E、k 为活跃说话人数 ≤ 20）。
把每个非零时长词拆为开始/结束事件并按时刻归组，升序扫描时同一时刻
先处理结束、再处理开始，即得左闭右开语义（端点相接不计重叠）；相邻事件
时刻之间活跃说话人集合不变，用每说话人计数器维护活跃集合（同一说话人的
重叠词自然只计一次），把每个基本区间的时长累加到集合内每个说话人的覆盖；
集合大小 ≥ 2 的基本区间若与上一输出段端点相接且集合相同则合并，
否则另起新段；说话人对重叠按输出段展开——每段时长计入其活跃集合的
全部两人组合（三人同时发声的时长分别计入三对）。

修订稿重对齐逐词时间（`transcript_retime.py`）为 LCS 后向 DP + 贪心重建，
**O(n·m) 时间/空间**（n、m ≤ 300）。以 `(text, speaker)` 为符号，
`lcs[i][j]` 表示原稿后缀 `[i,n)` 与修订后缀 `[j,m)` 的最长保序匹配长度，
按标准后向递推填充；重建时在当前子矩形 `[i0,n)×[j0,m)` 内按原稿索引 `i`
升序、同 `i` 按修订索引 `j` 升序，取第一个满足
`1 + lcs[i+1][j+1] == lcs[i0][j0]` 的相同符号格——该条件保证“选此对 +
其后缀最优”恰能补满当前最优长度，而枚举顺序先 `i` 后 `j` 即取索引对字典序
最小的可行首对，归纳可得整条匹配索引对序列字典序最小；每次取对后子矩形
严格缩小，累计扫描不超过 n·m 格。命中词沿用原时间；未命中 run 按
`span // c` 均分、余数 `span % c` 从前往后逐个加 1 毫秒，首尾 run 用原稿
首词 `start_ms`、末词 `end_ms` 作虚拟锚点，无命中时整段均分全长；
任一 run `span < c` 即返回 422 并给出该 run 的修订词闭区间。

访谈问答索引（`qa_pairs.py`）为后向 DP + 贪心重建，
**O(p·r) 时间/空间**（p、r 为提问与回答轮次数，p + r ≤ 词数 ≤ 300）。先把
相邻同一说话人的词合并为轮次，按“去尾部空白后以 ？/? 结尾”划分提问与回答，
再以（说话人不同、`0 <= 回答 start - 问题 end <= max_gap_ms`）建候选边；
`dp[i][j]` 表示提问后缀 `[i,p)` 与回答后缀 `[j,r)` 上的最优值
`(配对数, 回答字符总数)`（元组先比配对数），转移取“跳过提问 / 跳过回答 /
取候选对 (i,j) 加对角后缀”三者最大，边界全为 `(0,0)`；重建时在当前子矩形内
按问题序号升序、同问题按回答序号升序，取第一个满足“取此对 + 对角后缀最优”
恰等于当前最优值的候选对——枚举顺序先问题后回答即取
（问题首词序号, 回答首词序号）字典序最小的可行首对，归纳可得整条配对序列
字典序最小；未入选的提问首词序号按升序作为未配对列表返回。

响度增益规划（`gain_plan.py`）为链式后向 DP + 贪心重建，
**O(n·G²) 时间、O(n·G) 空间**（n ≤ 300 帧、G = 25 个增益档 -12～12）。
每帧可行增益为升序整数区间 `[-12, min(12, -true_peak_dbfs_i)]`，令
`ideal_i = target_lufs - loudness_lufs_i`，则节点代价
`|loudness_lufs_i + g - target_lufs|` 即 `|g - ideal_i|`；
`bwd[i][k]` 表示帧 i 取其第 k 个可行增益时后缀 `[i,n)` 的最小代价
（含本帧节点代价与其后全部相邻变化代价 2·|Δg|），末层只计节点代价，
前层枚举满足 `|后继增益 - 当前增益| <= max_step_db` 的后继取最小。
重建时从左向右、对当前帧可行增益**升序**枚举，取第一个使
“节点代价 + 相邻变化代价 + 后继后缀最小”恰等于剩余最优代价的增益——
枚举顺序即取字典序最小的可行当前值，归纳可得整条增益序列字典序最小；
全取 -12 恒可行，故不存在无可行方案的情形。

## 测试

核心算法只依赖 Python 标准库，无需安装任何第三方包即可运行：

```bash
python3 -m unittest discover -s tests -v
```

在容器内（含 fastapi/httpx）会额外执行 HTTP 接口层测试：

```bash
docker compose run --rm api python -m unittest discover -s tests -v
```

## 项目结构

```
app/
  __init__.py
  main.py               # FastAPI 路由、异常处理（422 统一错误体）
  schemas.py            # 请求/响应 Pydantic 模型
  segmentation.py       # 输入校验 + 动态规划核心（纯标准库）
  ad_breaks.py          # 广告插播点选择（纯标准库，复用同一套输入校验）
  speaker_reconcile.py  # 说话人校正（纯标准库，复用同一套输入校验）
  chapter_align.py      # 章节时刻对齐（纯标准库，复用同一套输入校验）
  duplicate_passages.py # 重复口播片段检测（纯标准库，复用同一套输入校验）
  redact_preview.py     # 敏感短语遮罩预览（纯标准库，复用同一套输入校验）
  trailer_plan.py       # 预告片片段组合（纯标准库，复用同一套输入校验）
  playback_plan.py      # 播放变速规划（纯标准库，复用同一套输入校验）
  crosstalk_report.py   # 叠话统计报告（纯标准库，允许乱序与交叠，仅校验词内时间）
  transcript_retime.py  # 修订稿重对齐逐词时间（纯标准库，LCS 匹配 + 锚点间隔均分）
  qa_pairs.py           # 访谈问答索引（纯标准库，轮次合并 + 保序一对一配对 DP）
  gain_plan.py          # 响度增益规划（纯标准库，链式后向 DP + 贪心字典序重建）
  srt.py                # SRT 渲染
  errors.py             # DomainError
tests/             # unittest：核心算法、暴力对拍、HTTP 接口层
  test_segmentation.py
  test_ad_breaks.py
  test_speaker_reconcile.py
  test_chapter_align.py
  test_duplicate_passages.py
  test_redact_preview.py
  test_trailer_plan.py
  test_playback_plan.py
  test_crosstalk_report.py
  test_transcript_retime.py
  test_qa_pairs.py
  test_gain_plan.py
  test_differential.py
  test_api.py
Dockerfile
docker-compose.yml
requirements.txt
```
