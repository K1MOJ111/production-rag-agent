# 阶段门禁与偏离检查

## M0：基线与仓库安全

- 状态：完成。
- 证据：原始 Mock 基线可回退；普通测试通过；`.env` 被 Git 忽略；MIT License 已提交。
- 偏离检查：无。没有提前接入真实服务或把 Mock 描述成生产实现。

## M1：真实 RAG

- 状态：代码完成，真实运行验收阻塞。
- 已完成：LangChain 中文切块、百炼 OpenAI 兼容 Embedding、DeepSeek 回答、`PGVectorStore`、文档 SHA-256/状态、重启持久化检查、引用编号和真实集成测试入口。
- 已验证：Mock 主链路和 M1 服务单元测试通过；真实集成测试默认跳过，不产生费用。
- 未验证：本机没有 PostgreSQL/pgvector 或 Docker；安装前检查确认主板 BIOS 当前关闭 AMD 虚拟化（`Virtualization Enabled In Firmware: No`），Docker 暂时无法运行；未配置百炼/DeepSeek/API 地址；尚未执行付费真实问答与拒答。
- 方向修正：原调研默认 `text-embedding-v4`；百炼当前官方文档已推荐 `qwen3.7-text-embedding` 系列，因此默认改为低成本 Flash 版并保持 1024 维。这是模型版本更新，不改变百炼 Embedding + pgvector 的目标架构。
- 偏离检查：无。仍是单体 FastAPI、单 PostgreSQL、双模式渐进替换；没有提前加入 Redis、多 Agent、MCP、Rerank 或权限系统。
- 门禁结论：不能宣称 M1 完成，也不能自动进入 M2。完成数据库准备和付费集成检查后再次审查。

一手依据：[langchain-postgres](https://github.com/langchain-ai/langchain-postgres)、[百炼 Embedding](https://help.aliyun.com/zh/model-studio/embedding)、[DeepSeek API](https://api-docs.deepseek.com/zh-cn/)。
