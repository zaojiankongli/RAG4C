"""文档源接入层：把「本地目录 / 远程官方文档仓库 / …」统一成可增量同步的入库源。

三层结构，各自职责单一：

- :mod:`sources.base`     契约（``DocumentSource`` / ``FetchedDocument``）
- :mod:`sources.registry` 插件注册表（新增一种源 = 一个类 + 一行注册）
- :mod:`sources.runner`   执行器（清单解析、增量判定、批量入库、删除清理）

导入本包即完成内置源注册（``github_repo`` / ``local_dir``），与
:mod:`indexing.parsers` 的形态一致。

用法::

    from sources import SourceSyncer, load_manifest
    for spec in load_manifest(Path("config/sources.json")):
        print(SourceSyncer(pipeline, cache_dir).sync(spec).summary())
"""
from __future__ import annotations

from sources.base import DocumentSource, FetchedDocument, SourceError, content_sha256
from sources.registry import (
    SourcePlugin,
    create_source,
    list_source_plugins,
    register_source_plugin,
    unregister_source_plugin,
)
from sources.runner import SourceSpec, SourceSyncer, SyncReport, load_manifest, make_doc_id

# 副作用导入：注册内置源。放在最后，避免与上面的符号导入形成循环。
from sources import plugins as _plugins  # noqa: E402,F401

__all__ = [
    "DocumentSource",
    "FetchedDocument",
    "SourceError",
    "SourcePlugin",
    "SourceSpec",
    "SourceSyncer",
    "SyncReport",
    "content_sha256",
    "create_source",
    "list_source_plugins",
    "load_manifest",
    "make_doc_id",
    "register_source_plugin",
    "unregister_source_plugin",
]
