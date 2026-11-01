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
| `tools.py` | 工具定义：JSON Schema + 执行函数（待完成） |
| `agent_loop.py` | Agent Loop 核心逻辑 + System Prompt（待完成） |
| `test_queries.py` | 5 个测试问题的运行脚本（待完成） |

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
