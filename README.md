# Customeragent

本地外呼智能体与离线评测工作台。后端使用 Python、FastAPI、Pydantic 和 SQLite，前端使用 Vue 3、TypeScript 与 Vite。本地模型通过 Ollama 接入。

代码提供业务状态机、工具调用与参数校验、受约束话术选择、任务队列、日志重放、评测统计、政策知识检索，以及有限提示补丁的离线迭代。内置配送与课程两类业务流程；规则和提示词属于示例业务实现，接入其他业务时需要自行修改和审核。

## 此版本包含的内容

- `backend/customer_agent/`：当前实现，按接口、执行、业务规则、运行时、知识、工具和评测组织。
- `backend/industrial/`：旧导入路径的兼容入口。
- `backend/prompts/`：业务提示词代码。
- `frontend/src/`：工作台和兼容页面源代码。
- `scripts/`：本地数据导入和知识索引构建工具。
- `tests/`：公开版本的启动与边界测试。
- [操作指南](docs/OPERATIONS.md)：安装、启动、自备数据、模型、测试和上传步骤。

依赖清单用于安装与构建。仓库不附带数据集、原数据生成模板、实验结果、知识快照、模型权重、运行日志、数据库、环境文件、部署配置或机器专属路径。参数读取代码和通用默认值属于程序实现；运行所需的环境参数由使用者在本地提供。前端构建通过 `tools.mjs` 执行，无须提交单独的构建配置文件。

## 快速启动

需要 Python 3.13 和 Node.js 22.12 或更高版本。在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Push-Location frontend
npm ci
npm run build
Pop-Location
.\start.ps1
```

打开 <http://127.0.0.1:8001/>；API 文档位于 `/docs`。Linux/macOS 的等效步骤见操作指南。

没有导入数据时，服务仍可启动并显示空工作台：`/health` 返回存活状态，`/ready` 返回 503；评测任务会返回 `dataset_not_provided`。自行导入数据后重启服务，即可运行规则参考或本地模型评测。默认的 `rule-reference` 仅检查执行流程，不证明模型效果。

项目未接入真实电话、语音识别、语音合成或 CRM；业务工具使用本地模拟状态。结果来自使用者自行运行的任务，仓库不提供历史效果结论。
