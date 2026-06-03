# Vertical Doc Agent

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED)
![OnlyOffice](https://img.shields.io/badge/OnlyOffice-Document%20Server-orange)
![License](https://img.shields.io/badge/License-Apache%202.0-green)
![Status](https://img.shields.io/badge/status-MVP-informational)

Vertical Doc Agent 是一个面向垂直业务文档生成场景的 AI 助手原型。它围绕“解析源文档、抽取关键要求、生成结构化目录、检索业务知识素材、分章节生成正文、导出 Word 初稿并人工复核”这一类专业文档生产流程，帮助业务人员降低重复劳动，沉淀可复用的企业知识资产。

当前实现以建设工程技术标编制为首个落地场景：系统可以读取招标文件、抽取评分点、生成技术标目录、参考投标知识库生成正文、导出 Word 初稿，并通过 OnlyOffice 在线复核。后续可以扩展到方案书、申报材料、合规报告、评审材料、项目总结等其他垂直业务文档。

本项目当前定位为企业内部 MVP：适合用于流程验证、工程样例测试和后续产品化研发，不建议直接作为无人值守的正式投标交付系统。

> 安全提醒：真实业务文件、招标文件、优秀标书、企业资料、生成的 Word 初稿、API Key、数据库文件和运行产物都不应提交到 Git。公开仓库前请再次检查 `.env`、`data/`、`outputs/`、`.tmp/` 和历史提交。

## 目录

- [项目背景](#项目背景)
- [核心功能](#核心功能)
- [典型工作流](#典型工作流)
- [技术栈](#技术栈)
- [系统架构](#系统架构)
- [关键实现](#关键实现)
- [快速启动](#快速启动)
- [模型与参数配置](#模型与参数配置)
- [业务知识素材库](#业务知识素材库)
- [Word 导出与在线复核](#word-导出与在线复核)
- [目录结构](#目录结构)
- [测试与质量检查](#测试与质量检查)
- [数据安全与 Git 提交规则](#数据安全与-git-提交规则)
- [常见问题](#常见问题)
- [路线图](#路线图)
- [贡献指南](#贡献指南)
- [许可证](#许可证)

## 项目背景

垂直业务文档生成通常有几个共性痛点：

- 源文件长，关键要求、评分点、合规条款和格式要求分散在不同章节；
- 业务文档目录必须响应输入要求，不能随意发挥；
- 历史优秀文档、企业制度、法规规范和标准模板有价值，但人工复制、改写、配图和排版耗时；
- 正文、表格、图片、目录、页码、格式之间容易不一致；
- 业务经验很难沉淀成可复用的企业知识库。

本仓库首个落地样例是建设工程技术标编制，因此下文会保留“招标文件、评分点、技术标、优秀标书”等领域术语。它们可以理解为 Vertical Doc Agent 在工程投标场景下的一组具体实现。

本系统尝试把这些工作拆成可控的流水线：

```text
上传源文档
  -> 解析项目/业务信息、关键要求、评分点
  -> 生成结构化目录树
  -> 上传/同步业务知识素材库
  -> 分章节生成正文
  -> 渲染 Word 初稿
  -> OnlyOffice / 人工在线复核
  -> 下载或确认最终 Word
```

## 核心功能

### 1. 源文档解析

- 支持上传 Word / PDF 源文档；
- 自动定位招标公告、投标人须知前附表、评标办法前附表、技术标准和要求、发包人要求等关键区域；
- 按任务构建抽取输入包，避免整本长文档直接送入 LLM；
- 抽取：
  - 项目名称；
  - 建设地点；
  - 建设规模；
  - 招标范围；
  - 工期要求；
  - 质量要求；
  - 安全文明要求；
  - 技术标评分点；
  - 技术标准与要求；
- 输出前端可读的解析报告。

### 2. 结构化目录生成

- 在技术标场景下，一级目录严格使用招标文件技术评分点原文；
- 基于评分点类型、优秀标书目录经验和 LLM 目录补强生成二三级目录；
- 对不同章节类型设置目录深度和数量约束，避免三级目录失控；
- 前端支持目录树查看、展开、折叠、编辑、删除和新增子节点；
- 后续正文生成以确认后的目录树为准。

### 3. 正文分章节生成

- 将技术标目录拆分成可并发的生成单元；
- 支持按章节选择生成、一键生成全部、失败重试、跳过已生成章节；
- 按任务读取 `configs/llm-task-profiles.json` 中的并发、超时、重试、温度、max tokens 等参数；
- 支持房建技术标常见章节：
  - 主要施工方案与技术措施；
  - 质量管理体系与措施；
  - 安全管理体系与措施；
  - 文明施工与环境保护；
  - 工期保证措施；
  - 资源配置计划；
  - 施工进度计划；
  - 施工总平面布置；
  - BIM、信息化、创新、风险控制等。

### 4. 业务知识素材库

- 支持在前端上传优秀历史技术标、法规规范、企业制度、评审办法、业务模板等资料；
- 上传时要求选择项目类型，例如房建、市政、公路等；
- 上传前要求确认文件已脱敏；
- 自动解析优秀标书中的章节、表格、图片、图文块和素材元数据；
- 支持后续按章节主题、图片语义、表格类型和图文块关系召回素材；
- 当前建议只把代码上传 Git，业务原文和解析产物通过系统上传入库。

### 5. Word 初稿导出与在线复核

- 根据确认后的目录树和正文生成结果渲染 Word；
- 支持标题编号、目录页、正文另起页、一级标题另起页、页码、表格、图片题注等格式；
- 支持前端配置 Word 导出格式；
- 集成 OnlyOffice Document Server，实现在线预览和人工编辑；
- 支持版本文件：
  - `system_generated.docx`：系统生成版；
  - `review_editing.docx`：OnlyOffice 人工编辑保存版；
  - `final_export.docx`：最终确认版。

### 6. 模型配置与任务参数

- 前端可配置：
  - `API_KEY`；
  - `BASE_URL`；
  - `MODEL`；
  - `API_TYPE`；
  - `TEMPERATURE`；
  - `TOP_P`；
  - `MAX_TOKENS`；
  - `TIMEOUT_SECONDS`；
  - `MAX_RETRIES`；
  - `MAX_WORKERS`；
  - 是否使用结构化输出；
  - 是否开启思考模式；
- 不同任务可配置不同参数，例如招标解析、目录补强、正文生成分别设置并发和超时。

## 典型工作流

### 编标人员视角

1. 新建项目。
2. 上传招标文件。
3. 点击启动招标文件解析。
4. 查看解析报告，确认项目信息、技术要求和技术评分点。
5. 生成技术标目录。
6. 在目录树中人工调整不合理章节。
7. 上传或同步业务知识素材库。
8. 选择章节生成正文，或批量生成全部正文。
9. 进入 Word 初稿页面，按格式导出 Word。
10. 在 OnlyOffice 中在线复核、更新目录、保存修改。
11. 下载 Word 初稿或确认最终成稿。

### 系统处理视角

```text
招标文件上传
  -> 文档解析与关键区域候选定位
  -> 三类抽取输入包
     - 项目信息包
     - 评分点包
     - 技术要求包
  -> LLM 结构化抽取
  -> 解析报告
  -> 评分点原文锁定为一级目录
  -> 规则骨架 + LLM 二三级目录补强
  -> 正文生成输入包
  -> 优秀标书素材召回
  -> 分章节 LLM 生成正文
  -> 系统匹配图片/表格/图文块
  -> Word 渲染
  -> OnlyOffice 复核与保存
```

## 技术栈

| 分类 | 技术 |
| --- | --- |
| 后端语言 | Python 3.11+ |
| Web 框架 | FastAPI, Uvicorn |
| 文档解析 | python-docx, pdfplumber, Pillow |
| 文档生成 | python-docx |
| 数据库 | PostgreSQL 16 |
| 缓存/任务预留 | Redis 7 |
| 在线文档预览 | OnlyOffice Document Server |
| 前端 | 原生 HTML / CSS / JavaScript |
| LLM 接入 | OpenAI 兼容接口，支持 Responses API / Chat Completions |
| 反向代理 | Nginx |
| 部署 | Docker Compose |
| 测试 | pytest, node --check |

## 系统架构

```text
+------------------------------+
|            Browser           |
| 项目管理 / 解析 / 目录 / 正文 / Word |
+---------------+--------------+
                |
                v
+------------------------------+
|             Nginx            |
| /            -> Backend Web  |
| /api         -> FastAPI      |
| /onlyoffice  -> OnlyOffice   |
+-------+----------------+-----+
        |                |
        v                v
+---------------+   +----------------------+
| FastAPI       |   | OnlyOffice           |
| Backend       |   | Document Server      |
+-------+-------+   +----------+-----------+
        |                      |
        v                      v
+---------------+   +----------------------+
| PostgreSQL    |   | Word 在线编辑与回调   |
| 项目/任务元数据 |   +----------------------+
+-------+-------+
        |
        v
+---------------+       +------------------+
| Docker Volume |       | LLM Provider     |
| 上传/素材/产物 | <---> | DeepSeek/Qwen 等 |
+---------------+       +------------------+
```

## 关键实现

### 招标解析：关键区域候选定位

系统不会把整本招标文件原样送入模型，而是先做结构化解析和候选区域定位：

- 对 Word / PDF 提取文本块、表格、页码、章节标题；
- 根据关键词和章节结构定位评分办法、投标人须知、技术要求等候选区域；
- 针对不同抽取任务构建不同输入包；
- 对超长区域进行去重、合并和 token 预警；
- LLM 只处理与任务相关的内容。

### 评分点：原文锁定

技术标一级目录必须使用招标文件中的技术评分点原文。目录生成时：

- 不改写一级目录；
- 不合并一级评分点；
- 不遗漏一级评分点；
- 二三级目录才允许结合优秀标书经验进行扩展；
- 目录生成后做数量、层级、空标题等质量检查。

### 正文生成：分章节调度

正文生成不是一次性生成整本标书，而是按目录拆成多个生成单元：

- 便于并发生成；
- 便于失败重试；
- 便于只重跑某些章节；
- 便于缓存复用；
- 便于后续做人工复核和局部改写。

### 素材复用：业务知识素材库

业务知识素材入库会提取：

- 章节层级；
- 正文片段；
- 表格；
- 图片；
- 图片题注；
- 表格行级语义；
- 图文块；
- 素材来源和项目类型。

正文生成时使用素材库作为参考，不建议直接把业务原文全文塞入 LLM。对于图片，系统会尽量基于章节主题、图片题注、图文块语义和去重规则匹配，降低图不对版概率。

### Word 渲染：内容与格式解耦

LLM 负责生成结构化内容，Word 渲染器负责格式：

- 标题样式；
- 编号；
- 目录字段；
- 段落行距；
- 表格列宽；
- 图片大小；
- 图片题注；
- 页眉页脚；
- 版本文件。

这样可以避免每次大模型输出格式都不一致。

## 快速启动

### 1. 前置条件

推荐使用 Docker Compose 启动完整系统。

需要安装：

- Git；
- Docker Desktop 或 Docker Engine；
- Docker Compose v2；
- 一个 OpenAI 兼容的大模型 API Key。

可选：

- Python 3.11+，用于本地开发和运行测试；
- Node.js，用于前端 JS 语法检查。

### 2. 克隆仓库

```bash
git clone https://github.com/anan-root/vertical-doc-agent.git
cd vertical-doc-agent
```

如果仓库仍是私有仓库，需要你的 GitHub 账号拥有访问权限。

### 3. 准备环境变量

Windows PowerShell：

```powershell
Copy-Item .env.docker.example .env.docker
```

Linux / macOS：

```bash
cp .env.docker.example .env.docker
```

编辑 `.env.docker`：

```env
API_KEY=your-api-key
BASE_URL=https://api.deepseek.com
MODEL=deepseek-v4-flash
LLM_PROVIDER=deepseek
API_TYPE=chat_completions

TEMPERATURE=0
TOP_P=1
MAX_TOKENS=
TIMEOUT_SECONDS=180
MAX_RETRIES=2
MAX_WORKERS=8

POSTGRES_DB=construction_bidding_agent
POSTGRES_USER=postgres
POSTGRES_PASSWORD=please-change-this

APP_SECRET_KEY=please-change-this
APP_PUBLIC_URL=http://localhost

ONLYOFFICE_PUBLIC_URL=http://localhost/onlyoffice
ONLYOFFICE_JWT_ENABLED=false
ONLYOFFICE_JWT_SECRET=
```

说明：

- `API_KEY` 不要提交到 Git；
- 私有部署时应修改 `POSTGRES_PASSWORD` 和 `APP_SECRET_KEY`；
- 如果部署到服务器，把 `APP_PUBLIC_URL` 和 `ONLYOFFICE_PUBLIC_URL` 改成服务器 IP 或域名；
- DashScope、DeepSeek、其他 OpenAI 兼容平台可通过 `BASE_URL`、`MODEL`、`API_TYPE` 切换。

### 4. 首次启动

```bash
docker compose --env-file .env.docker up -d postgres redis
docker compose --env-file .env.docker --profile migrate run --rm migrate
docker compose --env-file .env.docker up -d --build
```

访问：

```text
http://localhost
```

### 5. 日常命令

查看服务状态：

```bash
docker compose --env-file .env.docker ps
```

查看后端日志：

```bash
docker compose --env-file .env.docker logs -f backend
```

重启后端：

```bash
docker compose --env-file .env.docker restart backend
```

重新构建并启动：

```bash
docker compose --env-file .env.docker up -d --build
```

停止服务但保留数据卷：

```bash
docker compose --env-file .env.docker down
```

停止并删除数据卷，谨慎使用：

```bash
docker compose --env-file .env.docker down -v
```

### 6. 本地开发启动

如果不使用 Docker，需要自行准备 PostgreSQL。

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e .
python scripts/manage_db.py --apply
uvicorn construction_bidding_agent.backend.app:app --reload --host 0.0.0.0 --port 8000
```

访问：

```text
http://localhost:8000
```

## 模型与参数配置

全局配置来自 `.env` 或 `.env.docker`，任务级配置来自：

```text
configs/llm-task-profiles.json
```

主要任务包括：

- `project_info_extraction_input`：项目信息抽取；
- `score_points_extraction_input`：评分点抽取；
- `technical_requirements_extraction_input`：技术要求抽取；
- `outline_refinement`：二三级目录补强；
- `technical_bid_chapter_generation`：正文分章节生成。

建议：

- 招标解析保持低温度，优先稳定；
- 目录补强可使用略高温度，但必须受规则校验；
- 正文生成可适当提高温度，但仍应使用结构化输出；
- 并发数 `MAX_WORKERS` 不宜盲目调高，应结合模型限流、单章输入包大小和失败率调整。

## 业务知识素材库

业务知识素材不随代码仓库发布。当前技术标场景下，素材主要包括优秀标书、法规规范、企业制度、评审办法和投标模板等。初始化一个新环境时，建议按以下方式准备素材库：

1. 启动系统；
2. 打开“投标知识库”；
3. 点击上传参考资料；
4. 选择项目类型，例如房建；
5. 勾选脱敏确认；
6. 上传 Word / PDF 业务资料；
7. 等待系统解析入库；
8. 在素材库页面检查章节、表格、图片和素材数量。

当前策略更适合高质量 Word 资料。PDF 或 PDF 转 Word 的资料可解析，但图文关系、题注和章节归属更容易失真，建议单独治理后再正式入库。

## Word 导出与在线复核

Word 初稿页的目标不是简单下载文件，而是成稿工作台：

- 查看 Word 是否已生成；
- 调整导出格式；
- 重新导出 Word；
- 使用 OnlyOffice 在线预览；
- 更新目录页码；
- 人工编辑并保存；
- 下载最新版本。

OnlyOffice 保存逻辑：

```text
system_generated.docx
  系统根据目录和正文生成的 Word 初稿。

review_editing.docx
  用户在 OnlyOffice 中编辑并保存后的版本。

final_export.docx
  用户确认后的最终成稿版本。
```

下载优先级通常为：

```text
review_editing.docx > system_generated.docx
```

## 目录结构

```text
vertical-doc-agent/
├── configs/
│   └── llm-task-profiles.json          # LLM 任务级参数
├── deploy/
│   └── nginx/default.conf              # Nginx 反向代理配置
├── docs/                               # 产品、架构、模块、部署、质量文档
├── migrations/                         # PostgreSQL 迁移脚本
├── scripts/                            # 数据库管理和辅助脚本
├── src/construction_bidding_agent/
│   ├── backend/                        # FastAPI、数据库、API、任务编排
│   ├── document_parser/                # 招标文件和优秀标书解析
│   ├── outline_generator/              # 技术标目录生成
│   ├── chapter_generator/              # 正文生成、素材召回、Word 渲染
│   ├── llm_client.py                   # LLM 调用封装
│   └── llm_config.py                   # LLM 配置解析
├── tests/                              # 单元测试
├── web/                                # MVP 前端
├── .env.example                        # 本地环境变量示例
├── .env.docker.example                 # Docker 环境变量示例
├── docker-compose.yml                  # Docker Compose 编排
├── Dockerfile.backend                  # 后端镜像
├── LICENSE                             # 许可证
├── pyproject.toml                      # Python 项目配置
└── README.md                           # 项目说明
```

## 测试与质量检查

运行单元测试：

```bash
python -m pytest -q
```

检查前端 JS 语法：

```bash
node --check web/app.js
```

提交前建议检查：

```bash
git status --short
git diff --check
git diff --cached --name-only
```

确认未提交：

- `.env`；
- `.env.docker`；
- `data/`；
- `outputs/`；
- `.tmp/`；
- `.docx`、`.pdf`、`.xlsx`、压缩包；
- 真实 API Key；
- 未脱敏招标文件和优秀标书。

## 数据安全与 Git 提交规则

`.gitignore` 已默认忽略：

- 本地环境变量；
- Python / Node 缓存；
- 运行数据；
- 上传文件；
- 生成结果；
- Word / PDF / Excel / PPT / 压缩包；
- 数据库文件；
- 日志文件；
- 编辑器缓存。

首次公开仓库前，建议额外执行：

```bash
git status --short
git ls-files
```

如果历史提交里曾经误提交过大文件或密钥，需要先清理 Git 历史，再公开仓库。

## 常见问题

### 1. 为什么不上传优秀标书原文？

优秀标书通常包含企业经验、工程信息、图片、版式和潜在敏感内容。即使当前文件已脱敏，也建议通过系统上传入库，而不是直接放进 Git 仓库。

### 2. 没有优秀标书，别人能复现吗？

可以运行系统和测试，但无法复现你的企业素材效果。新用户应通过“优秀标书库”上传自己的优秀标书，系统会解析生成本地素材库。

### 3. 为什么使用 PostgreSQL？

项目后续会面向多人使用，项目、文件、任务、素材库、Word 版本都需要稳定的关系型数据管理。SQLite 更适合单机原型，不适合作为长期多人协作方案。

### 4. 为什么需要 OnlyOffice？

Python 生成 Word 很难可靠计算最终页码，目录页码、分页、图片跨页等需要文档排版引擎。OnlyOffice 用于在线预览、编辑、刷新目录和保存人工复核版本。

### 5. 为什么正文生成耗时较长？

正文生成是分章节调用 LLM，并且每章会携带评分点、目录路径、项目上下文、素材摘要、表格参考和图片/图文块候选。质量越高，输入包越重，耗时也会增加。项目中已支持并发、失败重试、输入包瘦身和缓存复用方向的优化。

### 6. 模型可以切换吗？

可以。只要模型平台兼容 OpenAI 风格接口，可通过 `.env.docker` 或前端模型配置页面调整：

```env
API_KEY=
BASE_URL=
MODEL=
API_TYPE=responses 或 chat_completions
```

不同平台对结构化输出、Responses API、Chat Completions、思考模式的支持不同，需要按平台实际能力调整。

## 路线图

### MVP 已覆盖

- 招标文件上传；
- 招标解析报告；
- 技术标评分点抽取；
- 技术标目录生成；
- 优秀标书上传入库；
- 分章节正文生成；
- Word 初稿导出；
- OnlyOffice 在线复核；
- Docker Compose 一键部署。

### 近期优化方向

- 更稳定的图片语义匹配和图文块复用；
- 正文生成耗时优化；
- 目录与正文结构一致性质量闸门；
- Word 目录页码自动刷新体验优化；
- 前端复核页面进一步简化；
- 素材库质量报告和人工确认流程。

### 中长期方向

- 多人协作与权限角色；
- 企业固定资料库；
- 多专业/多项目类型扩展；
- 更完善的异步任务队列；
- 对象存储；
- 版本对比和审阅流转；
- 私有化部署脚本和运维监控。

## 贡献指南

欢迎围绕以下方向提交 Issue 或 Pull Request：

- 招标文件解析准确率；
- 评分点抽取和质检；
- 目录生成约束；
- 优秀标书素材入库；
- 图文块复用质量；
- Word 导出版式；
- OnlyOffice 在线复核体验；
- Docker 部署和运维稳定性。

建议流程：

1. 新建分支；
2. 修改代码或文档；
3. 运行必要测试；
4. 确认没有提交敏感文件；
5. 提交 PR，并说明改动目的、测试结果和影响范围。

## 许可证

本项目代码采用 [Apache License 2.0](LICENSE)。

Apache-2.0 是宽松开源许可证，允许使用、修改、分发和商用，但需要保留版权声明和许可证文本，并包含免责声明和专利授权条款。

请注意：许可证仅覆盖本仓库中的代码和文档，不覆盖用户上传的招标文件、投标文件、优秀标书、企业资料、生成的 Word 成稿、数据库内容、模型 API Key 或其他运行数据。这些业务数据应继续保持脱敏和私有化管理。
