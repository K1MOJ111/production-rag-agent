# 阶段门禁与偏离检查

## M0：基线与仓库安全

- 状态：完成。
- 证据：原始 Mock 基线可回退；普通测试通过；`.env` 被 Git 忽略；MIT License 已提交。
- 偏离检查：无。没有提前接入真实服务或把 Mock 描述成生产实现。

## M1：真实 RAG

- 状态：代码和本地数据库验收完成，模型 API 验收阻塞。
- 已完成：LangChain 中文切块、百炼 OpenAI 兼容 Embedding、DeepSeek 回答、`PGVectorStore`、文档 SHA-256/状态、重启持久化检查、引用编号和真实集成测试入口。
- 已验证：Docker Engine 可用；PostgreSQL 16.12 + pgvector 容器健康；使用本地假向量完成建表、写入、关闭重开、列出、读取分块、检索和删除；普通测试 11 项通过，付费集成测试 1 项按预期跳过；依赖检查通过。
- 未验证：未配置百炼/DeepSeek 密钥及百炼工作空间 API 地址；尚未执行付费真实 Embedding、相关问答与无关问题拒答。
- 方向修正：原调研默认 `text-embedding-v4`；百炼当前官方文档已推荐 `qwen3.7-text-embedding` 系列，因此默认改为低成本 Flash 版并保持 1024 维。这是模型版本更新，不改变百炼 Embedding + pgvector 的目标架构。
- 偏离检查：无。仍是单体 FastAPI、单 PostgreSQL、双模式渐进替换；没有提前加入 Redis、多 Agent、MCP、Rerank 或权限系统。
- 门禁结论：不能宣称 M1 完成，也不能自动进入 M2。配置本地密钥并完成付费集成检查后再次审查。

一手依据：[langchain-postgres](https://github.com/langchain-ai/langchain-postgres)、[百炼 Embedding](https://help.aliyun.com/zh/model-studio/embedding)、[DeepSeek API](https://api-docs.deepseek.com/zh-cn/)。
