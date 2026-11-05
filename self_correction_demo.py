"""
self_correction_demo.py — 演示 Agent 的「自我纠错」能力
========================================================
要证明的事
----------
工具执行出错时，错误信息会以字符串形式回传给模型，
模型读到后能自己改写 SQL 重试 —— 这就是 Self-Correction。

为什么要「注入错误」而不是等它自己出错？
----------------------------------------
模型对简单问题一般不会写错，干等着可能一次也不触发。
为了稳定复现，这里在**第一轮** run_sql 时故意把 SQL 改错，
模拟「模型写错了」这种情况，后面的修正过程是模型真实的反应，没有人为干预。

实现方式
--------
不改 agent_loop.py 的生产代码，而是在这个演示脚本里
临时替换掉 agent_loop 引用的 execute_tool（monkey patch）。
演示结束进程就退出，不影响正式运行。

运行：
    .\.venv\Scripts\python.exe self_correction_demo.py
"""

import contextlib
import io
import json
import re
import sys
from pathlib import Path

import agent_loop
from agent_loop import run_agent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

QUESTION = "各班英语平均分从高到低排序，列出班级名和平均分"
LOG_FILE = Path(__file__).parent / "self_correction_log.txt"


class Tee(io.TextIOBase):
    """同时写屏幕和文件，这样截图和存档都不耽误。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def corrupt(sql: str) -> str:
    """
    把一条正常 SQL 改出一个确定会报错的版本。

    优先改字段名（score → score_value），这样报错信息是
    "no such column: score_value"，模型一看就知道问题在哪；
    如果语句里没有 score，就把第一个 SELECT 换成不存在的字段。
    """
    if re.search(r"\bscore\b", sql, re.I):
        return re.sub(r"\bscore\b", "score_value", sql, count=1, flags=re.I)
    return re.sub(r"\bselect\b", "SELECT __nonexistent__", sql, count=1, flags=re.I)


def make_hooked_executor():
    """包装出一个「第一次 run_sql 会故意写错」的执行器。"""
    original = agent_loop.execute_tool
    state = {"injected": False, "inject_no": 0}

    def hooked(name: str, arguments: str) -> str:
        # 只在第一次 run_sql 时动手，之后完全交给原函数
        if name == "run_sql" and not state["injected"]:
            state["injected"] = True
            state["inject_no"] += 1
            bad_sql = None
            try:
                args = json.loads(arguments)
                bad_sql = corrupt(args["sql"])
                args["sql"] = bad_sql
                arguments = json.dumps(args, ensure_ascii=False)
            except Exception:
                pass  # 解析失败就放弃注入，不影响正常流程

            if bad_sql:
                print("\n" + "!" * 64)
                print("!! 演示注入：故意把模型这条 SQL 改错（模拟模型写错字段名）")
                print(f"!! 改后：{bad_sql.strip()}")
                print("!" * 64)

            result = original(name, arguments)

            if bad_sql:
                first_line = result.strip().splitlines()[0]
                print(f"!! 模型即将读到：{first_line}")
                print("!" * 64 + "\n")
            return result

        return original(name, arguments)

    return hooked


def main() -> None:
    console = sys.stdout
    buffer = io.StringIO()
    sys.stdout = Tee(console, buffer)

    try:
        print("=" * 64)
        print("  Self-Correction 演示：模型写错 SQL 后能否自己修好")
        print("=" * 64)
        print(f"\n问题：{QUESTION}\n")
        print("-" * 64)

        # 关键一行：临时替换掉 agent_loop 里的 execute_tool
        agent_loop.execute_tool = make_hooked_executor()

        answer = run_agent(QUESTION, verbose=True)

        print("\n" + "=" * 64)
        print(f"最终回答：{answer}")
        print("=" * 64)

        # 检查一下日志里有没有出现"自我修正"的证据
        text = buffer.getvalue()
        had_error = "score_value" in text or "SQL 执行错误" in text
        had_retry = text.count("调用工具 run_sql") >= 2

        print("\n【演示结论】")
        print(f"  · 是否出现了错误反馈：{'是' if had_error else '否'}")
        print(f"  · 模型是否重试了 run_sql：{'是（重试 %d 次）' % text.count('调用工具 run_sql') if had_retry else '否'}")
        if had_error and had_retry:
            print("  · 结论：模型读到错误信息后自己改了 SQL 并成功重试 —— Self-Correction 成立")
        else:
            print("  · 结论：本次未观察到完整闭环，可重跑一次")

    finally:
        sys.stdout = console

    LOG_FILE.write_text(buffer.getvalue(), encoding="utf-8")
    print(f"\n完整过程已保存：{LOG_FILE.name}")


if __name__ == "__main__":
    main()
