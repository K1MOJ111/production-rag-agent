# RAG 项目面试讲解卡片

## 30 秒介绍

我做的是一个企业文档知识库问答系统。用户可以上传或读取企业制度文档，系统会把文档清洗后切成 chunk，再做 Embedding 并存入向量库。用户提问时，系统把问题向量化，检索相关 chunk，拼成 Prompt，再交给大模型生成答案，并返回引用来源。

第一版我先用了 mock Embedding 和 mock LLM，目的是先跑通 RAG 主链路。后续可以把 mock 模块替换成真实 Embedding API、真实向量数据库和真实大模型 API。

## 核心流程

```text
文档上传 / 读取示例文档
 -> 文本清洗
 -> chunk 切分
 -> mock Embedding
 -> 存入内存向量库
 -> 用户提问
 -> 问题向量化
 -> 相似度检索 top-k
 -> 拼接 Prompt
 -> mock LLM 生成答案
 -> 返回 answer + sources + prompt
```

## 可以讲的接口

```text
POST /documents/load-samples
读取示例企业文档，方便演示。

POST /documents/upload
上传一段文档文本，后端完成清洗、切块、向量化和入库。

GET /documents
查看已经入库的文档。

GET /documents/{document_id}/chunks
查看某个文档切成了哪些 chunk。

POST /qa/ask
用户提问，系统检索相关 chunk，拼 Prompt，返回答案和引用来源。
```

## 为什么要切 chunk

企业文档通常很长，不能全部塞进 Prompt。切成 chunk 后，系统可以只检索和问题最相关的小片段，减少无关内容干扰，也能降低 token 成本。

## 为什么返回 sources

返回 sources 可以告诉用户答案依据来自哪份文档、哪个片段，方便追溯，也能降低大模型无依据胡说的风险。

## mock 版怎么解释

mock 版不是最终生产方案，而是 MVP。它的价值是先验证工程流程：文档处理、chunk 切分、检索、Prompt 拼接和答案返回。等流程跑通后，把 `MockEmbeddingService` 替换成真实 Embedding 模型，把 `MockLLMService` 替换成真实大模型接口即可。

## 如果面试官问项目不足

可以这样回答：

第一版是为了跑通主链路，所以还没有接真实向量数据库、真实大模型、权限系统和效果评估。后续我会优先补三块：第一，接入真实 Embedding 和 LLM；第二，加入 Rerank、相似度阈值和引用来源；第三，增加问答日志和人工评估集，用来持续优化检索效果。
