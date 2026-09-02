# 系统架构与安全设计

## 目标

系统为企业内部知识检索和受控业务操作提供统一 API。知识回答必须基于检索资料；可能产生业务影响的操作必须先生成草稿并经过人工确认。

## RAG 数据流

1. 文本 JSON 或 TXT、PDF、DOCX 文件进入 FastAPI 文档接口。
2. PDF 按页、DOCX 按标题/段落提取文字；文本经过清洗和中文分块，生成稳定的内容哈希。
3. 百炼 Embedding 将分块转换为向量并写入 PostgreSQL+pgvector。
4. 查询同时执行向量召回和 `pg_trgm` 关键词召回。
5. 两路结果通过 RRF 融合，再由 `qwen3-rerank` 排序。
6. 低于相关度阈值的查询直接拒答。
7. DeepSeek 仅基于检索资料生成回答，返回前校验引用编号。

`documents` 保存文件类型和内容哈希；`rag_chunks` 保存文件名、文件类型、页码或章节/段落位置。检索、Rerank 和回答接口沿用同一份来源元数据。扫描版 PDF 在入库边界被拒绝，不进入空分块或向量表。

## Agent 数据流

LangGraph `StateGraph` 负责模型与工具之间的状态流转。当前工具包括：

- `knowledge_search`：复用 RAG 检索链路。
- `get_order`：通过业务适配器读取 PostgreSQL 订单。
- `get_inventory`：通过业务适配器读取 PostgreSQL 库存。
- `draft_order_cancellation`：生成取消草稿并触发人工确认。

取消流程使用 LangGraph `interrupt` 暂停，通过 `Command` 恢复。PostgreSQL Checkpointer 保存完整执行状态，因此应用重启后仍可继续待确认流程。草稿阶段只读；批准后，PostgreSQL业务适配器在一个事务中写入 `cancellation_requests`、更新允许取消的订单状态并记录确认审计。幂等键由线程、动作和订单组成，节点重试不会重复执行。拒绝不写业务表。

业务表和种子数据构成可本地验证的模拟业务系统，并非企业 ERP。Agent 只依赖业务适配器的小接口，SQL、事务、行锁、状态校验和幂等约束都集中在适配器内。

## 身份与授权

本地用户保存在 PostgreSQL：

- 密码使用 Argon2id 单向哈希。
- 登录成功后签发30分钟 HS256 JWT。
- JWT 包含 `sub`、`iss`、`aud`、`iat` 和 `exp`。
- 服务固定允许的签名算法并严格验证必要声明。
- 每个受保护请求根据 `sub` 重新查询用户角色和启用状态。

认证层只向业务层暴露 `Principal(user_id, role)`。Agent 不解析 JWT，直接使用可信用户 UUID 进行线程归属和审计隔离。

## Agent 安全边界

- operator 只能运行和确认自己的线程。
- admin 可以读取其他用户的指定线程审计，但不能替其他用户确认操作。
- 审计保存事件、状态、工具名和动作元数据，不保存完整提问、模型回答、密码或 Token。
- Checkpointer 使用严格 MessagePack 反序列化配置。

## 运行架构

Docker Compose 包含 FastAPI 应用和 PostgreSQL+pgvector：

- 应用容器使用非 root 用户。
- `/health` 用于存活检查。
- `/ready` 验证数据库和 Agent 检查点连接。
- 每个响应包含 `X-Request-Id`。
- 结构化日志记录请求方法、路径、状态码和耗时，不记录请求头或请求正文。
- `.env` 和密钥不进入镜像与 Git。

## 部署前置条件

面向公开网络或多实例部署时，需要在当前基线上补充：

- HTTPS 和可信反向代理；
- 登录限流、账号锁定或外部身份提供商；
- JWT 撤销或 Refresh Token 策略；
- 集中日志、指标、链路追踪和告警；
- 托管密钥服务和密钥轮换；
- 企业 ERP 字段映射、调用认证、超时重试、对账与补偿策略。
