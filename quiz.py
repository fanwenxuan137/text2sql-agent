"""
quiz.py — 5 分钟自测（检验 agent_loop.py 的核心概念）
=====================================================
8 道选择题，自动判分并给出讲解。
最后 2 道简答，自己默写，再看参考答案对照。

运行：
    双击 run_quiz.bat
    或  .\.venv\Scripts\python.exe quiz.py
"""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# ============================================================
# 题库：(题目, 选项列表, 正确选项下标, 讲解)
# ============================================================
QUESTIONS = [
    (
        "调用模型时，tools=TOOLS 这个参数的作用是什么？",
        [
            "A. 告诉模型有哪些工具可用，它才知道可以请求调用",
            "B. 让模型直接执行 SQL",
            "C. 把工具的执行结果传给模型",
            "D. 限制模型只能回答数据库问题",
        ],
        0,
        "tools 只负责「声明」，相当于给模型一份工具说明书。模型看到说明书后，"
        "才会返回 tool_calls 表示「我想用某个工具」。真正执行的是我们自己的 Python 代码。",
    ),
    (
        "循环应该在什么时候结束？",
        [
            "A. finish_reason 等于 'stop' 时",
            "B. msg.tool_calls 为空时",
            "C. 工具返回结果为空时",
            "D. 用户不再输入时",
        ],
        1,
        "判断 msg.tool_calls 是否为空最通用。各厂商对 finish_reason 的实现不完全一致，"
        "而「有没有要调工具」这个事实是确定的。这是设计取舍，不是唯一答案，但要能说出理由。",
    ),
    (
        "模型返回了 tool_calls，为什么必须先把这条消息 messages.append(msg)？",
        [
            "A. 不 append 的话模型会忘记问题",
            "B. 为了让日志更好看",
            "C. 后面 role='tool' 的结果必须有对应的 tool_calls 消息可配对，否则报 400",
            "D. 不 append 会导致 API 扣费翻倍",
        ],
        2,
        "实测报错：Messages with role 'tool' must be a response to a preceding message with "
        "'tool_calls'。工具结果是无处安放的，协议上必须先有「调用」再有「结果」。",
    ),
    (
        "工具执行结果回传时，role 应该写成什么？",
        [
            "A. \"user\"",
            "B. \"assistant\"",
            "C. \"system\"",
            "D. \"tool\"",
        ],
        3,
        "必须是 \"tool\"。写成 user 会报 An assistant message with 'tool_calls' must be "
        "followed by tool messages —— 模型会以为这是用户在说话，协议链断裂。",
    ),
    (
        "tool_call_id 必须用 call.id 的原值，它解决什么问题？",
        [
            "A. 记录调用时间",
            "B. 把「这次工具结果」和「刚才那个调用请求」精确配对",
            "C. 标识是哪个用户发起的",
            "D. 用于计费统计",
        ],
        1,
        "一次可能同时调多个工具（比如同时查两张表），结果回来时必须靠 id 对号入座，"
        "否则模型分不清「4 张表」这个结果对应的是哪个调用。",
    ),
    (
        "MAX_STEPS = 8 这个上限，主要是防什么？",
        [
            "A. 防止程序占满 CPU",
            "B. 防止数据库被锁",
            "C. 防止模型陷入反复调工具的死循环，持续消耗 API 额度",
            "D. 防止回答太长",
        ],
        2,
        "模型可能反复写错 SQL 一直重试。没有上限就会无限循环、持续扣费。"
        "这是防呆设计，不是功能。",
    ),
    (
        "tools.py 里 SQL 执行报错时，返回错误字符串而不是抛异常，为什么？",
        [
            "A. 抛异常会导致整个循环中断，模型也就失去了自我修正的机会",
            "B. 字符串比异常更省内存",
            "C. Python 在 Agent 里不允许用异常",
            "D. 因为 sqlite3 不支持异常",
        ],
        0,
        "把错误原文（如 no such column: xxx）返回给模型，它读到后能改写 SQL 重试，"
        "这就是 Self-Correction。如果直接 raise，程序崩了，模型根本看不到错误。",
    ),
    (
        "调用模型时设置 temperature=0，目的是？",
        [
            "A. 让回答更有创意",
            "B. 降低费用",
            "C. 让输出更确定、可复现，同样的问法得到同样的 SQL",
            "D. 加快响应速度",
        ],
        2,
        "查数据场景要的是准确和可复现，不需要创造性。temperature 越高输出越随机，"
        "测试结果就无法复现，也没法写进实验记录。",
    ),
]


# ============================================================
# 简答题：(题目, 参考答案)
# ============================================================
ESSAYS = [
    (
        "用一句话说清「一轮完整的工具调用」包含哪几步？",
        "发请求（带 tools）→ 模型返回 tool_calls → 把这条消息原样存进历史 "
        "→ 执行工具 → 结果以 role=tool + call.id 回传 → 再次发请求。",
    ),
    (
        "如果模型对着一个简单问题连续调了 8 次工具还没给出答案，你会怎么排查？（提示：先看日志）",
        "先看日志里每轮调了什么工具、返回了什么：① 是不是工具一直报错（表名/字段名写错），"
        "模型在死磕同一个错误 SQL；② 是不是工具返回的内容太长或格式太乱，模型读不懂；"
        "③ 是不是 System Prompt 没规定工作顺序，模型在反复探索只读不改。"
        "针对 ① 可以把错误提示写得更明确，针对 ③ 可以在 Prompt 里规定先 list→describe→sql 的顺序。",
    ),
]


def main() -> None:
    print("=" * 62)
    print("  Text-to-SQL Agent · 核心概念自测")
    print("  共 8 道选择题 + 2 道简答，约 5 分钟")
    print("=" * 62)

    score = 0
    wrong = []

    for i, (q, opts, ans, why) in enumerate(QUESTIONS, 1):
        print(f"\n第 {i} 题  {q}")
        for o in opts:
            print(f"    {o}")

        # 反复要输入，直到输入合法选项
        while True:
            try:
                choice = input("  你的答案（A/B/C/D）：").strip().upper()
            except (EOFError, KeyboardInterrupt):
                print("\n已退出自测。")
                return
            if choice in ("A", "B", "C", "D"):
                break
            print("  请输入 A、B、C 或 D")

        picked = "ABCD".index(choice)
        if picked == ans:
            score += 1
            print("  [对]")
        else:
            print(f"  [错] 正确答案是 {'ABCD'[ans]}")
            wrong.append(i)
        print(f"  讲解：{why}")

    # ---- 简答：先自己写，再看参考答案 ----
    print("\n" + "=" * 62)
    print("  简答部分（先自己默写，回车后看参考答案）")
    print("=" * 62)

    for j, (q, ref) in enumerate(ESSAYS, 1):
        print(f"\n简答 {j}：{q}")
        try:
            mine = input("  你的回答（直接回车也可）：").strip()
        except (EOFError, KeyboardInterrupt):
            mine = ""
        print(f"\n  参考答案：{ref}")
        if mine:
            print(f"  你写的　：{mine}")
            print("  → 自己对照一下：关键步骤有没有漏、顺序对不对。")

    # ---- 结算 ----
    print("\n" + "=" * 62)
    print(f"  选择题得分：{score} / {len(QUESTIONS)}")
    if score == len(QUESTIONS):
        print("  全对。核心协议已经吃透了，可以放心进下一步。")
    elif score >= 6:
        print(f"  第 {', '.join(map(str, wrong))} 题再看一眼就行，不影响继续推进。")
    else:
        print(f"  错的是第 {', '.join(map(str, wrong))} 题，建议回去把 agent_loop.py")
        print("  里标了 ★★ 的三块再读一遍，特别是核心块 3 的三行协议。")
    print("=" * 62)
    print("  按任意键关闭")
    input()


if __name__ == "__main__":
    main()
