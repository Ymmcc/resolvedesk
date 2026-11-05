# ResolveDesk · 软件售后诊断与工单协同

一个个人可独立理解的 SaaS 售后业务项目。以虚构的「商舟订单云」为服务对象，支持导入、导出、权限三类问题：从客户提问、版本感知检索、任务调查，到人工确认升级、技术处理、客户确认及案例审核发布。

**当前状态：可运行的本地项目，具备完整业务闭环与自动测试。默认无密钥演示；真实 LLM 与 Embedding 适配已实现，真实供应商效果尚未验证。**

## 启动

需要 Python >=3.11 与 [uv](https://docs.astral.sh/uv/)。Node 仅用于前端语法检查，不需要前端构建服务。

```powershell
cd D:\new-agent-project\resolvedesk
uv sync --frozen
uv run uvicorn app.main:app --host 127.0.0.1 --port 8123
```

打开 <http://127.0.0.1:8123>，API 文档在 <http://127.0.0.1:8123/docs>。也可以运行 `./start.ps1`。SQLite 数据保存在 `data/resolvedesk.db`，首次启动自动生成样例，无需 Docker 或独立数据库。

## 两种运行模式

| 模式 | Agent | 向量 | 用途 |
|---|---|---|---|
| `demo`（默认） | 明确标注的规则式演示 | 384 维词法特征哈希，不理解同义词 | 演示状态流转、工程回归、压测 |
| `live` | 三个角色调用真实聊天模型，调查角色可进行受限工具调用 | 真实 Embedding API | 验证真实问答与语义检索效果 |

真实模式：复制 `.env.example` 为 `.env`，配置 `RD_MODE=live`、聊天和 Embedding 的 base URL、API key、model。聊天与向量服务可分别使用不同供应商；base URL 填到 `/v1` 等 API 根路径，代码分别追加 `/chat/completions` 与 `/embeddings`。聊天服务需要支持工具调用与 JSON object 输出。

不需要修改代码。缺少配置时启动直接报错；上游失败返回明确错误，不自动回退到规则答案。模型指纹变化后，启动时重建已发布文档向量。向量服务无重试风暴，单次 HTTP 与整条链路均有限时。

**不要提交 `.env`。默认角色凭证只适合本机演示；本项目尚未实现生产级登录、账号管理和分布式限流。**

## 演示身份与数据

演示模式右上角可切换四种身份；认证由后端执行，切换控件本身不提供权限。

| 身份 | Bearer token | 范围 |
|---|---|---|
| 青禾商贸客户 | `demo-customer-a` | 自己的 v2.0 环境、任务和工单 |
| 远山书店客户 | `demo-customer-b` | 自己的 v1.0 环境、任务和工单 |
| 客服 | `demo-support` | 选择客户调查、处理工单、编辑知识草稿 |
| 技术人员 | `demo-engineer` | 处理产品问题、发布文档、审核案例 |

全部企业、任务、产品文档与故障均为自建虚构样例，非真实客户运行数据。若自定义了 token，请在“访问凭证”中填写；演示身份快捷切换使用上表的默认值。

## 十分钟业务演示

1. 客户「青禾商贸」→ 服务接待 → 点“导出超时”→ 开始调查。查看任务 `EXP-1002`、v2.0 文档引用、已知问题候选和执行轨迹。
2. 检查升级草稿 → **确认提交技术工单**。调查阶段没有自动创建单据。
3. 切换客服 → 工单工作台 → 状态选择“待技术处理”，核实后关联 `PRB-001`，填写处理说明并保存。
4. 切换技术 → 已知问题 → 填写已验证的处理方案 → 保存为已修复。关联的处理中工单转为“待客户确认”。
5. 切换客户 → 工单工作台 → 选择“已解决”，填写确认结果。若仍失败，可选择“处理中”继续调查。
6. 切换客服 → 已解决工单 → 生成案例知识草稿。
7. 切换技术 → 知识中心 → 案例审核 → 修改、核对后发布。新案例仅原客户可检索。
8. 用“检索实验”比较三种检索；用“运行记录”查看可观测事件。重新打开工单后，对应案例会撤下。

另两个入口：`IMP-1001 / E2013` 展示升级模板不兼容；`AUTH-1003 / E4030` 展示权限查询，AI 不会直接替客户授权。

## 项目结构与阅读顺序

```text
app/
  config.py       环境配置
  models.py       输入与结构化输出校验
  db.py           SQLite schema、短事务、索引快照
  seed.py         可阅读的虚构业务资料
  auth.py         后端身份、角色与客户范围
  providers.py    词法特征、真实聊天/向量适配
  knowledge.py    切分、BM25、余弦、RRF、索引发布
  agents.py       LangGraph 分诊 → 调查 → 协同
  business.py     幂等建单、状态机、知识审核
  main.py         HTTP API 与应用生命周期
web/              原生 HTML/CSS/JS 工作台、本地字体
tests/            自动化业务与模型协议测试
eval/             24 条人工构造检索回归题
scripts/          检索评测、合成语料准备、HTTP 压测
reports/          实际原始结果与验收报告
docs/             架构、核心技术、演示及面试材料
```

先读 [架构与链路](docs/architecture.md)，再读 [核心技术与面试边界](docs/core-tech.md)，最后对照源码和 [验证报告](reports/validation.md)。建议依次理解 `seed → db → knowledge → agents → business → main`。

## 验证与复现

```powershell
uv run python -m unittest discover -v
node --check web/app.js
uv run python scripts/evaluate.py
```

压测使用独立数据库，避免污染交互演示：

```powershell
uv run python scripts/seed_scale.py --documents 500
$env:RD_DB='data/benchmark.db'
uv run uvicorn app.main:app --host 127.0.0.1 --port 8124 --no-access-log
```

另开终端，在项目目录运行：

```powershell
uv run python scripts/benchmark.py --scenario search --requests 300 --concurrency 8 --output reports/search-c8.json
uv run python scripts/benchmark.py --scenario ask --requests 100 --concurrency 4 --output reports/ask-c4.json
uv run python scripts/benchmark.py --scenario write --db data/benchmark.db --requests 200 --concurrency 8 --output reports/write-c8.json
```

压测脚本分别测试检索、完整调查、建单事务；`write` 会在指定基准库写入合成的已完成运行记录。`ask` 会产生运行记录，超出 8 个同时调查返回 429。真实模式须明确加 `--allow-live`，会实际调用收费模型；鉴权凭证可由 `RD_BENCH_TOKEN` 指定。

基准服务停止后，在该终端执行 `Remove-Item Env:RD_DB`，避免下一次交互演示误用基准库。项目仅支持单个 Uvicorn worker；进程内准入与查询缓存不是分布式能力。

## 实现边界

- 原生工作台含服务接待、工单、已知问题、知识中心、检索实验、运行记录六个页面。
- 文档入库支持 Markdown/纯文本，按标题与段落切分；没有 PDF OCR、图谱、MCP 或任意代码执行。
- 向量是 NumPy 内存矩阵精确扫描，持久化在 SQLite；没有声称使用 Milvus、ANN 或百万级检索。
- 历史工单按当前客户与模块查询最近五条，属于结构化工具查询，不是完整工单语义搜索。
- LangGraph 是条件编排，无 checkpoint。工单可跨会话继续，进行中的 Agent 任务在重启后标记失败，不会从中间节点恢复。
- 引用 ID 校验能拦截不存在的来源，不能证明回答内容一定被来源支持；仍需人工评测事实一致性。
- 真实模型未使用个人密钥实测；协议测试采用 MockTransport，不能替代供应商联调和真实质量评测。

字体使用 IBM Plex（SIL OFL），授权见 `web/fonts/LICENSE.txt`。
