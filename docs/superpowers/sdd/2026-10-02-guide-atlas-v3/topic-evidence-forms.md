# 主题证据形态（topic evidence forms）

一个主题用文字攻略能不能当**完整来源**，取决于它的解谜/任务是什么类型。这是搜索与证据
策略的第一性问题（a1-6 §六/§七），不是事后统计。

## 分类

| 形态 | 含义 | 文字攻略是否足够 | 例子 |
| --- | --- | --- | --- |
| `INPUT_SEQUENCE` | 只需要方向/按键序列 | ✅ 足够 | 黄金替罪羊（上下左右四键）、浮脂溯源 ROTATE |
| `MODULE_PUZZLE` | 模块移动与旋转 | ✅ 足够 | 梦境迷钟 |
| `TRANSFORM_PUZZLE` | 变身 + 机关，点位靠图 | ⚠️ 操作可写、定位不行 | 小小哈努行动 |
| `PLATFORMER` | 平台跳跃、精确走位 | ❌ 不足 | 二次元 JUMP |
| `COLLECTIBLE_SPOT` | 3D 隐藏点位收集 | ❌ 不足 | 无名尘灵、若虫、折纸小鸟 |
| `LOCATION_TALK` | 到点对话 | ⚠️ 对话可写、定位不行 | 王下一桶 |
| `MIXED` / `UNKNOWN` | 未评估 | 不假设 | — |

数据放在主题档案 `hsrmap/guides/topics/profiles/*.yaml` 的 `evidence_form` 块里：

```yaml
evidence_form:
  kind: INPUT_SEQUENCE
  text_sufficient: true
  reason: 解谜只需上下左右四个方向键，方向序列用文字就能完整表达
```

`hsrmap/guides/topics/evidence.py` 读它，`guides evidence-forms` 列出来（含每个主题还剩多少
缺源），`guides search-plan` 会把主题的证据形态与检索建议写进计划。

## 与「完成要求」正交（2026-10 新增）

「文字够不够表达这个解谜」和「这个点位要不要解」是两件事，档案里分成两个块：

```yaml
evidence_form:            # 这份攻略能不能用文字承载（搜索策略）
  kind: COLLECTIBLE_SPOT
  text_sufficient: false
completion:               # 玩家拿到这个点位要走到哪一步（完成判定）
  requirement: LOCATE_ONLY
  solve_kind: NONE
```

四个组合都真实存在：若虫（`COLLECTIBLE_SPOT` + `LOCATE_ONLY`）、浮脂溯源
（`INPUT_SEQUENCE` + `LOCATE_AND_SOLVE`）、哈努（`TRANSFORM_PUZZLE` + `LOCATE_AND_SOLVE`）、
二次元 JUMP（`PLATFORMER` + `LOCATE_AND_SOLVE`，而且官方详情为空 —— 唯一一个
`text_sufficient: false` 又 `LOCATE_AND_SOLVE`、连定位证据都没有的主题）。
完成判定、六状态与工作队列见 [guide-completeness.md](guide-completeness.md)。

## 对流程的三个影响

1. **搜索只找对的形态**：`text_sufficient: true` 的主题只找图文/纯文字攻略；其余主题的文字
   攻略**只能当范围证据**（「该地图共 N 个」这类计数），点位级绑定要等视觉证据。
2. **证据读取器按形态补**：黄金替罪羊的方向序列、`位置：…` 逐条定位、`第一个/第二个` 编号，
   这些之所以算数，是因为对输入序列型解谜来说「一条序列 = 一个点位」。
3. **优先级**：同样是缺源，先补 `text_sufficient` 的主题，因为一篇文字攻略就能完整落地；
   视觉型主题先攒范围证据（分区数量），等视觉定位能力就绪再定点。

## 当前结论（2026-10，按完成模型的状态数）

- 文字足够（`text_sufficient: true`）：`floating_grease`（缺解法 23）、`golden_scapegoat`（缺解法 43）、
  `dream_ticker`（缺解法 15）——这三条线只能靠页面文字，值得继续投；
- 文字不足但官方点位图可用：`nymph`（缺 2）、`nameless_dust_spirit`（缺 1）、
  `king_bucket`（缺 17）、`hanu`（缺 6）、`origami_bird`（仅范围 40）——**官方点位详情**
  （一行位置说明 + 官方截图）就是它们的完整攻略，见 `official-point-details.md`；
- 既没有文字解法、也没有官方详情：`jump`（无证据 128 + 缺解法 9）、`miracle_orb`（无证据 9）、
  `zagreus_hand`（缺解法 2）、`pioneer_fairy`（缺解法 1）。