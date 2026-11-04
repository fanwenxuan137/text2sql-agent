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
import re
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


def _connect_readonly() -> sqlite3.Connection:
    """
    以「只读模式」打开数据库，并挂上授权回调。

    mode=ro：SQLite 层面直接拒绝写入，即使 SQL 绕过了前面的检查也改不了数据。
    set_authorizer：引擎每次要执行某类操作时先问这个函数，返回 DENY 就直接失败。
    """
    # URI 形式才能带参数；as_posix() 把反斜杠换成斜杠，避免 Windows 路径解析出错
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.set_authorizer(_readonly_authorizer)
    return conn


# 只允许这几类操作通过：查询、读表、调用函数、递归查询
_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,     # SELECT 语句
    sqlite3.SQLITE_READ,       # 读某个表/字段
    sqlite3.SQLITE_FUNCTION,   # 调用函数，如 COUNT / AVG / ROUND
    sqlite3.SQLITE_RECURSIVE,  # WITH RECURSIVE
}


def _readonly_authorizer(action, arg1, arg2, db_name, trigger_name):
    """
    SQLite 授权回调：每个操作执行前都会被调用一次。
    返回 SQLITE_OK 放行，SQLITE_DENY 拒绝（拒绝后 sqlite3 会抛 DatabaseError）。
    """
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


# ---- 第 1 层：SQL 白名单校验 ----

# 只要出现这些关键字就直接拒绝（INSERT/UPDATE/DELETE/DDL 等写操作与结构变更）
_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "replace",
    "drop", "alter", "create", "truncate",
    "attach", "detach", "pragma", "vacuum", "reindex",
    "trigger", "begin", "commit", "rollback",
)

_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)      # /* ... */
_RE_LINE_COMMENT = re.compile(r"--[^\n]*")              # -- ...
_RE_SINGLE_QUOTE = re.compile(r"'(?:[^']|'')*'")        # '字符串'
_RE_DOUBLE_QUOTE = re.compile(r'"(?:[^"]|"")*"')        # "标识符"


def _strip_noise(sql: str) -> str:
    """
    去掉注释和字符串字面量，再做关键字检查。

    为什么必须去掉字符串？否则 `WHERE subject = 'drop'` 会被误判成危险语句。
    （这个库的科目名里没有这类词，但真实业务数据里很常见）
    """
    s = _RE_BLOCK_COMMENT.sub(" ", sql)
    s = _RE_LINE_COMMENT.sub(" ", s)
    s = _RE_SINGLE_QUOTE.sub("''", s)
    s = _RE_DOUBLE_QUOTE.sub('""', s)
    return s


def validate_sql(sql: str) -> str | None:
    """
    检查 SQL 是否安全。安全返回 None，不安全返回「给模型看的错误说明」。

    返回错误说明而不是抛异常，是因为模型要读懂原因才知道怎么改。
    """
    if not sql or not sql.strip():
        return "错误：SQL 为空。"

    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        return "错误：SQL 为空。"

    # 去噪后再判断，防止注释和字符串干扰
    probe = _strip_noise(cleaned).strip()

    # 1) 必须以 SELECT 或 WITH（CTE）开头
    lowered = probe.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return "错误：只允许执行 SELECT 查询，该语句已被拒绝。"

    # 2) 只允许单条语句（去噪后还剩分号，说明想拼接多条）
    if ";" in probe:
        return "错误：一次只能执行一条语句，检测到多条语句，已拒绝。"

    # 3) 关键字黑名单，\b 保证是完整单词（避免 selected 误伤 select）
    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", lowered):
            return f"错误：语句中包含被禁止的关键字 {kw.upper()}，只允许纯查询操作。"

    return None


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
    """
    执行 SELECT，返回结果文本。

    安全防护一共三层，任何一层拦下都会把原因返回给模型（而不是直接崩）：
        第 1 层 validate_sql     —— 语法白名单，拦明显违规 + 给出人能看懂的理由
        第 2 层 authorizer       —— SQLite 引擎级，只放行读操作
        第 3 层 mode=ro 只读连接 —— 物理上写不进去，最坏情况也改不了数据

    为什么需要三层？因为只靠字符串判断容易被绕过（注释、拼接、大小写混写），
    只靠引擎限制又给不出友好提示，模型不知道该改什么。
    先给提示、再上硬限制、最后物理兜底。
    """
    # ---- 第 1 层：语法白名单 ----
    problem = validate_sql(sql)
    if problem:
        return problem

    # ---- 第 2、3 层：只读连接 + 授权回调 ----
    conn = _connect_readonly()
    try:
        cleaned = sql.strip().rstrip(";").strip()
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

    # ---- 进阶：安全防护的四类绕过尝试 ----
    print("\n" + "-" * 60)
    print("安全防护测试（四类典型绕过手段）")
    print("-" * 60)

    print("\n[9] 多语句拼接：SELECT 后面藏 DROP")
    print(run_sql("SELECT 1; DROP TABLE students"))

    print("\n[10] 注释绕过：前面加行注释")
    print(run_sql("-- 无害的注释\nDROP TABLE students"))

    print("\n[11] 块注释绕过 + 大小写混写")
    print(run_sql("/*x*/ DeLeTe FROM students"))

    print("\n[12] PRAGMA 尝试（会修改数据库设置的语句）")
    print(run_sql("PRAGMA writable_schema=ON"))

    print("\n[13] 反向验证：字符串字面量里的 'drop' 不应被误伤")
    print(run_sql("SELECT 'drop' AS 词, COUNT(*) AS 人数 FROM students"))

    print("\n[14] 反向验证：正常 JOIN / 聚合 / 子查询仍然可用")
    print(run_sql("""
        SELECT name FROM students
        WHERE id IN (SELECT student_id FROM scores WHERE score >= 95)
        LIMIT 3
    """))

    print("\n[15] 确认数据没被改动（学生数应仍为 30）")
    print(run_sql("SELECT COUNT(*) AS 学生数 FROM students"))

    print("\n[16] 验证第 2、3 层：绕过第 1 层直接对只读连接执行 UPDATE")
    conn = _connect_readonly()
    try:
        conn.execute("UPDATE students SET name='hacked' WHERE id=1")
        print("没被拦住（异常！安全防护失效）")
    except sqlite3.Error as e:
        print(f"被引擎拦下：{type(e).__name__}: {e}")
    finally:
        conn.close()

    print("\n" + "=" * 60)
    print("自测结束。全部符合预期 → 可以进入 agent_loop.py")
    print("=" * 60)
