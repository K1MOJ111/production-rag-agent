# 企业文档知识库问答系统 MVP

这是一个用于面试讲解的最小 RAG 项目。它不追求复杂功能，重点是跑通企业文档问答的主链路：

## 当前阶段与事实边界

- **M0 已完成**：已保留原始 Mock 版本，并建立 Git、测试、许可证和密钥保护规则。
- **M1 已完成**：百炼 Embedding、DeepSeek、PostgreSQL+pgvector、持久化和真实问答均已验收。
- **M2 已完成**：已加入向量+关键词混合召回、百炼 Rerank、引用校验和固定检索 Eval。
- **M3 已完成**：单个 LangGraph Agent 可编排知识检索、演示订单/库存查询和需人工确认的取消草稿。
- **M4 已完成**：Agent 检查点持久化到 PostgreSQL，并增加线程归属约束和最小审计查询。
- **M5 已完成**：已加入请求 ID、结构化耗时日志、数据库就绪检查和非 root Docker 部署验证。
- **M6 已完成**：PostgreSQL 本地用户、Argon2id 密码哈希、短期 Bearer JWT 和 viewer/operator/admin 权限已接入。
- 默认 `RAG_MODE=mock`，仍可零费用运行；只有显式切换到 `real` 才连接数据库和模型 API。
- 当前 Agent 仍使用演示业务数据；JWT `sub` 已替代调用方自报身份，但当前只有本地账号体系、结构化日志和容器部署验证，不能表述为已接入企业登录、真实订单系统或已生产部署。

```text
上传或读取文档
 -> 文本清洗
 -> 切分 chunk
 -> mock Embedding
 -> 存入本地内存向量库
 -> 用户提问
 -> 问题向量化
 -> 检索相关 chunk
 -> 拼接 Prompt
 -> mock LLM 生成答案
 -> 返回答案和引用来源
```

## 为什么先用 mock

第一版不需要 API Key。项目用 `MockEmbeddingService` 模拟文本向量化，用 `MockLLMService` 模拟大模型回答。这样可以先验证 RAG 工程流程，后续只需要替换这两个模块：

```text
MockEmbeddingService -> 真实 Embedding API
MockLLMService       -> 真实 LLM API
```

## 运行方式

在项目根目录安装依赖并进入后端目录：

```powershell
python -m pip install -r requirements.txt
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

打开接口文档：

```text
http://127.0.0.1:8000/docs
```

## 推荐演示步骤

1. 调用 `POST /documents/load-samples` 加载示例企业文档。
2. 调用 `GET /documents` 查看文档列表。
3. 调用 `POST /qa/ask` 提问，例如：

```json
{
  "question": "差旅报销需要准备哪些材料？",
  "top_k": 3
}
```

4. 查看返回的 `answer`、`sources` 和 `prompt`。

## 主要接口

### POST /documents/upload

用 JSON 上传一段文档文本。

```json
{
  "filename": "员工报销制度.txt",
  "content": "这里放文档正文"
}
```

后端会完成文本清洗、chunk 切分、mock Embedding 和入库。

### POST /documents/load-samples

读取 `sample_docs` 目录下的示例文档，方便本地演示。

### GET /documents

查看已经入库的文档。

### GET /documents/{document_id}/chunks

查看某篇文档被切成了哪些 chunk。

### POST /qa/ask

用户提问，系统检索相关 chunk，拼 Prompt，并返回 mock 答案。

```json
{
  "question": "远程办公访问公司系统有什么要求？",
  "top_k": 3
}
```

## 面试讲法

这个项目是一个企业文档知识库问答系统。用户上传文档后，系统会对文档进行清洗和 chunk 切分，再生成 Embedding 并存入向量库。用户提问时，系统会把问题向量化，从向量库检索相关 chunk，再把参考资料和问题拼成 Prompt，交给大模型生成答案，并返回引用来源。

第一版我先用 mock Embedding 和 mock LLM 跑通主流程，避免一开始被 API Key 和复杂部署卡住。后续可以替换成真实 Embedding 模型、真实向量数据库和真实大模型 API。

## 基线测试

```powershell
python -m pip install -r requirements-dev.txt
cd backend
python -m unittest discover -s tests -v
```

测试固定当前健康检查、JWT/RBAC、文档加载、已知问题检索、低相关拒答、请求校验和切块重叠行为。普通测试只使用 Mock，不调用付费 API。

## M1 真实模式

复制 `.env.example` 为 `.env`，填写本机 PostgreSQL 连接串、模型 API 配置和至少 32 字节的随机 `JWT_SECRET`。不要把 `.env` 提交到 Git。

```powershell
Copy-Item .env.example .env
# 编辑 .env，将 RAG_MODE 改为 real 并填写必需变量
docker compose up -d postgres
cd backend
python -m uvicorn app.main:app --env-file ../.env --port 8000
```

默认模型为百炼 `qwen3.7-text-embedding-flash`（1024 维）、`qwen3-rerank` 和 `deepseek-v4-flash`。模型名、维度、地址和临时拒答阈值均由环境变量控制；更换向量维度后必须重建向量表。

真实集成检查会连接 PostgreSQL并产生少量模型费用，因此默认跳过：

```powershell
cd backend
../.venv/Scripts/python.exe -m dotenv -f ../.env run -- `
  ../.venv/Scripts/python.exe -m unittest `
  tests.test_real_integration tests.test_m2_real_eval -v
```

执行前还需在 `.env` 中设置 `RUN_REAL_INTEGRATION=1`。该检查验证重连持久化、混合检索、Rerank、相关问答、无关问题拒答、引用校验和固定 5 题 Eval，结束后删除测试文档。

## M3 Agent 接口

`POST /agent/run` 接收 `message` 和可选 `thread_id`，返回回答、实际调用的 `used_tools`，或 `needs_confirmation`。`POST /agent/confirm` 用同一 `thread_id` 批准或拒绝草稿。两者必须使用 operator 或 admin 的 Bearer JWT；线程归属来自 JWT `sub`，其他用户不能读取或继续。批准只记录草稿结果，不会执行订单操作。

`GET /agent/{thread_id}/audit` 返回该调用方的运行/确认状态、工具名和动作元数据。审计不保存完整提问、模型回答或密钥。

演示工具：`knowledge_search`、`get_order`、`get_inventory`、`draft_order_cancellation`。订单和库存是代码内固定演示数据，不是公司或生产数据。

免费验证待确认状态可跨 Agent 实例恢复：

```powershell
cd backend
$env:RUN_POSTGRES_INTEGRATION='1'
../.venv/Scripts/python.exe -m dotenv -f ../.env run -- `
  ../.venv/Scripts/python.exe -m unittest tests.test_m4_persistence -v
```

## M5 运行与部署检查

`GET /health` 只检查进程存活；`GET /ready` 在真实模式下执行 PostgreSQL 连通检查。每个响应带 `X-Request-Id`，应用日志记录请求方法、路径、状态码和耗时，不记录请求正文或密钥。

应用服务使用 Compose 的 `app` profile，避免原有 `docker compose up -d postgres` 意外启动应用：

```powershell
docker compose --profile app up -d --build
Invoke-RestMethod http://127.0.0.1:8000/ready
docker compose --profile app logs -f app
```

镜像以非 root 用户运行；真实配置仍从本机 `.env` 注入，不会复制进镜像。

## M6 本地认证与权限

生成随机 JWT 密钥并填入 `.env` 的 `JWT_SECRET`：

```powershell
[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
```

创建首个管理员；密码通过终端隐藏输入，不进入命令历史：

```powershell
cd backend
../.venv/Scripts/python.exe -m dotenv -f ../.env run -- `
  ../.venv/Scripts/python.exe -m app.create_user admin --role admin
```

`POST /auth/token` 使用表单字段 `username`、`password` 换取30分钟 Access Token。后续请求携带 `Authorization: Bearer <token>`。

- `viewer`：查看文档、分块并查询知识库。
- `operator`：继承 viewer，并运行 Agent、确认和查看自己的操作草稿。
- `admin`：继承全部权限，可上传/删除文档并查看其他用户的指定线程审计。

JWT 只保存用户 ID 和必要标准声明；每次请求仍查询 PostgreSQL 获取最新角色和启用状态。未实现 Refresh Token、开放注册、第三方登录、登录限流或账号锁定，因此公开部署前还需要网关限流和 HTTPS。

## 后续里程碑

- M1：接入真实 Embedding、LLM 和 PostgreSQL+pgvector。
- M2：加入混合检索、Rerank、引用校验与固定 Eval。
- M3：用单个 LangGraph Agent 编排知识检索、订单/库存查询和需人工确认的操作草稿（已完成）。
- M4：用 PostgreSQL 持久化 Agent 状态，并加入线程归属和最小审计（已完成）。
- M5：补运行可观测性、就绪检查和部署验证（已完成）。
- M6：本地用户、Argon2id、Bearer JWT 与三角色权限（已完成）。

`.env.example` 只声明后续阶段需要的变量名；真实密钥必须写入被 Git 忽略的 `.env`。本项目采用 [MIT License](LICENSE)。

阶段门禁及偏离检查记录见 [`docs/stage_status.md`](docs/stage_status.md)。
