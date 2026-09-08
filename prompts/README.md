# RAG4C Prompt Templates

企业 RAG 系统的 LLM 槽位提示词模板。每个模板都是一个自包含的纯文本文件：开发者只需把占位变量（{curly_braces} 形式）替换为实际内容，即可整份粘贴到任意 LLM 对话中使用。

## 版本约定

- 文件名后缀标识模板版本：`<slot>_v<N>.txt`，N 从 1 开始递增。例如 `query_rewrite_v1.txt`、`query_rewrite_v2.txt`。
- 每次对模板做出行为性修改（规则、输出格式、JSON 结构、评分合约变更）都必须递增版本号，生成新的 `<slot>_v<N+1>.txt`，不得原地覆盖旧版本。
- 每个文件的顶部注释块必须写明：文件路径、槽位名、版本号、指令语言、功能说明。
- 旧的版本文件保留在目录中用于追溯；系统配置中通过文件名显式引用当前使用的版本。

## 文件到槽位的映射

| 文件名 | LLM 槽位 | 输出格式 | 占位变量 |
| --- | --- | --- | --- |
| query_rewrite_v1.txt | 查询改写 | 严格 JSON | {query} |
| router_llm_v1.txt | 意图路由（LLM 兜底） | 严格 JSON | {query}, {candidate_routes} |
| generation_v1.txt | 有据回答生成 | 纯文本 | {question}, {evidence}, {max_tokens_hint}, {language} |
| judge_groundedness_v1.txt | 有据性评审 | 严格 JSON | {claims}, {evidence} |
| judge_relevance_v1.txt | 相关性评审 | 严格 JSON | {question}, {answer}, {evidence} |

## 通用规则

- 所有模板的指令以中文为主，JSON 键名严格使用英文。
- 占位变量一律使用 `{curly_braces}` 命名，变量名与各模板输入小节中的字段一一对应。
- 输出 JSON 的模板必须整段输出合法 JSON，不得附带 markdown 代码块标记或额外解释。

## 失败重试约定

两个评审槽位（judge_groundedness、judge_relevance）以及 query_rewrite、router_llm 的输出都必须能被程序解析为合法 JSON。

- 解析失败属于可重试错误：系统必须以原模板重新调用该槽位并请求重新输出，绝不静默失败。
- 重试次数上限由系统配置决定；达到上限后必须记录明确的失败日志并给出默认兜底结果（例如路由默认走 hybrid，评审默认给最低分并标记异常），而不是吞掉错误。
- 生成槽位（generation）不要求 JSON，无此重试逻辑，但其输出中若出现未知引用编号 [N]，应视为缺陷并在下游校验阶段标记。
