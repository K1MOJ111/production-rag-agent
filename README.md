# 企业文档知识库问答系统 MVP

这是一个用于面试讲解的最小 RAG 项目。它不追求复杂功能，重点是跑通企业文档问答的主链路：

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

进入后端目录：

```powershell
cd E:\CODEX\bbos\rag-demo\backend
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
