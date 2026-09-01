# RAG 项目面试讲解卡片

## 30 秒介绍

我做的是一个企业文档知识库问答系统。用户可以上传或读取企业制度文档，系统会把文档清洗后切成 chunk，再做 Embedding 并存入向量库。用户提问时，系统把问题向量化，检索相关 chunk，拼成 Prompt，再交给大模型生成答案，并返回引用来源。

项目保留零费用 mock 模式用于回归测试；真实模式使用百炼 Embedding、PostgreSQL+pgvector、混合召回、百炼 Rerank 和 DeepSeek。单个 LangGraph Agent 可选择知识检索、演示订单或库存工具，取消订单只生成需人工确认的草稿；待确认状态和最小审计持久化在 PostgreSQL。运行层补了请求 ID、结构化耗时日志、数据库就绪检查和非 root Docker 镜像。认证使用 PostgreSQL 本地用户、Argon2id 密码哈希、30分钟 Bearer JWT 和三角色权限，Agent 线程归属直接使用 JWT `sub`。

## 核心流程

```text
文档上传 / 读取示例文档
 -> 文本清洗
 -> chunk 切分
 -> 百炼 Embedding
 -> 存入 PostgreSQL+pgvector
 -> 用户提问
 -> 问题向量化
 -> 向量与关键词混合召回
 -> qwen3-rerank 二次排序
 -> 拼接 Prompt
 -> DeepSeek 生成答案
 -> 校验 [资料 N] 引用
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

POST /agent/run
运行单 Agent，返回回答、实际工具名或待确认草稿。

POST /agent/confirm
批准或拒绝草稿；当前不会执行真实订单操作。

POST /auth/token
本地用户名和密码换取短期 JWT；JWT `sub` 是可信用户 ID。

GET /health 与 GET /ready
分别用于进程存活和数据库就绪检查。
```

## 为什么要切 chunk

企业文档通常很长，不能全部塞进 Prompt。切成 chunk 后，系统可以只检索和问题最相关的小片段，减少无关内容干扰，也能降低 token 成本。

## 为什么返回 sources

返回 sources 可以告诉用户答案依据来自哪份文档、哪个片段，方便追溯，也能降低大模型无依据胡说的风险。

## mock 版怎么解释

mock 版用于无密钥的快速演示和回归测试；真实模式已经接入模型 API 和持久化数据库。两种模式走同一组文档、检索、Prompt 和接口入口，便于低成本测试。

## 如果面试官问项目不足

可以这样回答：

当前已完成真实 Embedding、向量数据库、混合检索、Rerank、引用校验、固定 Eval、单 Agent、持久检查点、JWT 线程归属、RBAC、最小审计和本地容器部署验证。边界是订单/库存仍为演示数据，本地认证没有 Refresh Token、开放注册、登录限流或企业 OIDC；日志尚未接入集中监控平台，镜像也没有发布到真实生产环境。
