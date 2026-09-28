"""pytest 全局约定（本仓此前没有 conftest，这是第一份）。

只做一件事：**让"外部依赖不可达"表现为快速失败，而不是挂住。**

背景（2026-09-28 实测）：目录库指向 `192.168.100.128:3307`，虚拟机上的 MySQL
没开。目录库的连接池带 `pool_pre_ping=True`，**每次取连接都会先探活**，而探活
要等驱动的默认超时（pymysql 10 秒）才失败。整条套件里这样的取连接几十次，
加起来就是"跑了十几分钟才到 13%"——看起来像套件挂了，其实是在排队等超时。

把 `catalog.connect_timeout_s` 在测试会话里压到 1 秒后，同样的不可达会立刻
报错：结论一模一样（连不上就是连不上），但反馈从"十几分钟"回到"秒级"。

这一份**不改变任何测试对外部依赖的断言**：需要 MySQL 的用例仍然会失败（没有
MySQL 就该失败），只是不再把整条套件拖住。想让它们真的通过，就把 MySQL 起起来。

可用开关：
- `RAG4C_TEST_CONNECT_TIMEOUT_S`：覆盖这里的默认 1 秒；
- `RAG4C_TEST_SKIP_EXTERNAL=1`：暂未启用（留作后续"按依赖标记跳过"的扩展点）。
"""
from __future__ import annotations

import os

#: 测试会话里的连接等待上限（秒）。生产默认仍是 10 秒，见 config.settings。
_TEST_CONNECT_TIMEOUT_S = "1"

# 端点探活在测试会话里默认关闭：大量用例用桩客户端 / 不存在的地址，探活会把
# 它们挡在真正被测的代码之外。想专门验探活行为的用例自己打开它。
if not os.environ.get("RAG4C_RETRY_ENDPOINT_PROBE_ON"):
    os.environ["RAG4C_RETRY_ENDPOINT_PROBE_ON"] = "false"

if not os.environ.get("RAG4C_CATALOG_CONNECT_TIMEOUT_S"):
    os.environ["RAG4C_CATALOG_CONNECT_TIMEOUT_S"] = os.environ.get(
        "RAG4C_TEST_CONNECT_TIMEOUT_S", _TEST_CONNECT_TIMEOUT_S
    )
