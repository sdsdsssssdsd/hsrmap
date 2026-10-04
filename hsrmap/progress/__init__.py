"""个人进度层（a1-9 Phase 6）：把「官方账号看到的进度」和「本地判定」隔离开。

三条边界（写进代码，不只写进文档）：

1. **只读**：这里只允许调用 `mutating=false` 的端点；`save_point_status` 之类在框架层被拒绝
   （`endpoints.read_contract`），不是靠开发者自觉；
2. **凭据不进任何持久化**：cookie 只从环境变量 `HSRMAP_HOYOLAB_COOKIE` 读，只活在内存里，
   不打印、不落盘、不序列化、不进异常、不进 debug dump（`cookie.Credential` 的 repr 是脱敏的）；
3. **语义不猜**：官方地图的标记状态（`map_mark`）在 Gate 0 验证之前**不允许**推导出
   `completed`；只有 `manual`（用户自己勾的）和已验证的 `game_obtained` 才有资格。

模块分工（数据只往一个方向流，不回流）：

```text
realm / endpoints / cookie / adapter   合同与凭据边界（只读客户端）
        ↓
hoyolab / probe                        语义探针：role → treasure → point status（只出报告）
        ↓
models / store                         观察入库：manual / map_mark / game_obtained / unknown
        ↓
resolver                               语义裁决：谁有资格推导 completed
        ↓
diff                                   四桶差异 + 显式合并（默认 dry-run）
        ↓
atlas                                  remaining = 官方可收集 − 有效完成
        ↓
routes                                 区域聚类 + 坐标就近排序
```

没有 cookie 时，import 本包不产生任何网络行为；Guide Atlas 的既有流程完全不受影响。
"""

from __future__ import annotations

__all__ = [
    "realm",
    "endpoints",
    "cookie",
    "adapter",
    "hoyolab",
    "probe",
    "models",
    "store",
    "resolver",
    "diff",
    "atlas",
    "routes",
]
