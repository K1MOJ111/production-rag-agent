# 企业级 RAG 与业务 Agent 服务

面向企业内部知识检索与受控业务操作的 API 服务。系统提供文档入库、混合检索、Rerank、基于证据的回答、工具调用、人工确认、状态持久化、审计和角色权限控制，并支持 Docker Compose 部署。

## 核心能力

- TXT、PDF、DOCX 文档解析、中文分块、向量化、来源追踪和生命周期管理。
- pgvector 语义召回与 `pg_trgm` 关键词召回，通过 RRF 融合后使用 `qwen3-rerank` 二次排序。
- DeepSeek 基于检索资料生成回答；低相关问题拒答，回答引用必须匹配本次资料编号。
- LangGraph Agent 编排知识检索、订单查询、库存查询和需人工确认的操作草稿。
- Agent 检查点、线程归属和审计事件持久化到 PostgreSQL，服务重启后可以继续待确认流程。
- PostgreSQL 本地用户、Argon2id 密码哈希、30分钟 Bearer JWT 和 RBAC 权限控制。
- 存活/就绪检查、请求 ID、结构化耗时日志、非 root 容器运行和自动化测试。

## 系统架构

```text
Client
  │  Bearer JWT
  ▼
FastAPI
  ├─ Auth / RBAC
  ├─ Document API
  ├─ RAG Pipeline
  │    ├─ qwen3.7-text-embedding-flash
  │    ├─ pgvector + pg_trgm + RRF
  │    ├─ qwen3-rerank
  │    └─ DeepSeek + citation validation
  └─ LangGraph Agent
       ├─ knowledge_search
       ├─ get_order
       ├─ get_inventory
       └─ draft_order_cancellation → human approval
              │
              ▼
PostgreSQL
  ├─ documents / rag_chunks
  ├─ users
  ├─ LangGraph checkpoints
  └─ agent_threads / agent_audit_events
```

更详细的组件和安全边界见 [`docs/architecture.md`](docs/architecture.md)。

## 技术栈

| 层级 | 技术 |
|---|---|
| API | FastAPI、Pydantic、Uvicorn |
| 文档处理 | pypdf、python-docx、LangChain Text Splitters |
| Embedding | 百炼 `qwen3.7-text-embedding-flash`，1024维 |
| 检索 | PostgreSQL、pgvector、`pg_trgm`、RRF |
| Rerank | 百炼 `qwen3-rerank` |
| LLM | DeepSeek `deepseek-v4-flash` |
| Agent | LangGraph `StateGraph`、`interrupt`、`Command`、PostgreSQL Checkpointer |
| 认证 | PyJWT、Argon2id、Bearer Token、RBAC |
| 数据访问 | SQLAlchemy、psycopg、langchain-postgres |
| 运行 | Docker、Docker Compose、结构化日志、健康检查 |
| 质量 | unittest、固定检索 Eval、真实集成测试 |

## 权限模型

- `viewer`：查看文档和分块，执行知识库查询。
- `operator`：继承 viewer 权限，运行 Agent、确认自己的操作草稿、查看自己的审计。
- `admin`：继承全部权限，可管理文档并读取其他用户的指定线程审计；不能替其他用户确认操作。

JWT 仅保存用户 UUID 和必要标准声明。服务严格验证 `sub`、`exp`、`iss`、`aud` 和签名算法，并在每次请求中查询数据库获取最新角色和启用状态。

## 快速启动

### 1. 配置环境

```powershell
Copy-Item .env.example .env
```

在 `.env` 中设置：

- `RAG_MODE=real`
- PostgreSQL 连接配置
- 百炼和 DeepSeek API 配置
- 至少32字节的随机 `JWT_SECRET`

生成 JWT 密钥：

```powershell
[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
```

真实密钥只能保存在被 Git 忽略的 `.env` 中。

### 2. 启动服务

```powershell
docker compose --profile app up -d --build
```

检查运行状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
docker compose --profile app ps
```

接口文档：`http://127.0.0.1:8000/docs`

### 3. 创建首个管理员

```powershell
cd backend
../.venv/Scripts/python.exe -m dotenv -f ../.env run -- `
  ../.venv/Scripts/python.exe -m app.create_user admin --role admin
```

密码通过终端隐藏输入，不会进入命令历史。

### 4. 获取 Access Token

`POST /auth/token` 使用 `application/x-www-form-urlencoded`：

```text
username=admin
password=<password>
```

后续请求携带：

```text
Authorization: Bearer <access_token>
```

## API

| 方法 | 路径 | 最低权限 | 说明 |
|---|---|---|---|
| `GET` | `/health` | 公开 | 进程存活检查 |
| `GET` | `/ready` | 公开 | PostgreSQL 与 Agent 检查点就绪检查 |
| `POST` | `/auth/token` | 公开 | 用户名密码换取短期 JWT |
| `POST` | `/documents/upload` | admin | 通过 JSON 上传文本，保留兼容接口 |
| `POST` | `/documents/upload-file` | admin | 通过 multipart 上传 TXT、PDF 或 DOCX |
| `POST` | `/documents/load-samples` | admin | 导入仓库内的参考文档 |
| `GET` | `/documents` | viewer | 查询文档列表 |
| `GET` | `/documents/{id}/chunks` | viewer | 查询文档分块 |
| `DELETE` | `/documents/{id}` | admin | 删除文档及向量 |
| `POST` | `/qa/ask` | viewer | 执行混合检索与基于证据的回答 |
| `POST` | `/agent/run` | operator | 运行 Agent 或生成待确认草稿 |
| `POST` | `/agent/confirm` | operator | 批准或拒绝自己的草稿 |
| `GET` | `/agent/{thread_id}/audit` | operator | 查看授权范围内的线程审计 |

## 多格式文档入库

文件上传上限为 10 MiB。TXT 必须使用 UTF-8；PDF 按页保存来源，DOCX 按标题和段落保存来源。扫描版 PDF 不做 OCR，无法提取文字时接口会明确返回 400。

```powershell
curl.exe -X POST http://127.0.0.1:8000/documents/upload-file `
  -H "Authorization: Bearer $token" `
  -F "file=@.\travel-policy.pdf;type=application/pdf"
```

Mock 模式实际响应示例：

```json
{
  "document_id": "doc_216872c3",
  "filename": "travel-policy.pdf",
  "file_type": "pdf",
  "chunk_count": 1,
  "status": "success"
}
```

查询响应中的来源会保留文件和页码：

```json
{
  "citation_id": 1,
  "document_id": "doc_216872c3",
  "filename": "travel-policy.pdf",
  "chunk_id": "doc_216872c3_chunk_001",
  "score": 0.7559,
  "content": "Quartz travel reimbursement requires invoice and itinerary.",
  "file_type": "pdf",
  "page_number": 1,
  "section": null
}
```

相同提取内容再次上传时返回 409；删除文档会同时删除对应分块和向量。

## 开发与测试

`RAG_MODE=mock` 是确定性的本地测试适配器，不连接数据库和付费模型；真实运行使用 `RAG_MODE=real`。

安装依赖并运行普通测试：

```powershell
python -m pip install -r requirements-dev.txt
cd backend
python -m unittest discover -s tests -v
```

普通测试不会调用付费模型。PostgreSQL 持久化测试：

```powershell
cd backend
$env:RUN_POSTGRES_INTEGRATION='1'
../.venv/Scripts/python.exe -m dotenv -f ../.env run -- `
  ../.venv/Scripts/python.exe -m unittest `
  tests.test_m4_persistence tests.test_m6_auth.AuthPostgresTest -v
```

真实检索、Eval 和 Agent 测试会产生少量模型费用，默认跳过：

```powershell
cd backend
$env:RUN_REAL_INTEGRATION='1'
../.venv/Scripts/python.exe -m dotenv -f ../.env run -- `
  ../.venv/Scripts/python.exe -m unittest `
  tests.test_real_integration tests.test_m2_real_eval tests.test_m3_real_agent -v
```

## 数据与安全边界

- 订单和库存工具当前使用内置参考适配器，没有连接外部订单系统。
- 取消订单只生成并审核操作草稿；即使批准也不会执行外部写操作。
- 文档解析支持可提取文字的 TXT、PDF 和 DOCX；不支持 OCR、扫描件识别、复杂版式还原或多模态内容。
- 引用校验保证引用编号来自本次检索结果，但不等同于逐句事实一致性评估。
- JWT 不加密，当前没有 Refresh Token、撤销列表、开放注册、登录限流或企业 OIDC。
- 当前交付物是 Docker Compose 部署基线；公开网络部署前必须增加 HTTPS、入口限流、集中日志、指标告警和密钥托管。

## 项目文档

- [`docs/architecture.md`](docs/architecture.md)：架构、数据流和安全设计。
- [`docs/stage_status.md`](docs/stage_status.md)：阶段门禁和验证证据。

本项目采用 [MIT License](LICENSE)。
