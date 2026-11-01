"""
init_db.py — 初始化 SQLite 测试数据（多表版）
===============================================
功能：
  创建一个包含 4 张关联表的 SQLite 数据库（school.db）

  表结构（ER 关系）：
    teachers ──1:N──▶ classes ──1:N──▶ students
                                          │
                                        1:N
                                          ▼
                                        scores

  - teachers : 教师信息
  - classes  : 班级信息，每个班有一个班主任（teacher_id → teachers.id）
  - students : 学生信息，每个学生属于一个班（class_id → classes.id）
  - scores   : 成绩记录，每条记录 = 某学生某科目一次成绩
               （student_id → students.id）

目的：
  - 为 Text-to-SQL Agent 提供**多表关联**的测试数据
  - 考察 Agent 的 JOIN 能力、schema 探索能力
  - 刻意制造 edge case（缺考、同名、一人带多班等）

数据规模：
  - 6 位教师，5 个班级，30 名学生，约 140 条成绩
  - 5 门科目：数学、英语、物理、历史、编程

运行方式：
  python init_db.py

运行后会在当前目录生成 school.db 文件。

★★ 必须掌握：理解多表关系型数据是怎么建的
"""

import sqlite3
import os
import sys

# ---- Windows 控制台 UTF-8 输出修复 ----
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# ============================================================
# 数据定义
# ============================================================

# 6 位教师
TEACHERS = [
    (1, "王老师"),
    (2, "李老师"),
    (3, "张老师"),
    (4, "刘老师"),
    (5, "陈老师"),
    (6, "赵老师"),
]

# 5 个班级（teacher_id 指向班主任）
# 注意：张老师（id=3）同时带两个班 → edge case：一人带多班
CLASSES = [
    (1, "一班", 1),   # 王老师
    (2, "二班", 2),   # 李老师
    (3, "三班", 3),   # 张老师
    (4, "四班", 3),   # 张老师（带两个班）
    (5, "五班", 4),   # 刘老师
]
# 陈老师（id=5）和赵老师（id=6）不带班 → edge case：有教师但不是班主任

# 30 名学生（class_id 指向班级）
# 注意：五班只有 4 人（其他班 6-7 人）→ edge case：班级人数不均
# 注意：一班有两个"王"姓同学 → edge case：同姓
STUDENTS = [
    # 一班（7 人）
    (1,  "张三",  1),
    (2,  "李四",  1),
    (3,  "王五",  1),
    (4,  "赵六",  1),
    (5,  "孙七",  1),
    (6,  "钱八",  1),
    (7,  "王明",  1),   # 同姓"王"
    # 二班（7 人）
    (8,  "周九",  2),
    (9,  "吴十",  2),
    (10, "郑一",  2),
    (11, "冯二",  2),
    (12, "陈三",  2),
    (13, "褚四",  2),
    (14, "卫五",  2),
    # 三班（6 人）
    (15, "蒋六",  3),
    (16, "沈七",  3),
    (17, "韩八",  3),
    (18, "杨九",  3),
    (19, "朱十",  3),
    (20, "秦一",  3),
    # 四班（6 人）
    (21, "许二",  4),
    (22, "何三",  4),
    (23, "吕四",  4),
    (24, "施五",  4),
    (25, "余六",  4),
    (26, "尤七",  4),
    # 五班（4 人）—— 故意少一些
    (27, "任八",  5),
    (28, "柳九",  5),
    (29, "鲍十",  5),
    (30, "水一",  5),
]

# 科目列表
SUBJECTS = ["数学", "英语", "物理", "历史", "编程"]

# 成绩数据：(student_id, subject, score)
# 设计说明：
#   - 大部分学生 5 门课全有成绩
#   - 学生 7（王明）缺少"编程"成绩 → edge case：缺考
#   - 学生 14（卫五）缺少"历史"成绩 → edge case：缺考
#   - 分数范围 35-100，含不及格（<60）
#   - 张三(id=1)数学 99 全校最高；周九(id=8)英语 38 全校最低
SCORES = [
    # ---- 一班 ----
    # 张三：数学顶尖，其他中上
    (1, "数学", 99), (1, "英语", 88), (1, "物理", 82), (1, "历史", 75), (1, "编程", 90),
    # 李四：均衡型
    (2, "数学", 78), (2, "英语", 80), (2, "物理", 76), (2, "历史", 82), (2, "编程", 74),
    # 王五：文科强理科弱
    (3, "数学", 62), (3, "英语", 91), (3, "物理", 55), (3, "历史", 94), (3, "编程", 60),
    # 赵六：理科强
    (4, "数学", 92), (4, "英语", 65), (4, "物理", 95), (4, "历史", 58), (4, "编程", 88),
    # 孙七：中等偏下
    (5, "数学", 60), (5, "英语", 68), (5, "物理", 63), (5, "历史", 70), (5, "编程", 55),
    # 钱八：全科优秀
    (6, "数学", 95), (6, "英语", 92), (6, "物理", 90), (6, "历史", 88), (6, "编程", 96),
    # 王明：编程缺考（只有 4 门成绩）
    (7, "数学", 73), (7, "英语", 77), (7, "物理", 70), (7, "历史", 80),

    # ---- 二班 ----
    # 周九：英语极差，其他一般
    (8, "数学", 72), (8, "英语", 38), (8, "物理", 68), (8, "历史", 65), (8, "编程", 70),
    # 吴十
    (9, "数学", 85), (9, "英语", 90), (9, "物理", 78), (9, "历史", 72), (9, "编程", 82),
    # 郑一
    (10, "数学", 80), (10, "英语", 76), (10, "物理", 83), (10, "历史", 79), (10, "编程", 77),
    # 冯二：编程满分
    (11, "数学", 88), (11, "英语", 84), (11, "物理", 86), (11, "历史", 80), (11, "编程", 100),
    # 陈三：多科不及格
    (12, "数学", 45), (12, "英语", 52), (12, "物理", 48), (12, "历史", 58), (12, "编程", 40),
    # 褚四
    (13, "数学", 83), (13, "英语", 87), (13, "物理", 80), (13, "历史", 85), (13, "编程", 79),
    # 卫五：历史缺考（只有 4 门成绩）
    (14, "数学", 70), (14, "英语", 66), (14, "物理", 72), (14, "编程", 68),

    # ---- 三班 ----
    (15, "数学", 86), (15, "英语", 78), (15, "物理", 90), (15, "历史", 74), (15, "编程", 85),
    (16, "数学", 68), (16, "英语", 88), (16, "物理", 60), (16, "历史", 92), (16, "编程", 62),
    (17, "数学", 93), (17, "英语", 75), (17, "物理", 91), (17, "历史", 70), (17, "编程", 94),
    (18, "数学", 58), (18, "英语", 90), (18, "物理", 52), (18, "历史", 95), (18, "编程", 50),
    (19, "数学", 90), (19, "英语", 82), (19, "物理", 88), (19, "历史", 78), (19, "编程", 92),
    (20, "数学", 75), (20, "英语", 70), (20, "物理", 73), (20, "历史", 76), (20, "编程", 71),

    # ---- 四班 ----
    (21, "数学", 82), (21, "英语", 79), (21, "物理", 85), (21, "历史", 73), (21, "编程", 80),
    (22, "数学", 77), (22, "英语", 83), (22, "物理", 74), (22, "历史", 86), (22, "编程", 75),
    (23, "数学", 91), (23, "英语", 70), (23, "物理", 93), (23, "历史", 65), (23, "编程", 89),
    (24, "数学", 64), (24, "英语", 72), (24, "物理", 60), (24, "历史", 78), (24, "编程", 58),
    (25, "数学", 88), (25, "英语", 86), (25, "物理", 84), (25, "历史", 90), (25, "编程", 87),
    (26, "数学", 55), (26, "英语", 60), (26, "物理", 50), (26, "历史", 62), (26, "编程", 48),

    # ---- 五班 ----
    (27, "数学", 96), (27, "英语", 85), (27, "物理", 92), (27, "历史", 80), (27, "编程", 95),
    (28, "数学", 70), (28, "英语", 74), (28, "物理", 68), (28, "历史", 76), (28, "编程", 72),
    (29, "数学", 60), (29, "英语", 58), (29, "物理", 55), (29, "历史", 63), (29, "编程", 52),
    (30, "数学", 35), (30, "英语", 42), (30, "物理", 40), (30, "历史", 50), (30, "编程", 38),
]


# ============================================================
# 建库逻辑
# ============================================================

def create_database():
    """
    创建多表关联的学校数据库。

    4 张表的关系（ER 图）：
        teachers ──1:N──▶ classes ──1:N──▶ students
                                              │
                                            1:N
                                              ▼
                                            scores

    edge case 设计：
        - 张老师同时带两个班（一人多班）
        - 陈老师、赵老师不带班（有教师无班级）
        - 王明缺少编程成绩、卫五缺少历史成绩（缺考）
        - 五班只有 4 人（班级人数不均）
        - 水一(id=30) 多科极低分（35 分）
        - 陈三(id=12) 多科不及格
    """
    db_path = "school.db"

    # 如果旧数据库存在，先删除（确保每次创建的数据一致）
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"🗑️  已删除旧数据库 {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 开启外键约束（SQLite 默认关闭）
    cursor.execute("PRAGMA foreign_keys = ON")

    # ---- 建表 ----

    cursor.execute("""
        CREATE TABLE teachers (
            id    INTEGER PRIMARY KEY,
            name  TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE classes (
            id          INTEGER PRIMARY KEY,
            class_name  TEXT NOT NULL,
            teacher_id  INTEGER NOT NULL,
            FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE students (
            id        INTEGER PRIMARY KEY,
            name      TEXT NOT NULL,
            class_id  INTEGER NOT NULL,
            FOREIGN KEY (class_id) REFERENCES classes(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE scores (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id  INTEGER NOT NULL,
            subject     TEXT NOT NULL,
            score       REAL NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(id)
        )
    """)

    # ---- 插入数据 ----
    cursor.executemany("INSERT INTO teachers (id, name) VALUES (?, ?)", TEACHERS)
    cursor.executemany("INSERT INTO classes (id, class_name, teacher_id) VALUES (?, ?, ?)", CLASSES)
    cursor.executemany("INSERT INTO students (id, name, class_id) VALUES (?, ?, ?)", STUDENTS)
    cursor.executemany("INSERT INTO scores (student_id, subject, score) VALUES (?, ?, ?)", SCORES)

    conn.commit()

    # ---- 验证数据 ----
    print(f"\n✅ 数据库创建成功：{db_path}")

    cursor.execute("SELECT COUNT(*) FROM teachers")
    print(f"   教师数：{cursor.fetchone()[0]}")

    cursor.execute("SELECT COUNT(*) FROM classes")
    print(f"   班级数：{cursor.fetchone()[0]}")

    cursor.execute("SELECT COUNT(*) FROM students")
    print(f"   学生数：{cursor.fetchone()[0]}")

    cursor.execute("SELECT COUNT(*) FROM scores")
    print(f"   成绩记录数：{cursor.fetchone()[0]}")

    cursor.execute("""
        SELECT c.class_name, COUNT(s.id)
        FROM classes c LEFT JOIN students s ON c.id = s.class_id
        GROUP BY c.id ORDER BY c.id
    """)
    class_counts = cursor.fetchall()
    print(f"   各班人数：{', '.join(f'{c[0]} {c[1]}人' for c in class_counts)}")

    cursor.execute("SELECT COUNT(DISTINCT subject) FROM scores")
    print(f"   科目数：{cursor.fetchone()[0]}")

    # 找出缺考情况
    cursor.execute("""
        SELECT s.name, COUNT(sc.id) AS exam_count
        FROM students s LEFT JOIN scores sc ON s.id = sc.student_id
        GROUP BY s.id
        HAVING exam_count < 5
    """)
    missing = cursor.fetchall()
    if missing:
        print(f"   缺考学生：{', '.join(f'{m[0]}({m[1]}门)' for m in missing)}")

    conn.close()

    print(f"\n📊 表结构：")
    print(f"   teachers(id, name)")
    print(f"   classes(id, class_name, teacher_id)")
    print(f"   students(id, name, class_id)")
    print(f"   scores(id, student_id, subject, score)")
    print(f"\n   关系：teachers ─1:N─▶ classes ─1:N─▶ students ─1:N─▶ scores")
    print(f"\n   下一步：运行 python agent_loop.py 开始用 Agent 查询！")


if __name__ == "__main__":
    create_database()
