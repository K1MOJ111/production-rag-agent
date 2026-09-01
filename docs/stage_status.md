# 阶段门禁与偏离检查

## M0：基线与仓库安全

- 状态：完成。
- 证据：原始 Mock 基线可回退；普通测试通过；`.env` 被 Git 忽略；MIT License 已提交。
- 偏离检查：无。没有提前接入真实服务或把 Mock 描述成生产实现。

## M1：真实 RAG

- 状态：完成。
- 已完成：LangChain 中文切块、百炼 OpenAI 兼容 Embedding、DeepSeek 回答、`PGVectorStore`、文档 SHA-256/状态、重启持久化检查、引用编号和真实集成测试入口。
- 已验证：Docker Engine 可用；PostgreSQL 16.12 + pgvector 容器健康；使用本地假向量完成建表、写入、关闭重开、列出、读取分块、检索和删除；依赖检查通过。
- 已验证：真实百炼 Embedding、PostgreSQL 重连持久化、相关检索、无关问题拒答和 DeepSeek 引用回答均通过。
- 方向修正：原调研默认 `text-embedding-v4`；百炼当前官方文档已推荐 `qwen3.7-text-embedding` 系列，因此默认改为低成本 Flash 版并保持 1024 维。这是模型版本更新，不改变百炼 Embedding + pgvector 的目标架构。
- 偏离检查：无。仍是单体 FastAPI、单 PostgreSQL、双模式渐进替换；没有提前加入 Redis、多 Agent、MCP、Rerank 或权限系统。
- 门禁结论：通过，已进入 M2。

## M2：检索质量与评估

- 状态：完成。
- 已完成：pgvector 向量召回 + `pg_trgm` 关键词召回、RRF 融合、百炼 `qwen3-rerank`、回答引用编号校验、5 题固定检索 Eval。
- 已验证：普通测试 13 项通过；真实集成与固定 Eval 2 项通过；3 个已知问题首位命中预期文档且超过阈值，2 个无关问题低于拒答阈值。
- 边界：引用校验只验证至少存在一个引用且编号属于本次资料，不能证明每句话都被资料支持；更细粒度的事实一致性评估暂不加入。
- 偏离检查：无。仍使用单体 FastAPI 和单 PostgreSQL；未提前加入 Elasticsearch、Redis、多 Agent、权限或生产部署。
- 门禁结论：通过，可以进入 M3。

## M3：单 Agent 与人工确认

- 状态：完成。
- 已完成：单个 LangGraph `StateGraph`、知识检索工具、演示订单/库存只读工具、取消订单草稿、`interrupt`/`Command` 人工确认、API 和 `used_tools` 审计字段。
- 已验证：本地 Agent 的订单、库存、批准、拒绝和不执行副作用测试通过；真实 DeepSeek 工具调用覆盖四个工具及中断恢复流程。
- 数据边界：订单和库存为明确的代码内演示数据；批准草稿后仍为 `executed=false`，未连接任何真实业务系统。
- 运行边界：检查点使用进程内 `InMemorySaver`，服务重启后待确认线程不会保留；持久化、用户隔离和审计应在后续生产化阶段完成。
- 偏离检查：无。仍是单 Agent；没有多 Agent、MCP、真实写操作或未经确认的副作用。
- 门禁结论：通过，已进入 M4。

## M4：Agent 状态可靠性

- 状态：完成。
- 已完成：LangGraph PostgreSQL 检查点、严格 MessagePack 反序列化、`thread_id → actor_id` 归属、最小审计表和审计查询 API。
- 已验证：待确认草稿跨 Agent 实例恢复；其他 `actor_id` 无法读取或确认；原调用方可继续；运行与确认审计跨实例保留；真实四工具 Agent 回归通过。
- 数据边界：审计保存事件、状态、工具名和动作元数据，不保存完整提问、模型回答或密钥。
- 身份边界：`X-Actor-Id` 必须由未来的可信网关或认证层注入；当前实现只做线程归属隔离，不构成身份认证。
- 偏离检查：无。继续复用单 PostgreSQL 和单 Agent；没有新增 Redis、多 Agent、真实业务写入或伪造生产认证完成状态。
- 门禁结论：通过。下一优先级是 M5 运行可观测性与部署就绪检查。

一手依据：[langchain-postgres](https://github.com/langchain-ai/langchain-postgres)、[百炼 Embedding](https://help.aliyun.com/zh/model-studio/embedding)、[百炼 Rerank](https://help.aliyun.com/zh/model-studio/text-rerank-api)、[DeepSeek API](https://api-docs.deepseek.com/zh-cn/)、[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[LangGraph PostgreSQL memory](https://docs.langchain.com/oss/python/langgraph/add-memory)。
