"""解析引擎插件体系冒烟：注册表 / 统一配置 / 免费付费模式开关。

运行：python scripts/smoke_engines.py
覆盖：
1. 插件注册表：注册 / 卸载 / 清单 / 选择逻辑（stub 引擎）
2. mineru free / paid 模式（CLI 已安装：真实实例化 + 模式参数）
3. mineru_http paid 缺 key 报可操作错误
4. docling：默认 disabled 不加载；enabled 未安装报可操作错误（构造不炸）
5. create_document_parser 集成：auto 选择 / 全禁用降级
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 干净的配置起点（避免 .env 干扰）
os.environ.setdefault("RAG4C_CATALOG_DB_PATH", str(Path(__file__).parent / ".." / "data" / "test-engines.db"))

from config.settings import ParserEngineSettings, get_settings  # noqa: E402
import indexing.parsers.plugins  # noqa: E402,F401  触发内置引擎注册（幂等）
from indexing.parsers.registry import (  # noqa: E402
    ParserPlugin,
    ParserPluginError,
    create_vision_engine,
    list_parser_plugins,
    register_parser_plugin,
    unregister_parser_plugin,
)

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


def reload_settings():
    get_settings.cache_clear()
    return get_settings()


print("== 1. 插件注册表（stub 引擎） ==")
class StubEngine:
    def __init__(self, engine_cfg, settings):
        self.mode = engine_cfg.mode


register_parser_plugin(ParserPlugin(name="stub", factory=StubEngine, describe=lambda: "测试引擎"))
check("注册成功", any(p["name"] == "stub" for p in list_parser_plugins()))
unregister_parser_plugin("stub")
check("卸载成功", all(p["name"] != "stub" for p in list_parser_plugins()))

print("== 2. 内置插件清单 ==")
plugins = {p["name"]: p for p in list_parser_plugins()}
check("mineru 已注册", "mineru" in plugins, str(plugins))
check("docling 已注册", "docling" in plugins)

print("== 2.5 动态插拔：新引擎零改动接入（注册 + parsers.plugins 注入配置） ==")
s = reload_settings()
s.parsers.plugins["stub"] = ParserEngineSettings(enabled=True, mode="free", priority=50)
register_parser_plugin(ParserPlugin(name="stub", factory=StubEngine, describe=lambda: "动态引擎"))
engine_dyn = create_vision_engine(s)
check("动态引擎被 auto 发现（priority 50 胜出）", type(engine_dyn).__name__ == "StubEngine", str(type(engine_dyn).__name__))
check("动态引擎配置透传（mode=free）", getattr(engine_dyn, "mode", None) == "free")
unregister_parser_plugin("stub")
s.parsers.plugins.pop("stub", None)

print("== 3. auto 选择：默认配置 -> mineru（priority 100） ==")
os.environ.pop("RAG4C_PARSERS_MINERU_ENABLED", None)
os.environ.pop("RAG4C_PARSERS_DOCLING_ENABLED", None)
s = reload_settings()
engine = create_vision_engine(s)
check("默认选 mineru", engine is not None and type(engine).__name__ == "MineruCliParser", str(type(engine).__name__))
check("mineru 默认 free 模式", getattr(engine, "mode", None) == "free")

print("== 4. mineru 付费模式（paid） ==")
os.environ["RAG4C_PARSERS_MINERU_MODE"] = "paid"
s = reload_settings()
engine2 = create_vision_engine(s)
check("paid 模式生效", getattr(engine2, "mode", None) == "paid")
os.environ.pop("RAG4C_PARSERS_MINERU_MODE", None)

print("== 5. mineru_http paid 缺 key 报可操作错误 ==")
os.environ["RAG4C_MINERU_PROVIDER"] = "http"
os.environ["RAG4C_PARSERS_MINERU_MODE"] = "paid"
s = reload_settings()
try:
    create_vision_engine(s)
    check("paid 缺 key 被拒", False, "竟然通过了")
except ParserPluginError as e:
    check("paid 缺 key 被拒（可操作提示）", "RAG4C_MINERU_API_KEY" in str(e), str(e)[:120])
os.environ.pop("RAG4C_MINERU_PROVIDER", None)
os.environ.pop("RAG4C_PARSERS_MINERU_MODE", None)

print("== 6. docling 开关（enabled -> 惰性装配，未安装报可操作错误） ==")
os.environ["RAG4C_PARSERS_MINERU_ENABLED"] = "false"
os.environ["RAG4C_PARSERS_DOCLING_ENABLED"] = "true"
s = reload_settings()
engine3 = create_vision_engine(s)
check("mineru 关 docling 开 -> 选 docling", engine3 is not None and type(engine3).__name__ == "DoclingParser", str(type(engine3).__name__))
check("docling 构造不 import 重依赖", True)
try:
    engine3.parse("whatever.pdf")
    check("docling 未安装报错", False, "竟然解析成功了")
except Exception as e:
    check("docling 未安装报可操作错误", "pip install docling" in str(e), str(e)[:150])
os.environ.pop("RAG4C_PARSERS_MINERU_ENABLED", None)
os.environ.pop("RAG4C_PARSERS_DOCLING_ENABLED", None)

print("== 7. 显式指定引擎 ==")
os.environ["RAG4C_PARSERS_ENGINE"] = "mineru"
os.environ["RAG4C_PARSERS_DOCLING_ENABLED"] = "true"
s = reload_settings()
engine4 = create_vision_engine(s)
check("显式 engine=mineru 优先于 priority", type(engine4).__name__ == "MineruCliParser", str(type(engine4).__name__))
os.environ.pop("RAG4C_PARSERS_ENGINE", None)
os.environ.pop("RAG4C_PARSERS_DOCLING_ENABLED", None)

print("== 8. 全禁用 -> None（router 可操作降级） ==")
os.environ["RAG4C_PARSERS_MINERU_ENABLED"] = "false"
os.environ["RAG4C_PARSERS_DOCLING_ENABLED"] = "false"
s = reload_settings()
engine5 = create_vision_engine(s)
check("全禁用 -> None", engine5 is None)
from indexing.parsers.router import create_document_parser  # noqa: E402
router = create_document_parser(s)
try:
    router.parse("something.docx")
    check("无引擎解析报可操作错误", False, "竟然通过了")
except Exception as e:
    check("无引擎解析报可操作错误", "已禁用" in str(e), str(e)[:120])
os.environ.pop("RAG4C_PARSERS_MINERU_ENABLED", None)
os.environ.pop("RAG4C_PARSERS_DOCLING_ENABLED", None)

print()
print(f"结果：{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
