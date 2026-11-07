# Text-to-SQL Agent

用自然语言查询 SQLite 学校数据库的 AI Agent：接收中文问题 → 自主探索表结构 → 生成并执行 SQL → 用中文回答。

## 快速开始

```bash
# 1. 装依赖
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. 配 Key（复制模板后填入自己的 Key）
copy .env.example .env

# 3. 生成数据库
python init_db.py

# 4. 验证 API 是否通
python hello_api.py
```

## 文件说明

| 文件 | 说明 |
|---|---|
| `init_db.py` | 数据库初始化脚本（题目提供，保持原样不改） |
| `hello_api.py` | 步骤 1：验证大模型 API 连通性 |
| `tools.py` | 工具定义：3 个工具的 JSON Schema + 执行函数 + 分发器，内置 16 项自测（8 基础 + 8 安全） |
| `agent_loop.py` | Agent Loop 核心逻辑 + System Prompt，支持命令行单问与交互模式 |
| `test_queries.py` | 固化题目指定的 5 个问题，自动记录轮数、耗时与最终答案，输出到 `test_output.txt` |
| `self_correction_demo.py` | 自我纠错演示：第一轮注入错误字段名，观察模型能否读报错并自行修正（日志见 `self_correction_log.txt`） |
| `quiz.py` | 核心概念自测题（8 选择 + 2 简答），用于检查是否真正理解协议 |
| `run_*.bat` | 双击启动器（`hello` / `initdb` / `tools` / `agent` / `quiz`），自动定位虚拟环境，避免双击 .py 闪退 |

## 运行方式

```bash
# 工具自测（不消耗 API 额度）
.\.venv\Scripts\python.exe tools.py

# 单次提问
.\.venv\Scripts\python.exe agent_loop.py "张老师带的班里，编程成绩前三名是谁？"

# 交互模式（可连续追问，保留上下文）
.\.venv\Scripts\python.exe agent_loop.py

# 跑固定的 5 个测试问题（约 30 秒，记录到 test_output.txt）
.\.venv\Scripts\python.exe test_queries.py

# 自我纠错演示
.\.venv\Scripts\python.exe self_correction_demo.py
```

> 注意：Windows 下请在项目根目录执行，且统一用 `.venv\Scripts\python.exe`，
> 不要用系统 `python`（`openai` 装在虚拟环境里）。

## 进阶实现

### SQL 安全防护（三层）

模型生成的 SQL 不可直接信任，`tools.py` 用三层递进的防护，每层拦截后都把原因作为字符串返回给模型（而不是抛异常），保证 Agent 仍能自我修正：

| 层 | 手段 | 拦什么 |
|---|---|---|
| 1 | `validate_sql()` 语法白名单 | 非 SELECT / WITH 开头、多语句拼接、危险关键字（DROP / DELETE / PRAGMA …） |
| 2 | `set_authorizer()` 引擎授权回调 | 只放行 SELECT / READ / FUNCTION / RECURSIVE |
| 3 | `mode=ro` 只读连接 | 物理层面写不进去，最坏情况也改不了数据 |

校验前先用正则剥离注释（`--`、`/* */`）与字符串字面量，避免 `WHERE subject='drop'` 这类正常查询被误判。

### 自我纠错（Self-Correction）

`self_correction_demo.py` 在第一轮 `run_sql` 时把模型生成的 SQL 中 `score` 替换为不存在的 `score_value`，制造一次真实报错。模型读到 `no such column` 后，先用 `SELECT * FROM scores LIMIT 3` 回查真实字段，再改写 SQL 重试成功——全过程无人工干预。

实现上用 monkey patch 临时替换 `agent_loop.execute_tool`，演示代码不污染生产逻辑。

## 数据库结构

```
teachers ─1:N─▶ classes ─1:N─▶ students ─1:N─▶ scores
```

- 6 位教师 / 5 个班级 / 30 名学生 / 148 条成绩 / 5 门科目
- 数据内埋的边界情况：张老师带两个班、陈老师与赵老师不带班、王明缺编程、卫五缺历史、五班仅 4 人

## 参考来源

- OpenAI Function Calling 文档：https://platform.openai.com/docs/guides/function-calling
- DeepSeek API 文档：https://api-docs.deepseek.com
- （后续实现中若参考了其他开源项目，在此处补充链接）
