# Golden A/B/C/D（a1-6 §31）

a1-5 要求文档里有 A/B/C/D 四级 golden；§31 要求它们**变成测试语义**而不是名词。

| 级别 | 名称 | 管线 | 由什么证明 |
| --- | --- | --- | --- |
| A | extraction | HTML → blocks | `tests/test_guide_real_parse.py` + `tests/test_guide_blocks.py`，跑 `tests/fixtures/guides/sample_{17173,gamersky,3dm,taptap}.html` |
| B | matching | blocks → target | `tests/test_guide_units.py` + `test_guide_match.py` + `test_guide_rematch.py`（含 MAP_LABEL 合成键与页面锚点） |
| C | publish | approved → published | `tests/test_guide_publish.py` + `test_guide_layout_render.py`（puzzle / collection / challenge 排布） |
| D | lookup | published → viewer | `tests/test_guide_atlas_api.py` + `test_guide_index.py`（by-point / index / atlas 离线查询） |

实现：`hsrmap/guides/golden.py::GOLDEN_LEVELS`（每级：id、名称、管线、含义、测试模块、fixture），
`level_status()` 检查这些文件**真实存在且含测试**并统计用例数；`guides closure-check` 现在打印：

```text
Golden regression....... 15/15 + A/B/C/D PASS
```

前半是 15 个 topic 的 `golden.json` 注册情况（§31 说这 15 个不用推翻），后半是四级测试语义；
任一级别的测试模块缺失或为空，closure 的 Golden regression 就会 FAIL（有回归测试验证这一点）。
JSON 里 `goldens.levels` 给出每级的测试文件、用例数与 fixture 存在性。
