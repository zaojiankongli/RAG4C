"""RAG4C 配置系统。

基于 pydantic-settings 的分层配置，环境变量前缀为 ``RAG4C_``，
嵌套段通过下划线拼合：``RAG4C_<SECTION>_<KEY>``。
例如 ``RAG4C_MILVUS_URI``、``RAG4C_LLM_GENERATION_MODEL``。

读取顺序（优先级从高到低）：
1. 构造函数显式传入的字段
2. 进程环境变量（RAG4C_*）
3. ``.env`` 文件（RAG4C_* 行），可通过 ``RAG4C_ENV_FILE`` 指定路径
4. 代码中的默认值

所有配置均可在不联网、不安装模型的情况下导入。

段模型依赖图（C2b 结构化，便于按段阅读与维护）：

    自包含段（无内部交叉引用，可独立阅读）：
        MilvusSettings / EmbeddingSettings / RerankerSettings / LlmProviderSettings /
        PipelineSettings / GraphSettings / MineruSettings / RetrySettings /
        ObservabilitySettings / CatalogSettings / SourcesSettings / CircuitSettings /
        RedisSettings / VerifySettings / TenantSettings / KnowledgeSecuritySettings /
        RunHistorySettings

    依赖段（引用上面的自包含段）：
        LlmSlotSettings <- LlmSlotsSettings（9+ 业务槽位，见 _RETRIEVAL_SLOTS / resolve_slot）
        ParserEngineSettings <- DoclingEngineSettings <- ParsersSettings

    根模型（聚合全部段）：
        Settings（env_prefix=RAG4C_，自定义 Rag4cEnvSource 解析 RAG4C_<SECTION>_<KEY>）

工具函数：resolve_env_file / _load_env_file / _iter_field_paths（热更新遍历用）/
    Rag4cEnvSource / Rag4cRegistryFileSecretSource / get_settings / resolve_tenant
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Iterator, Literal, get_origin
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SecretsSettingsSource,
)

# ---------------------------------------------------------------------------
# .env 加载（放在 os.environ 中，使自定义源能统一读到）
# ---------------------------------------------------------------------------

def resolve_env_file() -> Path | None:
    """返回本进程**实际**会加载的 .env 路径（都不存在时 None）。

    查找顺序：``RAG4C_ENV_FILE`` 显式路径 -> 当前工作目录 .env -> 项目根 .env
    -> ``config/.env``。

    单独抽出来是因为写入方必须和读取方看同一个文件。``/api/config/update``
    从前硬编码写项目根 ``.env``，而这里优先读 ``cwd/.env``：只要服务不是从
    项目根启动，配置页就在写一个**没人读**的文件，而且 ``RAG4C_ENV_FILE``
    被完全忽略。保存成功、重启无效、文件里确实有那一行——最难查的那种。
    """
    explicit = os.environ.get("RAG4C_ENV_FILE")
    if explicit:
        return Path(explicit)
    project_root = Path(__file__).resolve().parent.parent
    for candidate in (
        Path.cwd() / ".env",
        project_root / ".env",
        project_root / "config" / ".env",
    ):
        if candidate.is_file():
            return candidate
    return None


def _load_env_file() -> None:
    """将 .env 载入 os.environ（不覆盖已存在的环境变量）。

    ``override=False`` 在启动期是对的：真实环境变量（容器 / CI 注入）应当
    压过 .env 文件。热更新走的是另一条路径，见 ``server.app.config_update``。
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    path = resolve_env_file()
    if path is not None:
        load_dotenv(path, override=False)


_load_env_file()


# ---------------------------------------------------------------------------
# 自定义环境变量源：把 RAG4C_<SECTION>_<KEY> 映射到嵌套模型字段
# ---------------------------------------------------------------------------

def _is_settings_model(annotation: Any) -> bool:
    try:
        return isinstance(annotation, type) and issubclass(annotation, BaseModel)
    except TypeError:
        return False


def _iter_field_paths(
    model_cls: type[BaseModel],
    prefix: tuple[str, ...] = (),
    *,
    include_hidden: bool = False,
) -> Iterator[tuple[str, ...]]:
    """遍历配置叶字段；默认跳过仅允许 env/file-secret 注入的隐藏字段。"""
    for name, field in model_cls.model_fields.items():
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        if extra.get("config_api_hidden") and not include_hidden:
            continue
        path = prefix + (name,)
        ann = field.annotation
        if get_origin(ann) is dict:
            continue  # 动态配置字典（如 parsers.plugins）不参与 RAG4C_* env 映射
        if _is_settings_model(ann):
            yield from _iter_field_paths(ann, path, include_hidden=include_hidden)
        else:
            yield path


class Rag4cEnvSource(PydanticBaseSettingsSource):
    """读取 ``RAG4C_<SECTION>_<KEY>`` 形式的环境变量。

    路径由模型结构反向推导，天然支持任意嵌套层级，
    因此 ``RAG4C_LLM_GENERATION_MODEL`` 与 ``RAG4C_PIPELINE_RETRIEVAL_SCORE_THRESHOLD``
    都能正确落到对应字段。
    """

    ENV_PREFIX = "RAG4C_"
    PROVIDER_PREFIX = "RAG4C_LLM_PROVIDERS_"

    def get_field_value(self, field, field_name):
        # 自定义源无需走标准字段匹配路径
        return None, None, False

    def _collect_providers(self) -> dict[str, dict[str, str]]:
        """扫描 ``RAG4C_LLM_PROVIDERS_<名字>_<字段>`` 形式的环境变量。

        提供商是**动态命名**的 dict，``_iter_field_paths`` 会跳过 dict 字段
        （它按模型结构推导路径，推不出用户自定义的名字），所以这里单独扫。

        切分规则：从右往左按已知字段名后缀匹配。提供商名字自身可以含下划线，
        ``MY_PROVIDER_BASE_URL`` 必须切成 ``my_provider`` + ``base_url``，
        按第一个下划线切会得到 ``my`` + ``provider_base_url``，是错的。

        内置提供商作为底座合并：env 里只写了 API_KEY 的话，BASE_URL 仍取
        内置默认值——否则用户想给内置的 siliconflow 补个 key，还得把地址
        再抄一遍。
        """
        found: dict[str, dict[str, str]] = {}
        for env_name, value in os.environ.items():
            if not env_name.startswith(self.PROVIDER_PREFIX):
                continue
            remainder = env_name[len(self.PROVIDER_PREFIX):].lower()
            for field in _PROVIDER_FIELD_NAMES:
                suffix = "_" + field
                if remainder.endswith(suffix):
                    name = remainder[: -len(suffix)]
                    if name:
                        found.setdefault(name, {})[field] = value
                    break

        if not found:
            return {}

        # 与内置提供商合并（env 优先），只返回真正被触及的名字之外，
        # 还要带上全部内置项——否则 pydantic 会用本次返回值整体替换默认
        # dict，导致没在 env 里出现的内置提供商凭空消失。
        merged: dict[str, dict[str, str]] = {
            name: dict(vals) for name, vals in _BUILTIN_PROVIDERS.items()
        }
        for name, vals in found.items():
            merged.setdefault(name, {}).update(vals)
        return merged

    def __call__(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for path in _iter_field_paths(self.settings_cls, include_hidden=True):
            env_name = self.ENV_PREFIX + "_".join(p.upper() for p in path)
            if env_name in os.environ:
                node = data
                for part in path[:-1]:
                    node = node.setdefault(part, {})
                node[path[-1]] = os.environ[env_name]

        providers = self._collect_providers()
        if providers:
            data.setdefault("llm", {})["providers"] = providers
        return data


# ---------------------------------------------------------------------------
# 配置段
# ---------------------------------------------------------------------------

class Rag4cRegistryFileSecretSource(PydanticBaseSettingsSource):
    """Load the two hidden registry secrets using the project's env-style names."""

    _SECRET_FIELDS = {
        "ops_bearer_token": "RAG4C_RUN_HISTORY_OPS_BEARER_TOKEN",
        "fingerprint_secret": "RAG4C_RUN_HISTORY_FINGERPRINT_SECRET",
    }

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> None:
        super().__init__(settings_cls)
        self._secrets_dir = getattr(file_secret_settings, "secrets_dir", None)

    def get_field_value(self, field, field_name):
        return None, None, False

    def __call__(self) -> dict[str, Any]:
        if self._secrets_dir is None:
            return {}
        directories = (
            [self._secrets_dir]
            if isinstance(self._secrets_dir, (str, os.PathLike))
            else list(self._secrets_dir)
        )
        values: dict[str, str] = {}
        for field_name, secret_name in self._SECRET_FIELDS.items():
            for directory in reversed(directories):
                path = Path(directory).expanduser()
                if not path.is_dir():
                    continue
                candidate = SecretsSettingsSource.find_case_path(
                    path, secret_name, case_sensitive=False
                )
                if candidate is not None and candidate.is_file():
                    values[field_name] = candidate.read_text(encoding="utf-8").strip()
                    break
        return {"run_history": values} if values else {}


class MilvusSettings(BaseModel):
    """Milvus 向量库配置。

    uri 决定运行模式：
    - 本地文件路径（如 ``./rag4c.db``）-> Milvus Lite（零部署）
    - ``http(s)://host:port``      -> Milvus server / Zilliz Cloud

    BM25 稀疏检索由 Milvus 内置 Function 处理（应用层不生成 sparse 向量），
    故本类只含 BM25 的**索引与分词**配置，不含任何稀疏向量本身的配置。
    """
    model_config = ConfigDict(extra="ignore")

    uri: str = "./rag4c.db"
    token: str = ""
    db_name: str = ""
    collection_name: str = "rag4c_chunks"
    # 知识图谱（vector-graph-rag 方案）专用集合：实体 / 关系
    # （passage 角色由 rag4c_chunks 承担，图命中后按 chunk_id 回查，避免文本双写）
    entity_collection: str = "rag4c_entities"
    relation_collection: str = "rag4c_relations"
    dim: int = 1024  # BGE-M3 稠密向量维度
    text_max_length: int = 65535  # text 字段 VARCHAR 上限
    # 稠密向量索引
    index_type: str = "HNSW"
    metric_type: str = "COSINE"
    nlist: int = 1024
    m: int = 16          # HNSW M
    ef_construction: int = 200
    # 检索参数（nprobe 用于 IVF，ef 用于 HNSW；Milvus 对不适用参数会忽略）
    nprobe: int = 16
    # HNSW 检索期候选宽度（ef）；实际下发时对 candidate_limit 取上界，
    # 保证满足 Milvus 的 ef >= limit 约束。增大 -> 召回精度上升、延迟上升。
    ef: int = 64
    # 稀疏 BM25 索引参数（内置 Function 输出字段的索引）
    bm25_k1: float = 1.2
    bm25_b: float = 0.75
    # BM25 分词器（text 字段的 analyzer_params）。**默认值不是 Milvus 的默认值**：
    # Milvus 不传 analyzer_params 时用 standard 分析器，它按空白与标点切词——
    # 中文没有词间空白，整句会被切成极少数无用 token，导致中文查询 BM25 召回
    # 恒为 0，"混合检索" 静默退化成纯稠密检索（这个缺陷不会报错，只会让稀疏
    # 那一路白跑）。六条中英混合查询的实测：standard top1=3/6（三条中文全军
    # 覆没），chinese top1=6/6。
    #
    # 取值：
    #   "chinese"  -> {"type": "chinese"}（jieba 分词 + 中文停用词），中英混合
    #                 语料的推荐默认；英文侧结果比裸 jieba 更精准。
    #   "standard" -> 不下发 analyzer_params，保持 Milvus 默认（纯英文/西文
    #                 语料可用，中文语料不要选）。
    #   其他       -> 按 Milvus analyzer_params 的 JSON 原样下发，例如
    #                 '{"tokenizer": "jieba", "filter": ["lowercase"]}'，
    #                 供自定义分词器 / 多语言场景使用。
    #
    # 注意：analyzer_params 属于字段 schema，**建集合后无法原地修改**。
    # 改这一项需要重建集合（见 scripts/rebuild_collection.py）。
    bm25_analyzer: str = "chinese"
    # 稀疏倒排索引算法：BM25 度量下默认 DAAT_MAXSCORE；
    # 另可选 BLOCK_MAX_MAXSCORE / BLOCK_MAX_WAND / SINDI。
    # 注：原 bm25_drop_ratio_build 已从稀疏索引参数表中移除，故不再声明
    # （明确查证到被取代的是 SPARSE_WAND -> inverted_index_algo="DAAT_WAND"）。
    sparse_index_algo: str = "DAAT_MAXSCORE"
    # 混合检索：候选放大系数与 RRF 常数
    candidate_factor: int = 4
    rrf_k: int = 60
    timeout: float = 30.0
    # 注：一致性级别（consistency_level）此前声明但从未下发给任何 Milvus
    # 调用，属误导性配置，已移除。如需按级别读写，应在 search / query
    # 调用处显式传参，并针对目标 Milvus 版本验证后再放开配置。


class EmbeddingSettings(BaseModel):
    """嵌入配置。

    - provider="api"  ：OpenAI 兼容 /embeddings 端点（默认指向硅基流动 SiliconFlow，
      免费额度即可用 ``BAAI/bge-m3``；也可指向 Ollama / vLLM / 其他网关）。
      默认值即方案 1：SiliconFlow + BAAI/bge-m3（1024 维）。
    - provider="local"：本地 FlagEmbedding 的 BGE-M3（需要 pip install rag4c[embedding]）。
    """
    model_config = ConfigDict(extra="ignore")

    provider: str = "api"  # api | local
    model: str = "BAAI/bge-m3"
    # 注意：向量维度以 ``milvus.dim`` 为准（集合 schema 与检索都读那一个）。
    # 此处不再单独声明 dim，避免两个来源不一致时产生误导。
    device: str = "cpu"
    use_fp16: bool = False
    max_length: int = 8192  # BGE-M3 最大序列长度
    batch_size: int = 32
    normalize_embeddings: bool = False
    # API provider 专用（默认：硅基流动 SiliconFlow，OpenAI 兼容）
    api_base_url: str = "https://api.siliconflow.cn/v1"
    api_key: str = ""
    api_model: str = "BAAI/bge-m3"
    api_timeout: float = 120.0


class RerankerSettings(BaseModel):
    """重排序配置（BGE-reranker-v2-m3）。

    - provider="api"  ：OpenAI 兼容 /rerank 端点（默认硅基流动，免费额度），
      需 api_key；响应 results[{index, relevance_score}] 按 index 对齐回排；
    - provider="local"：本地 FlagEmbedding（pip install rag4c[embedding]，
      首次运行下载权重）。
    """
    model_config = ConfigDict(extra="ignore")

    provider: str = "local"  # local | api
    model: str = "BAAI/bge-reranker-v2-m3"
    device: str = "cpu"
    use_fp16: bool = False
    batch_size: int = 32
    normalize_score: bool = True
    # API 模式专用（provider=api 时生效）
    api_base_url: str = "https://api.siliconflow.cn/v1"
    api_key: str = ""
    api_model: str = "BAAI/bge-reranker-v2-m3"
    api_timeout: float = 30.0


class LlmProviderSettings(BaseModel):
    """一个 LLM 服务提供商的连接信息（地址 + 密钥）。

    引入这层的原因：11 个槽位如果各配一份 base_url / api_key，就是 22 个
    字段要填，换一家服务商得改 22 处。实际的心智是"我有几家服务商，
    每个槽位挑一家 + 挑个模型"，所以把连接信息抽出来命名复用。

    通过 ``RAG4C_LLM_PROVIDERS_<名字>_<字段>`` 配置，例如::

        RAG4C_LLM_PROVIDERS_DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
        RAG4C_LLM_PROVIDERS_DEEPSEEK_API_KEY=sk-xxx

    名字大小写不敏感（统一转小写），可自由新增。
    """
    model_config = ConfigDict(extra="ignore")

    base_url: str = ""
    api_key: str = ""


# 提供商的字段名集合。解析 RAG4C_LLM_PROVIDERS_<名字>_<字段> 时必须
# **从右往左**按这个集合匹配字段名——因为提供商名字本身可以含下划线
# （MY_PROVIDER_BASE_URL 该切成 my_provider + base_url，不是 my + provider_base_url）。
# 按已知字段名后缀匹配是唯一无歧义的切法。
_PROVIDER_FIELD_NAMES = ("base_url", "api_key")

# 开箱即用的内置提供商。用户可覆盖其中任一字段，也可另外新增。
#
# 只收录 **OpenAI 兼容** 的端点——这层存的是 base_url + api_key，能被内置
# 的前提就是"填个地址和密钥就能用现成的 LLMClient"。原生协议的厂商
# （Anthropic / Gemini / Bedrock 等）不属于这里，它们要的是一个新的
# provider 类去 LLM_PROVIDERS 注册，而不是多一条地址。
_BUILTIN_PROVIDERS: dict[str, dict[str, str]] = {
    "ollama": {"base_url": "http://localhost:11434/v1", "api_key": "ollama"},
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1", "api_key": ""},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key": ""},
    # 阿里云百炼 DashScope（通义千问 Qwen 系列）。
    #
    # 用的是国内站；DashScope 另有新加坡站 dashscope-intl.aliyuncs.com，
    # 两边的 API Key **不通用**——拿国内站的 key 打国际站会直接 401，而且
    # 报错只说鉴权失败、不提"你连错站了"，实测在这上面浪费过一轮排查。
    # 需要国际站就在 env 里覆盖 BASE_URL。
    #
    # 已实测该端点支持 LLMClient 用到的全部四项能力：非流式对话、
    # response_format={"type":"json_object"}（judge / router_llm /
    # metadata_filter 槽位依赖它）、流式、seed。所以不需要专门的
    # provider 类，走 openai_compatible 即可。
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",
    },
}


class LlmSlotSettings(BaseModel):
    """单个 LLM 槽位配置（OpenAI 兼容端点）。

    连接信息有两种给法，槽位自己的**优先**：

    1. ``provider_ref`` 指向一个已命名的提供商，继承它的 base_url / api_key；
    2. 槽位直接写 ``base_url`` / ``api_key``，覆盖上面的继承值。

    "有没有显式写过"用 pydantic 的 ``model_fields_set`` 判断，而不是判空——
    因为这两个字段有非空默认值（指向本机 Ollama），判空区分不出
    "用户就是要用 Ollama" 和 "用户没填、应该走 provider_ref"。

    继承在 :class:`LlmSlotsSettings` 构造完成时**就地完成**，所以直接读
    ``slot.base_url`` 拿到的已经是生效值，无需调用方做任何事。

    这里原本的契约是"取用时务必经 :func:`resolve_slot`"，被推翻了：全项目
    14 个 ``create_client`` 调用点没有一个遵守它，provider_ref 因此从未真正
    生效过——配了也只有 model 会变、base_url 还指着本机，拼出"拿 Ollama 要
    云端模型"的 404。一个需要每个调用点都记得的约定，忘 14 次就说明它不该
    是约定，而该是不变量。
    """
    model_config = ConfigDict(extra="ignore")

    provider: str = "openai_compatible"  # openai_compatible | openai
    # 引用 LlmSlotsSettings.providers 里的某个名字；留空表示不继承
    provider_ref: str = ""
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    model: str = "qwen2.5"
    temperature: float = 0.2
    timeout: float = 120.0
    max_tokens: int = 4096
    seed: int | None = None
    #: 本槽位的结果缓存存活秒数；``0`` 表示不缓存（默认）。
    #:
    #: 开在**槽位**上而不是调用点上，是因为这类"该不该缓"的判断有 14 个
    #: 调用点，而需要每个调用点都记得的约定在本文件里已经失败过一次
    #: （见上面 provider_ref 那段）。配置项则是不变量：加第 15 个调用点
    #: 也自动遵守，运维也能在不改代码的前提下把某个槽位的缓存关掉。
    #:
    #: 缓存键是整个请求的摘要（模型、端点、温度、max_tokens、seed、
    #: json_mode 和**渲染后的完整 prompt**），所以它**不需要语料代次**：
    #: prompt 里没有的东西不会影响结果，prompt 里有的东西一变键就变。
    #: 这也划出了哪些槽位适合开——见 :class:`LlmSlotsSettings` 的默认值。
    cache_ttl_s: float = 0.0


def _inherit_conn(
    slot: LlmSlotSettings, providers: dict[str, LlmProviderSettings]
) -> LlmSlotSettings:
    """把 ``provider_ref`` 指向的连接信息填进槽位，返回生效后的配置。

    优先级（高到低）：槽位自己显式写的 > provider_ref 指向的 > 槽位默认值。

    没有可继承的东西时返回**原对象**（不是副本），调用方可以用 ``is`` 判断
    有没有发生继承，省掉一次无谓的拷贝。

    找不到 provider_ref 指向的名字时静默退回槽位自身取值：配置里写错一个
    提供商名不该让整条问答链路崩掉，可见性由配置中心的校验负责。
    """
    ref = (slot.provider_ref or "").strip().lower()
    if not ref:
        return slot
    provider = providers.get(ref)
    if provider is None:
        return slot

    updates: dict[str, Any] = {}
    # 槽位没显式写过的字段才继承；写过的以槽位为准
    if "base_url" not in slot.model_fields_set and provider.base_url:
        updates["base_url"] = provider.base_url
    if "api_key" not in slot.model_fields_set and provider.api_key:
        updates["api_key"] = provider.api_key
    # model_copy(update=) 会把这些字段并进 model_fields_set，于是二次解析
    # 会把它们当成"已显式写过"而跳过——这正是我们要的幂等。
    return slot.model_copy(update=updates) if updates else slot



#: 检索期轻量槽位的默认缓存时长（秒）。
#:
#: 为什么**只有这一组**默认开：
#:
#: - 这六个槽位的 prompt 只由用户问句（或它的中间产物）和模板拼成，**从不
#:   包含检索到的文档**，所以缓存键天然自洽，不需要语料代次，入库也不会
#:   让它们失效。同一句话被反复问是问答系统的常态，命中的就是这部分。
#: - ``generation`` 不开：它的产物已经由 :mod:`core.query_cache` 按整条链路
#:   缓存（那一层才有代次、租户与 ACL），在这里再缓一遍既重复又危险——
#:   generation 的 prompt 里带着检索结果，而 ACL 决定了检索得到什么，
#:   prompt 相同不代表**看得见它的人**相同。
#: - ``judge`` 不开：prompt 里嵌着整块证据文本，值大、且只在答案缓存本来
#:   就会命中的时候才可能重复，属于花内存买不到命中。
#: - 入库三件套（triplet / contextual / classifier）不开：它们按 chunk 调用，
#:   一篇文档就是上千次，写进一个**与其它应用共用**的 Redis 会撑爆别人的
#:   服务。重建索引时想省钱可以单独调高，前提是有独占实例。
#:
#: 取 30 分钟而不是更长：这些值本身是纯函数、永不"过期"，TTL 在这里只用来
#: 回收冷门问句，以及给"换了模型但没换名字"留一个自愈期限。等 Redis 配上
#: maxmemory + volatile-lru 之后可以放长。
_PREPROC_CACHE_TTL_S = 1800.0

#: 检索期轻量槽位的名字。定义在类之前，因为下面的模型校验器要用它；
#: :data:`LLM_SLOT_GROUPS` 也复用这一份，免得两处清单迟早对不上。
_RETRIEVAL_SLOTS: tuple[str, ...] = (
    "rewrite", "router_llm", "hyde", "subqueries", "stepback", "metadata_filter",
)


def _default_cache_ttl(name: str, slot: LlmSlotSettings) -> LlmSlotSettings:
    """给检索期轻量槽位补上默认的结果缓存时长。

    为什么不能只写成字段默认值（``rewrite: LlmSlotSettings =
    LlmSlotSettings(cache_ttl_s=...)``）：那是**整个槽位对象**的默认值。只要
    配置里出现了 ``llm.rewrite``（哪怕只写了一个 model），pydantic 就会拿那份
    配置重新构造一个 ``LlmSlotSettings``，``cache_ttl_s`` 随之落回类级默认的
    ``0.0``——缓存就这么静默关掉了。实测就是如此：本项目 ``.env`` 里六个检索期
    槽位都指到了 DashScope，于是六个槽位的 ``cache_ttl_s`` 全是 0，缓存代码
    一行都不会执行。这与之前"SSE 缓存接好了但命中率恒为 0"是同一类故障：
    功能齐备、接线断开、没有任何报错。

    放在模型校验器里就没有这个问题——它在**每次**构造完成后都跑一遍，无论
    配置是从默认值、``.env``、还是配置中心的热更新来的。

    用 ``model_fields_set`` 判断有没有显式写过，而不是判 0：判 0 会让"我就是
    要把这个槽位的缓存关掉"变得无法表达。
    """
    if name not in _RETRIEVAL_SLOTS or "cache_ttl_s" in slot.model_fields_set:
        return slot
    # model_copy(update=) 会把字段并进 model_fields_set，于是二次解析会当成
    # "已显式写过"而跳过——与 _inherit_conn 一样的幂等处理。
    return slot.model_copy(update={"cache_ttl_s": _PREPROC_CACHE_TTL_S})


class LlmSlotsSettings(BaseModel):
    """业务 LLM 槽位（共 11 个，按调用阶段分组）。

    检索期：``rewrite`` 查询改写 / ``router_llm`` 意图路由兜底 /
    ``hyde`` 假设文档 / ``subqueries`` 子查询拆解 / ``stepback`` 后退提问 /
    ``metadata_filter`` 自动过滤表达式。
    生成期：``generation`` 答案生成 / ``judge`` 引用蕴含裁判。
    入库期：``triplet`` 三元组抽取 / ``contextual`` 片段上下文 /
    ``classifier`` 自动标签。

    各槽位独立配置 base_url / model / temperature，可指向不同服务。

    按调用特征分三组（前端配置界面也按这个分组呈现）：

    - **检索期轻量**（rewrite / router_llm / hyde / subqueries / stepback /
      metadata_filter）：在用户等待的关键路径上，输入输出都短、要结构化
      输出，看重响应速度与格式稳定性。
    - **生成与判定**（generation / judge）：generation 决定用户看到的答案
      质量；judge 判错会放过幻觉或误伤正确答案，而这两类错误用户都察觉
      不到。这组最不该省。
    - **入库批量**（triplet / contextual / classifier）：离线执行，不占用户
      等待，但调用量随文档片段数线性放大。注意 triplet 抽取质量差会污染
      知识图谱，属于"错了也不容易发现"的一类。
    """
    model_config = ConfigDict(extra="ignore")

    # 命名提供商注册表：槽位通过 provider_ref 引用，避免每个槽位重复填
    # 地址和密钥。内置 ollama / siliconflow / deepseek / dashscope，可覆盖可新增。
    providers: dict[str, LlmProviderSettings] = Field(
        default_factory=lambda: {
            name: LlmProviderSettings(**vals)
            for name, vals in _BUILTIN_PROVIDERS.items()
        }
    )

    # cache_ttl_s 不写在这些默认值里，由 _default_cache_ttl 在模型校验器里补：
    # 字段默认值只在"配置完全没提这个槽位"时生效，而一旦 .env 里出现
    # llm.rewrite（哪怕只写 model），整个对象会被重建、默认值随之丢失。
    rewrite: LlmSlotSettings = LlmSlotSettings(temperature=0.2)
    router_llm: LlmSlotSettings = LlmSlotSettings(temperature=0.1)
    generation: LlmSlotSettings = LlmSlotSettings(temperature=0.7)
    judge: LlmSlotSettings = LlmSlotSettings(temperature=0.0)
    # 知识图谱三元组抽取（入库阶段，需要确定性输出 -> 温度 0）
    triplet: LlmSlotSettings = LlmSlotSettings(temperature=0.0)
    # 查询增强（HyDE 假设文档 / 子查询拆解 / Stepback 后退提问，均需确定性输出）
    hyde: LlmSlotSettings = LlmSlotSettings(temperature=0.0)
    subqueries: LlmSlotSettings = LlmSlotSettings(temperature=0.0)
    stepback: LlmSlotSettings = LlmSlotSettings(temperature=0.0)
    # Contextual Retrieval（入库阶段为 chunk 生成文档级上下文，温度 0）
    contextual: LlmSlotSettings = LlmSlotSettings(temperature=0.0)
    # 元数据自动分类打标签（入库阶段，按 dataset 分类体系输出标签 JSON）
    classifier: LlmSlotSettings = LlmSlotSettings(temperature=0.0)
    # 自动元数据过滤（检索阶段，按 schema 生成 Milvus 过滤表达式）
    metadata_filter: LlmSlotSettings = LlmSlotSettings(temperature=0.0)

    @model_validator(mode="after")
    def _apply_provider_refs(self) -> "LlmSlotsSettings":
        """构造完成即把各槽位的 provider_ref 解析掉，让继承成为不变量。

        放在这里而不是让取用方调 :func:`resolve_slot`，是因为后者试过了：
        14 个 ``create_client`` 调用点无一遵守，provider_ref 从未生效。
        在这里做，所有取用方（含 ``/api/config`` 配置中心的展示、
        ``scripts/check_services.py`` 的探活）读到的都是同一个生效值，
        不存在"某处读到的和实际请求的不一致"这种最难查的偏差。

        按 ``isinstance`` 遍历而不是照着 ``LLM_SLOT_NAMES`` 写死：将来加第
        12 个槽位时，漏改这里会让新槽位静默失去继承——而这正是本次踩的坑。
        """
        for name, value in list(self.__dict__.items()):
            if not isinstance(value, LlmSlotSettings):
                continue  # providers 是 dict，跳过
            resolved = _inherit_conn(value, self.providers)
            resolved = _default_cache_ttl(name, resolved)
            if resolved is not value:
                # 绕过 __setattr__ 避免触发赋值校验递归；这里写回的对象
                # 已经是同类型的合法实例。
                object.__setattr__(self, name, resolved)
        return self


# 槽位按调用特征的分组。后端做批量操作、前端做界面分组都读这里，
# 避免两边各维护一份迟早对不上的清单。
LLM_SLOT_GROUPS: dict[str, tuple[str, ...]] = {
    "retrieval": _RETRIEVAL_SLOTS,
    "answer": ("generation", "judge"),
    "ingest": ("triplet", "contextual", "classifier"),
}

LLM_SLOT_GROUP_LABELS: dict[str, tuple[str, str]] = {
    "retrieval": (
        "检索期轻量",
        "在用户等待的关键路径上，输入输出都短、要求结构化输出；看重响应速度与格式稳定性",
    ),
    "answer": (
        "生成与判定",
        "决定答案质量与引用校验的严格程度；判错时用户往往察觉不到，最不该省",
    ),
    "ingest": (
        "入库批量",
        "离线执行不占用户等待，但调用量随文档片段数线性放大；抽取质量差会污染知识图谱",
    ),
}

# 所有槽位名（保持分组顺序，便于遍历与展示）
LLM_SLOT_NAMES: tuple[str, ...] = tuple(
    name for group in LLM_SLOT_GROUPS.values() for name in group
)


def resolve_slot(slots: LlmSlotsSettings, slot_name: str) -> LlmSlotSettings:
    """按名字取出某槽位的**有效**配置副本。

    继承已在 :class:`LlmSlotsSettings` 构造时完成，所以这个函数如今主要是
    「按名字取槽位 + 校验名字合法」的便利入口，返回副本以免调用方改到全局
    配置。仍然再跑一次 :func:`_inherit_conn` 是为了幂等兜底——手工拼装出
    未经校验器的 ``LlmSlotsSettings`` 时（测试里常见）它仍然给出正确结果。

    优先级（高到低）：槽位显式写的 > provider_ref 指向的 > 槽位默认值。
    """
    slot = getattr(slots, slot_name, None)
    if not isinstance(slot, LlmSlotSettings):
        raise ValueError(
            f"未知的 LLM 槽位 {slot_name!r}；可用：{', '.join(LLM_SLOT_NAMES)}"
        )
    return _inherit_conn(slot, slots.providers).model_copy()



class PipelineSettings(BaseModel):
    """检索管线开关与阈值。

    检索优化均为**开关式设计**（本轮用户决策），各开关默认开启：
    - ``hybrid_search_on``：混合检索（稠密 + BM25）开关
    - ``rerank_on``       ：BGE 重排开关
    - ``graph_retrieval_on``：知识图谱检索开关（由路由决定是否启用，
      关闭时即使路由命中 graph 也降级为 hybrid）
    - ``acl_filter_on``   ：ACL 过滤开关

    source_diversity 控制结果来源多样性：
    - "off"       ：纯混合检索，不分组
    - "group_only"：按 group_by_field 分组，每组取 group_size 条
    - "group_mmr" ：分组 + MMR 惩罚（mmr_lambda 控制多样性-相关度权衡）
    """
    model_config = ConfigDict(extra="ignore")

    # 检索优化开关（开关式设计）
    hybrid_search_on: bool = True
    rerank_on: bool = True
    # 图谱辅助检索。**依赖入库期的 graph_index_on**：没建过图时实体 / 关系
    # 集合不存在，图分支只会做一次无效的 Milvus 往返后降级为 hybrid。
    # 因此默认关闭，与 graph_index_on 的默认值保持一致；两者应一起开启。
    graph_retrieval_on: bool = False
    acl_filter_on: bool = True

    source_diversity: str = "off"  # off | group_only | group_mmr
    group_size: int = 3
    group_by_field: str = "doc_id"
    mmr_lambda: float = 0.7
    complexity_gate_on: bool = True
    # 双重阈值弃权（abstention）门槛
    retrieval_score_threshold: float = 0.3
    entailment_score_threshold: float = 0.6
    # rerank 未生效时改用「查询向量 vs chunk 向量」的真实余弦把关。
    #
    # **这是另一把尺子，绝不能跟上面的 0.3 共用。** 0.3 是重排器归一化后的
    # 相关性分；余弦是另一个量级。实测（8369 行的官方文档库，8 个库里答得上
    # 的查询 + 8 个明显答不上的）：
    #
    #     答得上 top1 余弦：min 0.576 / 中位 0.700 / max 0.800
    #     答不上 top1 余弦：min 0.379 / 中位 0.451 / max 0.492
    #
    # 两组之间有 (0.492, 0.576) 的干净间隔。若沿用 0.3，**八个离题查询全部
    # 会过闸**——闸门等于不存在。取 0.52：落在间隔内且偏下，因为误判"知识库
    # 无相关内容"是用户直接看得见、且无从挽回的，而放过去还有蕴含闸接着把关。
    #
    # 注意这个数与**语料和嵌入模型绑定**。换嵌入模型或换一批语料后必须重测，
    # 不能照抄——重测方法见 docs/模块实现说明.md 的对应小节。
    dense_cosine_threshold: float = 0.52
    top_k: int = 8

    # ---- 可插拔检索增强开关（官方 how_to_enhance_your_rag 系列）----
    # 查询端增强（需 LLM，默认关闭以保持零额外成本；开启后由对应槽位生成）：
    # - hyde_on        ：HyDE 假设文档嵌入。检索前用 LLM 生成"假设答案"，
    #                     用其向量检索，弥补查询-文档词汇鸿沟。
    # - subqueries_on  ：子查询拆解。把复杂查询拆成多个子查询分别检索后合并，
    #                     提升多主题查询召回。
    # - stepback_on    ：Stepback 后退式提问。生成一个更抽象的后退问题
    #                     补充检索，提升需要背景知识的查询召回。
    # 检索端增强（不依赖 LLM，可按需开启）：
    # - sentence_window_on：句子窗口回取。命中子块时按 parent_chunk_id
    #                       回取父块（small-to-big），提升上下文完整性。
    hyde_on: bool = False
    subqueries_on: bool = False
    stepback_on: bool = False
    sentence_window_on: bool = False
    # 子查询 / 后退问题各自检索时向 Milvus 请求的候选数（合并后仍按 top_k 裁剪）
    enhance_candidate_k: int = 8

    # ---- LangGraph 图编排开关（任务书"图编排 + 兜底"）----
    # graph_engine_on=True 时 answer_query 优先走 LangGraph StateGraph；
    # langgraph 未安装 / 图构建失败 / 图执行抛异常时自动回退到顺序编排
    # （answer_query 行为与 trace span 完全一致，仅内部编排方式不同）。
    graph_engine_on: bool = True

    # ---- 切分路由（indexing/chunking_router.py）----
    # 按文档复杂度/类型选择切分模式：
    #   auto         默认：csv/excel -> qa；短文本且无版面 -> recursive；否则 parent_child
    #   recursive    递归固定切分（简单文档，扁平 chunk 无父块）
    #   parent_child 父子切分（难文档，结构感知 + 父块/子块 small-to-big）
    #   qa           CSV/表格 -> 每行 QA 对
    chunking_mode: str = "auto"
    # auto 模式的简单文档判定阈值（全文字符数，且无版面组件时走 recursive）
    simple_doc_max_chars: int = 4000

    # ---- 入库端增强（indexing/，均在 server.documents 装配时按开关注入）----
    # - clean_on      ：入库清洗关。剥离页码 / 版权行 / 裸 URL / 控制字符等
    #                    噪声后再切分。不调用 LLM，开销可忽略，默认开启。
    # - contextual_on ：Contextual Retrieval（索引端增强）。入库时用 LLM 为
    #                    每个 chunk 生成文档级定位上下文，拼在片段正文前一起
    #                    嵌入，缓解片段脱离上下文导致的召回漂移。
    #                    **每批 chunk 一次 LLM 调用，入库成本显著上升**，默认关闭。
    # - graph_index_on：入库期构建知识图谱（三元组抽取 + 实体/关系向量化）。
    #                    **每个 chunk 一次 LLM 调用，入库成本很高**，默认关闭。
    #                    注意：检索侧 graph_retrieval_on 依赖本开关——没建过图时
    #                    图谱分支查不到实体/关系，会降级为纯 hybrid 结果。
    clean_on: bool = True
    contextual_on: bool = False
    graph_index_on: bool = False
    # 入库端 LLM 并发度。这两项直接决定对模型端点的瞬时压力：
    # 实际并发 = 该值 × RAG4C_INGEST_MAX_CONCURRENT（同时入库的文档数）。
    # 端点限流或本地模型吃不消时调小；默认值针对本地 Ollama 类部署选取。
    contextual_concurrency: int = 4
    graph_extract_concurrency: int = 8

    # ---- 检索端并发（retrieval/pipeline.py）----
    # 子查询扇出的线程池宽度。这个池是**进程级单例**（管线本身是单例），
    # 由所有并发查询共用：HTTP 侧最多放行 32 路并发，每路最多拆
    # ``max_sub_queries`` 个子查询，于是最坏情况是几十个检索任务排在这几个
    # worker 后面——池宽不够时，扇出不是"并发检索"而是"排队检索"，而排队
    # 的时间全部算在用户等待里。调大它压的是 Milvus，调小它压的是延迟。
    subquery_fanout_workers: int = 4
    # 单次查询等待全部子查询检索返回的上限（秒）；<=0 表示不设上限。
    # 这不是调优项，是保险丝：``future.result()`` 不带超时的话，一路
    # hybrid_search 卡住就会把整条 HTTP 请求永久挂住，进而吃掉一个准入名额
    # ——几路这样的请求就能让服务对外表现为整体不可用。超时不会取消已经
    # 发出的检索（线程仍被占着），但会让本次查询带着主检索结果照常返回。
    # 默认值取得足够宽松，正常情况下永远不会触发。
    subquery_fanout_timeout_s: float = 30.0
    # 送进上下文生成提示词的文档摘要上限（字符）。<=0 表示发送全文——
    # 长文档慎用：每批都会带上它，极易超出模型上下文窗口。
    contextual_document_chars: int = 4000


class GraphSettings(BaseModel):
    """知识图谱（vector-graph-rag 方案）检索配置。

    参照 zilliztech/vector-graph-rag 官方参数：
    - 实体 / 关系向量检索的候选数与相似度阈值
    - 子图扩展跳数 expansion_degree（1 = 一跳，收集直接相连关系）
    - 图检索最终返回的 passage（chunk）条数

    下面几个阈值不是拍出来的，是用本项目的嵌入端点（bge-m3）实测出来的，
    正负例分布见各字段注释。
    """
    model_config = ConfigDict(extra="ignore")

    entity_top_k: int = 20
    relation_top_k: int = 20
    # 实体命中相似度阈值。卡的是 cosine(embed_query(整句查询), 实体名)——
    # 一句话对一个名词短语，尺度天然低于句对句。实测（15 个实体 / 5 条查询）：
    # 该命中的实体落在 0.34~0.91，不该命中的落在 0.10~0.56。
    #   阈值 0.90：保留正例 1/10   ← 从前的默认值
    #   阈值 0.50：保留正例 6/10，漏进负例 2/65
    # 0.9 意味着**除非查询里逐字出现实体名，否则一个实体都命不中**，而实体
    # 命不中 = 子图扩展无从谈起 = 整条图分支形同虚设。
    # 这里不追求"用阈值分开相关与不相关实体"——实测两者区间重叠得很厉害，
    # 分不开。阈值只负责**找到锚点实体**（0.70~0.91，远在 0.5 之上），锚点
    # 的邻居靠图上的边去够，不靠相似度；这正是子图扩展存在的理由。
    entity_similarity_threshold: float = 0.5
    # 关系命中相似度阈值。关系文本是"头 谓词 尾"拼成的一句话，与查询是句对
    # 句，尺度明显高于实体侧——所以这里**不该**和实体侧取同一个数。实测
    # （14 条关系 / 4 条查询）：正例 0.62~0.80，负例 0.12~0.63。
    #   阈值 -1.0（从前的默认值）：正例 9/9，负例 47/47 全放进来
    #   阈值 0.35：正例 9/9，负例 8/47
    # -1.0 等于不过滤：relation_top_k 会无条件倒出 20 条，末位那些（实测低至
    # 0.12）照样进 LLM 重排候选、照样回取 passage，把噪音一路带进答案。
    # 取 0.35 而不是更高：0.45~0.58 那一段是**话题相邻**的关系（"年假审批人
    # 主管" 之于报销问题），扩展本来就可能要用到它们。
    relation_similarity_threshold: float = 0.35
    # 子图扩展跳数：从命中实体出发，沿 relation_ids 扩展几跳。
    # 1 跳只能拿到锚点实体**直接相连**的关系——那和"按关系向量检索"拿到的
    # 是同一批东西，多跳推理（"A 的上级的部门是什么"这类）需要的桥接实体在
    # 第 2 跳上。默认 1 等于把这个特性的意义抵消掉了。
    expansion_degree: int = 2
    # 扩展所得关系的先验分衰减系数：
    #   prior(第 h 跳的关系) = 锚点实体相似度 * decay ** (h + 1)
    # 扩展关系没有自己的向量检索分（它们是顺着边够到的，不是搜到的），得给
    # 一个分数才能和检索命中排在同一把尺子上。从前给的是 0.0，于是它们恒排
    # 最后——**扩展结果，也就是这个特性的全部理由，只有 LLM 重排把它们提上
    # 来才能存活**。衰减保证"离锚点越远越不可信"这件事被如实表达出来。
    # 0.0 = 退回从前的行为（全部扩展关系同分 0.0，稳定排序下仍排在检索命中
    # 之后）；1.0 = 不衰减，二跳外的关系会和一跳的平起平坐。
    expansion_prior_decay: float = 0.6
    # 图检索返回的最终 chunk 条数。和管线自己的 pipeline.top_k(=8) 对齐：
    # 再多也只能靠挤掉混合检索的命中才活得下来，而那该由重排决定，不该由
    # 这里一个写死的 3 决定。且这个截断发生在**租户 / ACL 过滤之前**，取 3
    # 时过滤掉两条就只剩一条了。
    final_top_k: int = 8
    # LLM 重排开关（对扩展后的关系做相关性重排，再决定回取哪些 chunk）
    use_llm_rerank: bool = True
    # 批量嵌入 / 查询的批大小
    batch_size: int = 32



class MineruSettings(BaseModel):
    """MinerU 文档解析配置。

    - provider="cli"  ：调用本机 mineru CLI（npm 包 ``mineru-open-api``，
      ``flash-extract`` 免 Token / 10MB / 20 页；``extract`` 需 Token 多格式）。
    - provider="http" ：直接调用 MinerU HTTP API（v4 精准解析需 Token，
      ≤200MB / 200 页 / 批量 ≤50；v1 Agent 轻量免 Token ≤10MB / 20 页）。
    """
    model_config = ConfigDict(extra="ignore")

    provider: str = "cli"  # cli | http
    executable: str = "mineru-open-api"  # CLI 可执行文件名
    # HTTP 模式（provider=http 时生效）
    api_base: str = "https://mineru.net"
    api_key: str = ""          # 空则走 v1 Agent 轻量 API（免 Token）
    model_version: str = "vlm"  # vlm | pipeline | MinerU-HTML（v4 精准 API）
    language: str = "ch"        # 文档语言（ch / en / japan / korean ...）
    enable_ocr: bool = True     # 扫描件 / 含文字图片启用 OCR
    enable_table: bool = True
    enable_formula: bool = True
    page_limit: int = 200       # 超出则拒绝解析（与 API 限额一致）
    timeout: float = 900.0      # 解析超时（秒），CLI 首跑可能下载模型


class RetrySettings(BaseModel):
    """通用重试配置（指数退避 + 随机抖动）。

    应用于易瞬态失败的调用（LLM / Milvus / 图节点等）：
    - ``max_attempts``：最大尝试次数（含首次）
    - ``base_delay``  ：首次失败后的退避基准（秒）
    - ``max_delay``   ：单次退避上限（秒）
    - ``jitter``      ：随机抖动比例（0~1），避免惊群
    - ``backoff_factor``：指数退避倍数（delay = min(base * factor^(attempt-1), max)）
    """
    model_config = ConfigDict(extra="ignore")

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    jitter: float = 0.1
    backoff_factor: float = 2.0


class ObservabilitySettings(BaseModel):
    """可观测性：日志、性能指标与 OpenTelemetry（预留 Langfuse / OTel 接入）。"""
    model_config = ConfigDict(extra="ignore")

    log_level: str = "INFO"  # DEBUG | INFO | WARNING | ERROR | CRITICAL
    otel_endpoint: str | None = None
    otel_service_name: str = "rag4c"
    tracing_enabled: bool = True
    # 进程内性能指标采集（计数 / 直方图 / P50/P95/P99，见 core.metrics）
    metrics_enabled: bool = True


class ParserEngineSettings(BaseModel):
    """解析引擎插件配置（统一结构：「1 配置」入口，所有引擎同构）。"""
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # 引擎内模式：mineru=free(flash-extract 免 token)/paid(精准 extract)；
    # docling=local(本地模型)/api(云服务)
    mode: str = "free"
    # 插件路由优先级（engine=auto 时数字越小越优先）
    priority: int = 100


class DoclingEngineSettings(ParserEngineSettings):
    """docling 引擎配置（继承统一三字段结构，仅默认 mode 不同）。

    独立子类的原因：环境变量局部覆盖（如仅设 RAG4C_PARSERS_DOCLING_ENABLED）
    时 pydantic 会按子类默认重建嵌套配置；若直接用 ParserEngineSettings，
    mode 会回落为类默认 "free"（对 docling 非法）。
    """
    model_config = ConfigDict(extra="ignore")

    mode: str = "local"  # local=本地模型免费 / api=云服务付费（预留）


class DoclingSettings(BaseModel):
    """Docling 引擎专属配置（mode=api 的端点与凭据；local 模式无需配置）。"""
    model_config = ConfigDict(extra="ignore")

    api_base: str = ""
    api_key: str = ""


class ParsersSettings(BaseModel):
    """DeepDoc 双引擎解析配置（Fast：pdf-inspector / Vision：MinerU）。"""
    model_config = ConfigDict(extra="ignore")

    # 路由开关：pdf-inspector 采样分类决定引擎（关闭则全部走 MinerU，行为同旧版）
    router_on: bool = True
    # Fast 引擎开关（pdf-inspector 可用性总闸）
    pdf_inspector_on: bool = True
    # 表格结构识别增强（TSR：层级表头 / 合并单元格转结构化句子，阶段 4）
    tsr_on: bool = False
    # 扫描件表格自动旋转（OCR 置信度择优 0/90/180/270 度，透传 MinerU）
    rotation_on: bool = True
    # ---- 插拔式 vision 引擎（统一配置：每引擎 enabled/mode/priority）----
    # engine=auto 按 priority 选第一个 enabled 且已安装的引擎；
    # 显式指定 mineru / docling 时强制使用该引擎（未启用报错）。
    engine: str = "auto"
    mineru: ParserEngineSettings = ParserEngineSettings(enabled=True, mode="free", priority=100)
    docling: DoclingEngineSettings = DoclingEngineSettings(enabled=False, priority=200)
    # 动态引擎配置（插拔式扩展）：第三方引擎在 import 时注册插件并向此字典
    # 注入同构 ParserEngineSettings 即可被 auto 发现，settings.py 零改动
    # （字典内容不支持 RAG4C_* 环境变量映射，仅固定字段可 env 覆盖）。
    plugins: dict[str, ParserEngineSettings] = {}


class CatalogSettings(BaseModel):
    """目录服务配置（SQLAlchemy：SQLite 默认 / MySQL 等远程库可选）。

    - db_path：SQLite 文件路径（db_url 为空时生效）；
    - db_url ：完整 SQLAlchemy URL（非空则忽略 db_path），如
      `mysql+pymysql://root:xxx@192.168.100.128:3307/rag4c?charset=utf8mb4`
      （敏感：含密码，配置中心脱敏显示；需 pip install pymysql）。
    """
    model_config = ConfigDict(extra="ignore")

    db_path: str = "data/rag4c.db"
    db_url: str = ""
    # legacy：兼容旧部署，启动时 create_all；verify：只验证 Alembic schema，不写库。
    schema_mode: Literal["legacy", "verify"] = "legacy"
    # off：旧路径；shadow：记录 ledger 但仍由旧路径写投影；active：由 operation worker 驱动。
    ingest_ledger_mode: Literal["off", "shadow", "active"] = "off"
    # off：不写 chunk_heads；shadow：双写但 Milvus 仍可为读路径；active：chunk_heads 为权威。
    chunk_authority_mode: Literal["off", "shadow", "active"] = "shadow"
    # 自动元数据过滤（检索时 LLM 生成表达式）与自动打标签（入库时 LLM 分类）
    auto_filter_on: bool = False
    auto_tag_on: bool = False


class SourcesSettings(BaseModel):
    """文档源接入层配置（:mod:`sources`）。

    **源本身不在这里声明，而是在一份独立的 JSON 清单里。** 原因有二：源的
    参数是结构化的（include 是数组、params 是嵌套对象），``.env`` 只能表达
    扁平字符串；而且 :class:`Rag4cEnvSource` 明确跳过 dict 字段，就算写了也
    映射不进来。本段只放「所有源共用的少数几个旋钮」，清单路径指向那份 JSON。
    """
    model_config = ConfigDict(extra="ignore")

    # 源清单路径（相对项目根或绝对路径）
    manifest_path: str = "config/sources.json"
    # 抓取缓存与增量状态的根目录。留在磁盘上是有意的：出问题时可以直接
    # 打开看抓到了什么，而不是只能从日志倒推。
    cache_dir: str = ".rag4c_cache/sources"
    # json：旧状态文件；dual：JSON + 数据库；database：数据库为唯一同步状态。
    state_mode: Literal["json", "dual", "database"] = "json"
    # 单次抓取的 HTTP 超时（秒）。默认给得比较宽松：官方文档仓库的
    # tarball 有几十 MB，跨境链路上几分钟是常态。
    http_timeout: float = 300.0
    # Source Control Plane 必须显式配置 allowlist；空列表表示 fail closed。
    local_allowed_roots: list[str] = Field(default_factory=list)
    local_allowed_extensions: list[str] = Field(default_factory=list)
    github_allowed_repositories: list[str] = Field(default_factory=list)
    github_allowed_organizations: list[str] = Field(default_factory=list)
    dispatch_poll_interval_seconds: float = Field(default=1.0, gt=0, le=300)
    execution_lease_seconds: float = Field(default=30.0, gt=0, le=3600)
    execution_heartbeat_seconds: float = Field(default=10.0, gt=0, le=1800)
    execution_workers: int = Field(default=2, ge=1, le=32)
    dispatch_batch_size: int = Field(default=50, ge=1, le=1000)
    reservation_lease_seconds: float = Field(default=5.0, gt=0, le=300)
    execution_retry_base_seconds: float = Field(default=1.0, gt=0, le=3600)
    execution_retry_max_seconds: float = Field(default=300.0, gt=0, le=86400)
    execution_retry_jitter_ratio: float = Field(default=0.2, ge=0, le=1)
    execution_max_attempts: int = Field(default=10, ge=1, le=1000)
    execution_shutdown_grace_seconds: float = Field(default=5.0, ge=0, le=300)
    verified_staging_ttl_seconds: float = Field(default=86400.0, ge=0, le=2592000)

    @model_validator(mode="after")
    def _validate_execution_lease(self) -> "SourcesSettings":
        if self.execution_heartbeat_seconds >= self.execution_lease_seconds:
            raise ValueError("execution heartbeat must be shorter than the lease")
        if self.execution_retry_base_seconds > self.execution_retry_max_seconds:
            raise ValueError("execution retry base must not exceed max delay")
        return self

    @field_validator("local_allowed_roots")
    @classmethod
    def _normalize_local_roots(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("local_allowed_roots must not contain empty paths")
        return list(dict.fromkeys(normalized))

    @field_validator("local_allowed_extensions")
    @classmethod
    def _normalize_local_extensions(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in values:
            value = str(raw).strip().casefold()
            if not value:
                raise ValueError("local_allowed_extensions must not contain empty values")
            if not value.startswith("."):
                value = f".{value}"
            suffix = value[1:]
            if not suffix.isalnum():
                raise ValueError("local_allowed_extensions entries must be simple suffixes")
            normalized.append(value)
        return list(dict.fromkeys(normalized))

    @field_validator("github_allowed_repositories")
    @classmethod
    def _normalize_github_repositories(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in values:
            value = str(raw).strip().casefold()
            parts = value.split("/")
            if (
                len(parts) != 2
                or any(not part for part in parts)
                or any(not all(char.isalnum() or char in "._-" for char in part) for part in parts)
            ):
                raise ValueError("github_allowed_repositories entries must be owner/repository")
            normalized.append(value)
        return list(dict.fromkeys(normalized))

    @field_validator("github_allowed_organizations")
    @classmethod
    def _normalize_github_organizations(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in values:
            value = str(raw).strip().casefold()
            if not value or not all(char.isalnum() or char in "._-" for char in value):
                raise ValueError("github_allowed_organizations entries must be organization names")
            normalized.append(value)
        return list(dict.fromkeys(normalized))


class CircuitSettings(BaseModel):
    """熔断器配置（core.circuit，依赖故障隔离）。

    - failure_threshold：连续失败多少次后打开
    - cooldown_s        ：打开状态的冷却时长（秒），期间快速失败
    - half_open_probe   ：半开状态允许的探测请求数
    """
    model_config = ConfigDict(extra="ignore")

    failure_threshold: int = 3
    cooldown_s: float = 30.0
    half_open_probe: int = 1


class RedisSettings(BaseModel):
    """Redis 配置（跨进程答案缓存 + Streams 排队）。

    Redis 在本系统里是**加速件，不是依赖件**：连不上时缓存退回进程内 LRU、
    排队退回进程内信号量，服务照常提供完整能力，只是失去跨副本共享。这条
    是硬约束——为了「缓存」而让整个问答服务跟着一个可选组件一起挂，是拿
    可用性换性能，方向反了。

    - url             ：连接串；留空表示不启用 Redis（纯内存态）
    - key_prefix      ：键名前缀。**必须带**：目标实例可能与其它应用共用
      （实测该实例 dbsize=19），没有前缀就是在别人的键空间里裸奔
    - socket_timeout_s：单次命令超时。取值要小——缓存查询卡 3 秒还不如直接
      算，缓存的意义就是快
    - cache_ttl_s     ：答案缓存存活时长
    - cache_on        ：是否启用 Redis 答案缓存（url 为空时此项无效）
    - queue_backend   ：memory | redis。默认 memory，不动现有快路径
    - stream_maxlen   ：Streams 裁剪长度上限（近似裁剪，防止无界增长）
    """
    model_config = ConfigDict(extra="ignore")

    url: str = ""
    key_prefix: str = "rag4c:"
    socket_timeout_s: float = 1.0
    socket_connect_timeout_s: float = 1.0
    cache_ttl_s: float = 900.0
    cache_on: bool = True
    #: 跨进程 single-flight 锁的持有时长。必须**大于**一次问答的最坏耗时，
    #: 否则锁提前过期，第二个进程会重复算一遍（退化成没有 single-flight）。
    lock_ttl_s: float = 300.0
    queue_backend: str = "memory"
    stream_maxlen: int = 10_000


class VerifySettings(BaseModel):
    """引用验证（三层防线）配置。

    三层防线依次为：L1 引用存在性 -> L2 文本哈希（是否 stale）->
    L3 蕴含判定（结论是否真被原文支撑）。L1/L2 是本地计算，开销可忽略；
    **L3 需要为每条声明调用一次裁判模型，是整个问答链路里最大的一笔
    隐性开销**，本段即用于控制它。

    - ``entailment_mode``：``llm`` 调用裁判模型逐条判定（最严格）；
      ``skip`` 跳过 L3，只保留 L1/L2（省下全部裁判调用，但失去
      「结论是否被证据支撑」这道防线，幻觉风险上升）；
      ``nli`` 为预留模式，当前未内置模型，选中会在验证时降级。
    - ``strict``：True 全量评审；False 时按 ``sample_ratio`` 抽样。
    - ``sample_ratio``：非严格模式下送入 L3 的引用比例（0~1，确定性
      均匀取样，结果可复现）。``strict=True`` 时本项无效。

    Note:
        弃权门读的是 L3 产出的蕴含分数。``entailment_mode="skip"`` 时
        没有蕴含分数产出，验证阶段的弃权判定将不再生效——只剩检索分数
        这一道闸。降本与安全性的取舍需要显式权衡。
    """
    model_config = ConfigDict(extra="ignore")

    entailment_mode: str = "llm"  # llm | skip | nli（预留）
    strict: bool = True
    sample_ratio: float = 1.0


class TenantSettings(BaseModel):
    """多租户隔离配置（单集合 + tenant_id 元数据过滤）。

    - ``enforced``：强制隔离开关。为 True 时，问答 / 入库 / 检索请求
      一律归属某个有效租户——显式传入的 ``tenant_id`` 或回退到
      ``default_tenant``（空串视为未指定），**跨租户数据不可见**；
      为 False 时 ``tenant_id=""`` 表示不过滤（开发 / 单租户模式）。
    - ``default_tenant``：未显式指定租户时的匿名回退租户
      （保证既有调用零改动即纳入隔离，存量数据默认归此租户）。
    """
    model_config = ConfigDict(extra="ignore")

    enforced: bool = True
    default_tenant: str = "default"


class KnowledgeSecuritySettings(BaseModel):
    """Dedicated signing policy for actor-bound KnowledgeOps credentials."""

    model_config = ConfigDict(extra="ignore")

    actor_signing_secret: SecretStr | None = Field(
        default=None, json_schema_extra={"config_api_hidden": True}
    )
    actor_max_ttl_s: int = Field(default=900, ge=60, le=3600)
    # Empty by default: OIDC start remains fail-closed until an operator configures
    # exact callback URIs. At most 32 entries keeps comparison and config surfaces bounded.
    oidc_redirect_uri_allowlist: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("oidc_redirect_uri_allowlist", mode="before")
    @classmethod
    def _parse_oidc_redirect_allowlist(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "oidc_redirect_uri_allowlist must be JSON or comma-separated"
                ) from exc
            if not isinstance(parsed, list):
                raise ValueError("oidc_redirect_uri_allowlist must be a list")
            return parsed
        return [item.strip() for item in raw.split(",")]

    @field_validator("oidc_redirect_uri_allowlist")
    @classmethod
    def _normalize_oidc_redirect_allowlist(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in values:
            value = str(raw).strip()
            if not value or len(value) > 1024:
                raise ValueError("OIDC redirect URI entries must be 1..1024 characters")
            parsed = urlsplit(value)
            scheme = parsed.scheme.casefold()
            if (
                not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError("OIDC redirect URI must not contain userinfo or fragments")
            try:
                port = parsed.port
            except ValueError as exc:
                raise ValueError("OIDC redirect URI port is invalid") from exc
            host = parsed.hostname.rstrip(".").casefold()
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError as exc:
                raise ValueError("OIDC redirect URI host is invalid") from exc
            loopback = host == "localhost"
            try:
                loopback = loopback or ipaddress.ip_address(host).is_loopback
            except ValueError:
                pass
            if scheme != "https" and not (scheme == "http" and loopback):
                raise ValueError("OIDC redirect URI must use HTTPS or loopback HTTP")
            default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
            rendered_host = f"[{host}]" if ":" in host else host
            netloc = rendered_host if port is None or default_port else f"{rendered_host}:{port}"
            canonical = urlunsplit((scheme, netloc, parsed.path, parsed.query, ""))
            if len(canonical) > 1024:
                raise ValueError("OIDC redirect URI entries must be 1..1024 characters")
            normalized.append(canonical)
        return list(dict.fromkeys(normalized))


class RunHistorySettings(BaseModel):
    """Bounded RunRegistry, persistence, and operator-access configuration."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    persistence_enabled: bool = True
    sqlite_path: str = "data/run-history.sqlite3"

    memory_max_active_runs: int = Field(default=128, ge=1, le=4096)
    memory_max_recent_runs: int = Field(default=512, ge=1, le=100000)
    memory_max_events_per_run: int = Field(default=1024, ge=2, le=100000)
    memory_max_events_total: int = Field(default=32768, ge=2, le=1000000)
    memory_terminal_ttl_s: int = Field(default=21600, ge=60, le=604800)

    max_event_json_bytes: int = Field(default=16384, ge=1024, le=1048576)
    max_topology_json_bytes: int = Field(default=131072, ge=4096, le=4194304)

    writer_queue_capacity: int = Field(default=8192, ge=1, le=1000000)
    writer_batch_size: int = Field(default=64, ge=1, le=10000)
    writer_flush_ms: int = Field(default=100, ge=1, le=60000)
    writer_shutdown_grace_ms: int = Field(default=2000, ge=0, le=60000)

    retention_days: int = Field(default=30, ge=1, le=3650)
    max_persisted_runs: int = Field(default=100000, ge=1, le=10000000)
    cleanup_interval_s: int = Field(default=600, ge=10, le=86400)
    cleanup_batch_size: int = Field(default=1000, ge=1, le=100000)

    heartbeat_interval_s: int = Field(default=5, ge=1, le=300)
    worker_stale_after_s: int = Field(default=30, ge=2, le=3600)
    stuck_after_s: int = Field(default=300, ge=1, le=86400)
    slow_threshold_ms: int = Field(default=30000, ge=1000, le=3600000)

    api_default_page_size: int = Field(default=50, ge=1, le=100)
    api_max_page_size: int = Field(default=100, ge=1, le=100)
    events_max_page_size: int = Field(default=500, ge=1, le=500)
    long_poll_max_ms: int = Field(default=25000, ge=0, le=25000)
    long_poll_max_clients: int = Field(default=64, ge=1, le=4096)
    cursor_ttl_s: int = Field(default=3600, ge=60, le=86400)

    ops_bearer_token: SecretStr | None = Field(
        default=None, json_schema_extra={"config_api_hidden": True}
    )
    fingerprint_secret: SecretStr | None = Field(
        default=None, json_schema_extra={"config_api_hidden": True}
    )

    @model_validator(mode="after")
    def validate_related_bounds(self) -> "RunHistorySettings":
        if self.api_default_page_size > self.api_max_page_size:
            raise ValueError("api_default_page_size exceeds api_max_page_size")
        if self.writer_batch_size > self.writer_queue_capacity:
            raise ValueError("writer_batch_size exceeds writer_queue_capacity")
        if self.worker_stale_after_s <= self.heartbeat_interval_s:
            raise ValueError("worker_stale_after_s must exceed heartbeat_interval_s")
        return self


class Settings(BaseSettings):
    """根配置：所有段在此聚合。"""

    model_config = SettingsConfigDict(
        env_prefix="RAG4C_",
        extra="ignore",
        validate_default=False,
    )

    milvus: MilvusSettings = MilvusSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    reranker: RerankerSettings = RerankerSettings()
    llm: LlmSlotsSettings = LlmSlotsSettings()
    pipeline: PipelineSettings = PipelineSettings()
    graph: GraphSettings = GraphSettings()
    verify: VerifySettings = VerifySettings()
    mineru: MineruSettings = MineruSettings()
    parsers: ParsersSettings = ParsersSettings()
    docling: DoclingSettings = DoclingSettings()
    retry: RetrySettings = RetrySettings()
    circuit: CircuitSettings = CircuitSettings()
    redis: RedisSettings = RedisSettings()
    catalog: CatalogSettings = CatalogSettings()
    sources: SourcesSettings = SourcesSettings()
    observability: ObservabilitySettings = ObservabilitySettings()
    tenant: TenantSettings = TenantSettings()
    knowledge_security: KnowledgeSecuritySettings = KnowledgeSecuritySettings()
    run_history: RunHistorySettings = RunHistorySettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # 用自定义 RAG4C_<SECTION>_<KEY> 源替换默认 env/dotenv 源
        return (
            init_settings,
            Rag4cEnvSource(settings_cls),
            Rag4cRegistryFileSecretSource(settings_cls, file_secret_settings),
            file_secret_settings,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级单例配置（首次调用后缓存）。"""
    return Settings()


def resolve_tenant(tenant_id: str | None, settings: Any = None) -> str:
    """把请求方传入的 tenant_id 解析为有效租户（全链路强制隔离的统一入口）。

    - ``enforced=True``：空 / None 回退 ``default_tenant``（存量调用零改动
      纳入隔离）；非空则原样返回。
    - ``enforced=False``：原样返回（可为 ``""`` 表示不过滤，开发 / 单租户
      模式）。
    - settings 为 None 或没有 ``tenant`` 配置段（如直接传入
      ``PipelineSettings`` 的桩场景）时按 enforced=True 处理，回退到
      ``default_tenant``（缺省 ``"default"``），保证存量调用零改动。

    Args:
        tenant_id: 请求方传入的租户标识（None / 空串 = 未指定）。
        settings: 配置对象（含 ``tenant`` 段）；None 时按强制模式处理。

    Returns:
        解析后的有效租户标识（enforced=False 且未指定时返回空串）。
    """
    tenant_cfg = getattr(settings, "tenant", None) if settings is not None else None
    if tenant_cfg is not None and not tenant_cfg.enforced:
        # 关闭开关：原样返回（空串 = 不过滤）
        return tenant_id or ""
    if tenant_id:
        return tenant_id
    default = (
        getattr(tenant_cfg, "default_tenant", "default")
        if tenant_cfg is not None
        else "default"
    )
    return default


__all__ = [
    "Settings",
    "get_settings",
    "Rag4cEnvSource",
    "MilvusSettings",
    "EmbeddingSettings",
    "RerankerSettings",
    "LlmSlotSettings",
    "LlmSlotsSettings",
    "PipelineSettings",
    "GraphSettings",
    "MineruSettings",
    "ParserEngineSettings",
    "DoclingEngineSettings",
    "RetrySettings",
    "ObservabilitySettings",
    "SourcesSettings",
    "TenantSettings",
    "KnowledgeSecuritySettings",
    "RunHistorySettings",
    "DoclingSettings",
    "ParsersSettings",
    "CircuitSettings",
    "RedisSettings",
    "CatalogSettings",
    "VerifySettings",
    "resolve_tenant",
]
