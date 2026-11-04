"""
test_queries.py — 固化 5 个测试问题，一次跑完并生成可存档结果
==============================================================
用途：
    1. 自动运行题目要求的 5 个测试问题
    2. 打印每一轮的工具调用过程（这些日志就是实验记录的截图素材）
    3. 把完整过程 + 汇总表写入 test_output.txt，方便直接引用

运行：
    .\.venv\Scripts\python.exe test_queries.py

说明：
    这里直接复用 agent_loop.run_agent，不重复实现循环逻辑。
    面试如果问「测试和实现是什么关系」，答：测试是实现的调用方，
    只负责组织输入、收集输出，不掺业务逻辑。
"""

import contextlib
import io
import sys
import time
from pathlib import Path

from agent_loop import run_agent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# 题目指定必须测的 5 个问题，从简单到复杂排列
QUESTIONS = [
    ("简单查询", "全校有多少学生？", "30"),
    ("单表聚合", "全校数学平均分是多少？", "75.67"),
    ("多表 JOIN（核心）", "张老师带的班里，编程成绩前三名是谁？", "韩八 94 / 朱十 92 / 吕四 89"),
    ("多表聚合 + 排序", "哪个班平均成绩最高？", "一班"),
    ("数据探索", "有没有缺考的学生？", "王明、卫五"),
]

OUTPUT_FILE = Path(__file__).parent / "test_output.txt"


def run_one(question: str) -> tuple[str, str, int, float]:
    """
    跑单个问题，返回 (最终回答, 过程日志, 轮数, 耗时秒)。

    用 redirect_stdout 把 run_agent 打印的过程日志截下来，
    既能存进文件，又不影响屏幕上实时显示。
    """
    buffer = io.StringIO()
    start = time.time()
    with contextlib.redirect_stdout(buffer):
        answer = run_agent(question, verbose=True)
    elapsed = time.time() - start

    logs = buffer.getvalue()
    rounds = logs.count("轮] 请求模型")  # 数一下请求了几次模型 = 循环了几轮
    return answer, logs, rounds, elapsed


def main() -> None:
    print("=" * 66)
    print("  Text-to-SQL Agent · 5 个测试问题")
    print("=" * 66)

    results = []
    full_log = []  # 完整过程，写进文件用

    for i, (kind, question, expected) in enumerate(QUESTIONS, 1):
        print(f"\n{'#' * 66}")
        print(f"# 测试 {i}/5  [{kind}]")
        print(f"# 问题：{question}")
        print(f"{'#' * 66}")

        answer, logs, rounds, elapsed = run_one(question)

        # 把过程日志打到屏幕上（原来是实时打印，被截下来了，这里补回去）
        print(logs)

        print("-" * 66)
        print(f"回答：{answer}")
        print(f"统计：{rounds} 轮工具调用，耗时 {elapsed:.1f} 秒")
        print(f"预期：{expected}")
        print("-" * 66)

        results.append((i, kind, question, answer, rounds, elapsed, expected))
        full_log.append(
            f"\n{'=' * 66}\n"
            f"测试 {i}/5  [{kind}]\n"
            f"问题：{question}\n"
            f"{'=' * 66}\n"
            f"{logs}\n"
            f"--- 最终回答 ---\n{answer}\n"
            f"--- 统计 ---\n轮数：{rounds}，耗时：{elapsed:.1f}s，预期：{expected}\n"
        )

    # ---- 汇总表 ----
    summary_lines = [
        "=" * 66,
        "  汇总表",
        "=" * 66,
        "",
        f"{'#':<3}{'类型':<18}{'轮数':<6}{'耗时':<8}{'预期':<24}",
        "-" * 66,
    ]
    for i, kind, question, answer, rounds, elapsed, expected in results:
        summary_lines.append(f"{i:<3}{kind:<16}{rounds:<6}{elapsed:<8.1f}{expected}")
    summary_lines += [
        "-" * 66,
        "",
        "完整回答：",
    ]
    for i, kind, question, answer, rounds, elapsed, expected in results:
        summary_lines.append(f"\n[{i}] {question}\n    {answer.strip()}")

    summary = "\n".join(summary_lines)
    print("\n" + summary)

    # ---- 写文件 ----
    header = (
        "Text-to-SQL Agent · 测试运行记录\n"
        f"运行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "运行命令：.\\\\.venv\\\\Scripts\\\\python.exe test_queries.py\n"
    )
    OUTPUT_FILE.write_text(header + "".join(full_log) + "\n\n" + summary + "\n", encoding="utf-8")

    print(f"\n完整过程已保存到：{OUTPUT_FILE.name}（可直接截图或粘贴进实验记录）")


if __name__ == "__main__":
    main()
