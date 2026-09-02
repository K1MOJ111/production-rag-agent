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
- 已完成：单个 LangGraph `StateGraph`、知识检索工具、内置订单/库存只读参考适配器、取消订单草稿、`interrupt`/`Command` 人工确认、API 和 `used_tools` 审计字段。
- 已验证：本地 Agent 的订单、库存、批准、拒绝和不执行副作用测试通过；真实 DeepSeek 工具调用覆盖四个工具及中断恢复流程。
- 数据边界：订单和库存来自代码内参考数据；批准草稿后仍为 `executed=false`，未连接任何外部业务系统。
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

## M5：运行可观测性与部署就绪

- 状态：完成。
- 已完成：`/health` 存活检查、真实模式 PostgreSQL `/ready` 就绪检查、响应 `X-Request-Id`、结构化请求耗时日志、非 root Docker 镜像和可选 Compose `app` profile。
- 已验证：普通测试 25 项中 21 项通过、4 项真实链路按环境开关跳过；Compose 配置校验通过；镜像构建成功；mock 容器和真实模式 Compose 容器均返回 200；真实模式报告 `database=ok`，容器健康、非 root 运行且日志可见。
- 运行边界：`/ready` 不调用付费模型 API，只验证当前必要的数据库依赖；日志输出到标准输出，尚未接入指标、链路追踪或集中日志平台。
- 部署边界：完成的是本机 Docker/Compose 可运行证据，不是云端或公司生产环境部署。
- 偏离检查：无。继续使用标准库日志、现有 FastAPI 和 Docker Compose，没有在当前部署范围内引入 Prometheus、OpenTelemetry、Kubernetes 或云资源。
- 门禁结论：通过。M0-M5 既定升级阶段完成；后续认证、真实业务系统或目标部署环境都需要先确定具体边界，不能自动视为已完成。

## M6：本地认证与角色权限

- 状态：完成。
- 已完成：PostgreSQL `users` 表、Argon2id 密码哈希、统一登录失败响应、30分钟 HS256 Bearer JWT、`sub`/`exp`/`iss`/`aud` 严格校验、数据库实时角色/启用状态检查和 viewer/operator/admin 权限。
- 身份替换：Agent API 不再接受 `X-Actor-Id`；线程归属和审计直接使用已验证 JWT 的用户 UUID。admin 只能越过归属限制读取指定线程审计，不能替他人确认操作。
- 已验证：普通测试30项中25项通过、5项真实链路按开关跳过；伪造、过期或缺失 Token 返回401，角色越权返回403，日志不包含 Token；PostgreSQL 只保存 Argon2 哈希；M4持久化隔离和真实四工具 Agent+JWT 回归通过。
- 本机状态：随机 `JWT_SECRET` 已写入被 Git 忽略的 `.env`；首个管理员尚未创建，因为管理员密码必须由用户在终端隐藏输入。
- 安全边界：JWT不加密且没有撤销列表；未实现 Refresh Token、开放注册、第三方登录、登录限流或账号锁定。公开部署前必须配置 HTTPS 和网关限流。
- 偏离检查：无。认证通过单一 `Principal(user_id, role)` seam 接入，复用 M4 线程归属和审计，没有把 JWT 逻辑放入 Agent，也没有搭建不需要的完整 OAuth 授权服务器。
- 门禁结论：通过。M0-M6 已形成可运行、可验证的本地部署与安全基线；真实业务接入和外部部署仍需单独确定目标系统与授权边界。

## M7：多格式文档入库与可追溯引用

- 状态：完成。
- 已完成：保留文本 JSON 接口，新增 multipart TXT/PDF/DOCX 上传；PDF 按页、DOCX 按标题和段落保存来源；文档列表、分块和问答来源返回文件类型及位置元数据。
- 输入保护：文件上限 10 MiB；拒绝不支持类型、空文件、非 UTF-8 TXT、损坏文件和重复内容；无可提取文字的 PDF 明确返回 OCR/扫描件不支持。
- 数据一致性：内存和 PostgreSQL 适配器采用相同内容哈希与来源字段；删除文档继续删除对应分块和向量；样例导入和既有内部调用仍可幂等复用已有文档。
- 已验证：普通测试33项中27项通过、6项按环境开关跳过；新增Mock测试覆盖三种格式、异常输入、重复、引用来源和删除。M4/M6/M7 PostgreSQL无付费套件3项通过；M7验证来源元数据持久化和删除。Docker镜像重新构建成功并确认包含解析依赖、以非root用户运行。
- 兼容性提示：Python 3.14 会提示当前 Windows Selector 事件循环策略将在 3.16 移除；PostgreSQL定向测试进程退出时仍有 psycopg 连接 `ResourceWarning`。两者未导致测试失败，但应在升级 Python 或 langchain-postgres 前完成生命周期复核，不能记为无警告运行。
- 运行边界：仅提取文本，不做 OCR、复杂版式还原、表格语义恢复或图片理解；真实模型端到端回归本阶段未重复运行，不能记为本次通过。
- 偏离检查：无。继续复用现有 DocumentService、PostgreSQL 和检索链路，只增加 pypdf、python-docx 两个必要解析依赖；没有引入多Agent、对象存储、OCR或前端。
- 门禁结论：通过。下一阶段是 M8 本地 PostgreSQL 业务数据适配器与受控写操作。

## M8：本地业务数据适配器与安全写操作

- 状态：完成。
- 已完成：新增 `orders`、`inventory`、`cancellation_requests` 表和PostgreSQL业务适配器；订单、库存工具不再读取Agent节点内置字典。真实模式启动时以冲突忽略方式导入最小参考数据。
- 写操作门禁：取消工具只生成草稿并中断；拒绝不写业务表；批准时重新校验订单状态和线程所有者，在一个事务内更新订单、写取消记录和确认审计。
- 幂等与恢复：`thread_id + action + order_id` 唯一约束防止重试重复取消；PostgresSaver恢复后仍由原用户确认，重复确认返回冲突，其他用户（包括admin）不能代确认。
- 审计：运行与确认事件可追踪 actor、thread、action、result 和 request_id；批准事件与本地业务状态共享事务。
- 已验证：普通测试36项中27项通过、9项按环境开关跳过；M4/M6/M7/M8 PostgreSQL无付费套件6项全部通过，其中M8覆盖查询、草稿、批准、拒绝、幂等、viewer/admin越权和重启恢复；Docker镜像重建成功，应用与PostgreSQL容器均健康，真实模式 `/ready` 返回数据库就绪。真实模型Agent回归保留测试但本阶段未运行，不能记为本次通过。
- 业务边界：这是本地PostgreSQL模拟业务系统与真实数据库写入，不是企业ERP接入；没有外部订单、支付、仓储或售后副作用。
- 偏离检查：无。业务seam只有PostgreSQL运行适配器和内存测试适配器，继续使用单Agent、单PostgreSQL和既有权限/审计；未加入消息队列、分布式事务或额外服务。
- 门禁结论：通过。下一阶段是 M9 可量化Eval与分阶段运行指标。

一手依据：[langchain-postgres](https://github.com/langchain-ai/langchain-postgres)、[百炼 Embedding](https://help.aliyun.com/zh/model-studio/embedding)、[百炼 Rerank](https://help.aliyun.com/zh/model-studio/text-rerank-api)、[DeepSeek API](https://api-docs.deepseek.com/zh-cn/)、[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[LangGraph PostgreSQL memory](https://docs.langchain.com/oss/python/langgraph/add-memory)、[FastAPI JWT](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)、[OAuth 2.0 Security BCP](https://www.rfc-editor.org/rfc/rfc9700.html)、[OWASP JWT](https://cheatsheetseries.owasp.org/cheatsheets/JSON_Web_Token_Cheat_Sheet.html)。
