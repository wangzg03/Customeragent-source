# 操作指南

所有命令从仓库根目录执行，除非命令中明确切换目录。以下仅使用仓库相对位置；个人数据、运行参数和凭据由使用者在本地提供。

## 1. 安装和启动

需要 Python 3.13、Node.js 22.12+、npm。基础启动不需要 Ollama 或 Docker。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Push-Location frontend
npm ci
npm run build
Pop-Location
.\start.ps1
```

Linux/macOS：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
(cd frontend && npm ci && npm run build)
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8001 --workers 1
```

工作台：<http://127.0.0.1:8001/>。接口说明：<http://127.0.0.1:8001/docs>。每个运行目录只启动一个 API worker。Windows 可以用 `start.ps1 -Port 8011` 更改端口；前端开发代理默认连接 8001。

源码安装不会自带任何用例。数据为空时，工作台显示空列表，`/health` 返回 200、`/ready` 返回 503，创建评测任务返回 503 和 `dataset_not_provided`。这表示尚未准备评测输入。前端首次运行需先完成构建。

## 2. 自行提供评测输入

使用自己准备并已确认可以使用的 UTF-8 JSONL，每行一个对象。仓库不提供样本记录或原评测集生成器。字段约束以 `backend/customer_agent/core/schemas.py` 中的 `Case`、适配逻辑与校验函数为准。

| 字段 | 要求 |
| --- | --- |
| `id` | 1–100 位小写字母、数字、下划线或连字符；全局唯一 |
| `group_id` | 分组标识，同组用例不得跨划分 |
| `scenario` | `delivery` 或 `course` |
| `intent` | 与本地业务和用户模拟器匹配的意图，分类见 `domain/policy.py` |
| `personality` | 分类见 `domain/policy.py` 的 `PERSONAS` |
| `split` | `train`、`dev` 或 `test` |
| `opening` | 1–2000 字符的用户开场文本 |
| `facts` | 包含正整数 `single_orders`、`daily_orders`、`days`、`reward_days`、`reward`、`deadline`；`contract` 使用 `single`/`multi`，`publish` 使用 `web`/`third_party` |
| `expected` | 包含 `terminal`；正常流程为 JSON 的 `null`，停止分支的取值见 `evaluation/dataset.py` |
| `provenance` | 自行填写数据来源及使用说明；不应放入个人信息或凭据 |

PowerShell 可交互输入源文件位置，避免把个人路径写进脚本：

```powershell
$inputFile = Read-Host '输入本地 JSONL 文件位置'
.\.venv\Scripts\python.exe scripts/import_data.py --source $inputFile
.\.venv\Scripts\python.exe -m backend.customer_agent.interfaces.cli validate-data
```

工具进行字段适配、常见联系信息脱敏和结构校验，将数据写入本地 `data/cases.jsonl`。它不会覆盖已有数据。内置脱敏仅处理常见模式，使用者仍需检查文本。验证通过后重启 API；元信息由当前用例计算，不依赖随仓库分发的清单。

默认使用仓库下的 `data/` 和 `runtime/`。需要使用其他位置时，通过当前进程的 `EVAL_DATA_DIR`、`EVAL_RUNTIME_DIR` 指定；不要把个人位置提交到源码。

## 3. 运行评测

首次运行建议选“运行任务”，模型选 `rule-reference`、知识来源选 `static`、沙盒选 `process`，并选择本地存在的数据划分。命令行也可以运行：

```powershell
.\.venv\Scripts\python.exe -m backend.customer_agent.interfaces.cli run --model rule-reference --knowledge-mode static --sandbox process --limit 2 --output runtime/first-run
```

输出目录需要是新目录。任务生成的记录、统计和数据库均留在本地，并由 `.gitignore` 排除。

需要实际模型时，先安装并启动 Ollama，再自行下载受支持的模型。支持列表见 `JobRequest`，工作台仅显示服务检测到的可用模型。示例启动模型：

```powershell
ollama pull qwen3.5:9b
```

模型能力和运行效果需要自行评测；规则参考运行不会调用模型。进程沙盒适用于本地开发。代码支持 Docker 模式，使用者需另行准备兼容镜像，并通过 `SANDBOX_IMAGE` 提供；此版本不分发部署文件。镜像必须能够从标准输入接收工具请求，并启动 `backend/industrial/sandbox_worker.py`，其依赖的后端代码也需要包含在镜像中。

## 4. 可选政策知识检索

先提供自行审核的政策文档 JSONL，再构建索引。文档字段必须符合 `knowledge/evidence.py` 中的 `document_error`：包括标识、规则、场景、标题、正文、检索别名、来源、版本、审批状态、审核者、租户、有效期、业务分支、数值绑定及许可信息。

`rule_id` 的允许值见 `QUERIES`；正文可绑定的账户字段见 `ACCOUNT_KEYS`。已审批文档的数字约束和必需占位符由校验器检查。业务参数与约束需要匹配当前示例业务代码。默认运行使用 `demo` 租户；使用其他租户时需要相应调整接入代码。

```powershell
ollama pull bge-m3:latest
$policyFile = Read-Host '输入本地政策文档 JSONL 位置'
.\.venv\Scripts\python.exe scripts/prepare_knowledge.py --source $policyFile
```

索引、活动指针、向量缓存仅在本地创建。工作台创建 RAG 任务时会冻结知识快照与查询日期；未构建知识库时请选择 `static`。仓库不附带政策语料、检索评测集或索引快照。

## 5. 本地运行参数与访问控制

运行参数从进程环境读取，不需要环境文件。可选变量名称见 `core/settings.py`：`EVAL_DATA_DIR`、`EVAL_RUNTIME_DIR`、`OLLAMA_URL`、`EVAL_API_KEY`、`EVAL_ENV`、`SANDBOX_MODE` 和 `SANDBOX_IMAGE`。

本地访问密钥由使用者生成并通过 `EVAL_API_KEY` 注入。启用后，在工作台的密钥输入框填入当前密钥；API 请求通过 `X-API-Key` 传递。不要把密钥粘贴到代码、README 或提交记录。浏览器输入值仅保留于当前页面内存。

`production` 模式要求至少 24 字符的访问密钥和 Docker 沙盒。该校验不代表真实外呼系统已经具备生产上线条件；本项目的外部业务动作仍为模拟实现。

## 6. 开发与检查

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
Push-Location frontend
npm run check
npm run build
# 需要前端开发服务器时另开终端执行 npm run dev
Pop-Location
```

测试使用临时目录检查空数据启动、访问控制、任务输入边界和运行目录参数；不依赖原项目的实验文件或数据集。前端 `tools.mjs` 负责类型检查、构建和本地开发代理。依赖版本由清单与锁文件管理。

## 7. 上传 GitHub

在整理后的独立目录创建全新仓库，使用空远程仓库的地址：

```powershell
git init
git add .
git status --short
git diff --cached --stat
```

确认暂存区只有代码、依赖清单、README、操作指南和 `.gitignore`，再继续：

```powershell
git commit -m 'Initial public source release'
git branch -M main
$remoteUrl = Read-Host '输入空 GitHub 仓库地址'
git remote add origin $remoteUrl
git push -u origin main
```

请从此独立目录初始化仓库；原仓库历史不属于公开交付内容。`.gitignore` 已排除数据、结果、缓存、环境文件、凭据和常见配置文件，依赖清单与前端 HTML 入口除外。不要强制添加被忽略的本地文件。新增源码中的硬编码值仍需在提交前自行检查。
