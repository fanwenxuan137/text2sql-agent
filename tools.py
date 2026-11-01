"""
tools.py — 给大模型用的三种「工具」
====================================
核心思想
--------
大模型本身**不能**直接读你的数据库。它只会说话。
所谓 "Function Calling（工具调用）"，本质是三方配合：

    1) 我们告诉模型："你手里有这三件工具，名字、用途、参数格式如下"
       —— 这就是 TOOLS 里那三段 JSON Schema
    2) 模型看完问题后，不直接回答，而是说："我需要调用 run_sql，参数是 {sql: '...'}"
       —— 模型只输出一个「调用意向」，它自己并不执行
    3) 我们的代码真正去执行，把结果当成一条消息再喂回给模型
       —— 模型拿到数据后才能回答用户

所以这个文件只干两件事：
    上半部分：描述工具（给模型看的说明书）
    下半部分：执行工具（真正干活，是我们自己的 Python 代码）

运行自测：
    python tools.py
"""

import json
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 数据库固定放在本文件同级目录，避免「在别的目录运行就找不到库」的坑
DB_PATH = Path(__file__).parent / "school.db"

# 单次查询最多返回多少行 —— 防止 SELECT * 把整张表灌进上下文，烧钱又答不准
MAX_ROWS = 50


# ============================================================
# 一、工具说明书：JSON Schema
# ============================================================
# 注意外层结构：{"type": "function", "function": {...}}
# 这是 OpenAI 规范的要求，DeepSeek / 通义 / 智谱都兼容这个格式。
# description 极其重要 —— 模型靠它判断「该不该用这个工具」，
# 写得含糊，模型就会瞎猜答案或者选错工具。

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": (
                "列出数据库中所有表的名称。"
                "当你不确定数据库里有哪些表时，先调用这个工具。"
            ),
            # 无参数也必须给一个空 properties，不能省掉 parameters 字段
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_table",
            "description": (
                "查看某张表的字段结构：字段名、类型、是否主键。"
                "在写 SQL 之前，如果不确定字段名（比如到底叫 score 还是 grade），"
                "必须先调用这个工具，不要凭猜测写 SQL。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "要查看的表名，例如 students",
                    }
                },
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "在 SQLite 数据库上执行一条 SELECT 查询并返回结果（最多 50 行）。"
                "只能执行查询语句，不能修改数据。"
                "写 SQL 前请先用 list_tables 和 describe_table 确认表名和字段名。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "要执行的单条 SELECT 语句，SQLite 语法",
                    }
                },
                "required": ["sql"],
            },
        },
    },
]


# ============================================================
# 二、工具实现：真正干活的部分
# ============================================================

def _connect() -> sqlite3.Connection:
    """每次调用都新开一个连接，用完即关。简单、不会出现连接残留的怪问题。"""
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"找不到数据库 {DB_PATH}，请先运行：python init_db.py"
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # 让查询结果能按列名取值
    return conn


def list_tables() -> str:
    """列出所有表名。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        names = [r["name"] for r in rows]
        return f"数据库共有 {len(names)} 张表：{', '.join(names)}"
    finally:
        conn.close()


def describe_table(table_name: str) -> str:
    """查看表结构。用 PRAGMA table_info，这是 SQLite 的专用语法。"""
    conn = _connect()
    try:
        # 先确认这张表真的存在，否则 PRAGMA 会静默返回空列表，模型会一头雾水
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            return f"错误：表 '{table_name}' 不存在。可以先用 list_tables 查看有哪些表。"

        rows = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        lines = [f"表 {table_name} 的字段结构："]
        for r in rows:
            pk = "，主键" if r["pk"] else ""
            notnull = "，非空" if r["notnull"] else ""
            lines.append(f"  - {r['name']} ({r['type']}{pk}{notnull})")

        # 顺手把外键也告诉模型，这样它才知道表之间怎么 JOIN
        fks = conn.execute(f"PRAGMA foreign_key_list('{table_name}')").fetchall()
        if fks:
            lines.append("外键关系：")
            for fk in fks:
                lines.append(
                    f"  - {fk['from']} → {fk['table']}.{fk['to']}"
                )
        return "\n".join(lines)
    finally:
        conn.close()


def run_sql(sql: str) -> str:
    """执行 SELECT，返回结果文本。"""
    # ---- 第一道安全闸：只允许 SELECT ----
    # 现在写得比较朴素，进阶部分会做得更严（多语句、注释绕过等）
    cleaned = sql.strip().rstrip(";").strip()
    lowered = cleaned.lower()
    if not lowered.startswith("select") and not lowered.startswith("with"):
        return "错误：只允许执行 SELECT 查询，该语句已被拒绝。"

    if ";" in cleaned:
        return "错误：一次只能执行一条语句，检测到多条语句，已拒绝。"

    conn = _connect()
    try:
        cursor = conn.execute(cleaned)
        rows = cursor.fetchmany(MAX_ROWS)
        columns = [d[0] for d in cursor.description] if cursor.description else []

        if not rows:
            return "查询执行成功，但没有返回任何数据（0 行）。"

        # 用「列名 | 列名」的文本表格形式返回，比 JSON 更省 token 且模型更容易读
        lines = [" | ".join(columns)]
        lines.append("-" * 40)
        for row in rows:
            lines.append(" | ".join("" if v is None else str(v) for v in row))

        result = "\n".join(lines)
        if len(rows) == MAX_ROWS:
            result += f"\n（结果已截断，只显示前 {MAX_ROWS} 行）"
        else:
            result += f"\n（共 {len(rows)} 行）"
        return result

    except sqlite3.Error as e:
        # 关键：把错误信息原样返回给模型，它才能自己改 SQL 重试（Self-Correction）
        return f"SQL 执行错误：{e}\n请检查表名、字段名和语法后重试。"
    finally:
        conn.close()


# ============================================================
# 三、分发器：模型给的是「工具名 + JSON 字符串」，这里路由到具体函数
# ============================================================

# 名字 → 函数。加新工具时这里登记一行即可
TOOL_MAP = {
    "list_tables": list_tables,
    "describe_table": describe_table,
    "run_sql": run_sql,
}


def execute_tool(name: str, arguments: str) -> str:
    """
    执行模型请求的工具调用。

    参数：
        name      : 工具名，模型给的
        arguments : 参数，**是 JSON 格式的字符串**（不是 dict！）
                    例如 '{"sql": "SELECT * FROM students"}'

    返回：
        字符串。这个字符串会作为 role="tool" 的消息内容回传给模型。
        所以不能抛异常出去 —— 报错也要以字符串形式返回，让模型知道发生了什么。
    """
    func = TOOL_MAP.get(name)
    if func is None:
        return f"错误：不存在名为 '{name}' 的工具。可用工具：{', '.join(TOOL_MAP)}"

    # 模型偶尔会给出空字符串或非法 JSON，必须兜住
    try:
        args = json.loads(arguments) if arguments.strip() else {}
    except json.JSONDecodeError as e:
        return f"错误：参数不是合法的 JSON（{e}），收到的内容是：{arguments}"

    try:
        return func(**args)
    except TypeError as e:
        # 典型原因：模型编了一个不存在的参数名，或漏了必填参数
        return f"错误：调用 {name} 时参数不匹配（{e}）"
    except Exception as e:
        return f"错误：执行 {name} 时出现异常（{type(e).__name__}: {e}）"


# ============================================================
# 四、自测：不经过大模型，直接验证三个工具本身对不对
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("工具自测（这一步过了，才说明工具本身没问题）")
    print("=" * 60)

    print("\n[1] list_tables")
    print(list_tables())

    print("\n[2] describe_table('scores')")
    print(describe_table("scores"))

    print("\n[3] describe_table('不存在的表')")
    print(describe_table("不存在的表"))

    print("\n[4] run_sql：各班平均分")
    print(run_sql("""
        SELECT c.class_name, ROUND(AVG(sc.score), 1) AS avg_score
        FROM classes c
        JOIN students s ON s.class_id = c.id
        JOIN scores sc ON sc.student_id = s.id
        GROUP BY c.id ORDER BY avg_score DESC
    """))

    print("\n[5] 安全拦截：DELETE 应被拒绝")
    print(run_sql("DELETE FROM students"))

    print("\n[6] 错误处理：故意写错字段名")
    print(run_sql("SELECT wrong_column FROM students"))

    print("\n[7] 分发器 execute_tool（模拟模型的调用格式）")
    print(execute_tool("run_sql", '{"sql": "SELECT COUNT(*) AS 学生总数 FROM students"}'))

    print("\n[8] 分发器：非法 JSON")
    print(execute_tool("run_sql", "{sql: bad json}"))

    print("\n" + "=" * 60)
    print("自测结束。全部符合预期 → 可以进入 agent_loop.py")
    print("=" * 60)
