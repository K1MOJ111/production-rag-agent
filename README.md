# 企业文档知识库问答系统 MVP

这是一个用于面试讲解的最小 RAG 项目。它不追求复杂功能，重点是跑通企业文档问答的主链路：

## 当前阶段与事实边界

- **M0 已完成**：已保留原始 Mock 版本，并建立 Git、测试、许可证和密钥保护规则。
- **M1 已完成代码和本地数据库验收，尚未完成模型 API 验收**：已实现百炼 Embedding、DeepSeek、PostgreSQL+pgvector、文档哈希/状态、持久化和引用编号。
- 默认 `RAG_MODE=mock`，仍可零费用运行；只有显式切换到 `real` 才连接数据库和模型 API。
- 当前没有 LangGraph Agent、混合检索/Rerank、权限审计或生产部署，不能提前表述为生产级 RAG 或已实现 Agent。

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

测试固定当前健康检查、文档加载、已知问题检索、低相关拒答、请求校验和切块重叠行为。测试只使用 Mock，不调用付费 API。

## M1 真实模式

复制 `.env.example` 为 `.env`，填写本机 PostgreSQL 连接串、百炼 API Key 和百炼控制台提供的 OpenAI 兼容 `base_url`。不要把 `.env` 提交到 Git。

```powershell
Copy-Item .env.example .env
# 编辑 .env，将 RAG_MODE 改为 real 并填写必需变量
docker compose up -d postgres
cd backend
python -m uvicorn app.main:app --env-file ../.env --port 8000
```

默认模型为当前百炼低成本文本向量模型 `qwen3.7-text-embedding-flash`（1024 维）和 `deepseek-v4-flash`。模型名、维度、地址和临时拒答阈值均由环境变量控制；更换向量维度后必须重建向量表。

真实集成检查会连接 PostgreSQL并产生少量模型费用，因此默认跳过：

```powershell
cd backend
../.venv/Scripts/python.exe -m dotenv -f ../.env run -- `
  ../.venv/Scripts/python.exe -m unittest tests.test_real_integration -v
```

执行前还需在 `.env` 中设置 `RUN_REAL_INTEGRATION=1`。该检查验证重连后数据仍存在、相关问答、无关问题拒答和 DeepSeek 回答，结束后删除测试文档。

## 后续里程碑

- M1：接入真实 Embedding、LLM 和 PostgreSQL+pgvector。
- M2：加入混合检索、Rerank、引用校验与固定 Eval。
- M3：用单个 LangGraph Agent 编排知识检索、订单/库存查询和需人工确认的操作草稿。

`.env.example` 只声明后续阶段需要的变量名；真实密钥必须写入被 Git 忽略的 `.env`。本项目采用 [MIT License](LICENSE)。

阶段门禁及偏离检查记录见 [`docs/stage_status.md`](docs/stage_status.md)。
