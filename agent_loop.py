"""
agent_loop.py — Agent 主循环
==============================
这是整个项目的核心。前面 tools.py 只是「工具」，这个文件负责
让模型和工具**反复对话**，直到它能回答用户的问题。

一轮循环发生了什么
------------------
    ┌─────────────────────────────────────────┐
    │ 1. 把 messages 发给模型                  │
    │ 2. 模型回一条消息                        │
    │    ├─ 有 tool_calls  → 执行工具          │
    │    │   → 结果作为 role="tool" 塞回历史   │
    │    │   → 回到第 1 步（这就是「循环」）   │
    │    └─ 没有 tool_calls → 这是最终回答     │
    │        → 结束                            │
    └─────────────────────────────────────────┘

为什么必须循环？
    实测过：问「全校有多少学生」，模型要走 4 轮
    （list_tables → describe_table → run_sql → 回答）。
    只调一次的话，第 2、3 轮的回答是空的。
    而「谁也不知道要几轮」——所以要用 while，
    外加 MAX_STEPS 兜底防止死循环。

运行方式
--------
    python agent_loop.py                    进入交互模式，可连续提问
    python agent_loop.py "全校有多少学生？"   只问一个问题
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from tools import TOOLS, execute_tool

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).parent / ".env")

MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
MAX_STEPS = 8  # 单个问题最多允许几轮工具调用，防止模型卡死烧钱

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
)


# ============================================================
# System Prompt：决定 Agent 的「工作方式」
# ============================================================
# 写这个东西的原则：
#   1) 明确禁止它凭猜测回答（否则它可能直接编一个数字）
#   2) 规定工作顺序（先看表 → 再看结构 → 再查数据）
#   3) 规定输出风格（中文、简洁、别贴原始表格）
SYSTEM_PROMPT = """你是一个学校数据库查询助手，可以通过工具查询一个 SQLite 数据库。

数据库中包含以下业务数据：教师、班级、学生、各科成绩。

工作规则：
1. 回答任何涉及数据的问题前，必须先调用工具获取真实数据，绝不能凭猜测或假设编造数字。
2. 标准工作顺序：先用 list_tables 了解有哪些表，再用 describe_table 确认字段名，最后用 run_sql 查询。
   如果你已经清楚表结构，可以跳过前面的步骤直接写 SQL。
3. 只允许写 SELECT 查询。如果工具返回错误，请阅读错误信息修正 SQL 后重试。
4. 涉及"某老师带的班"这类问题时，注意一位老师可能带多个班级，不要遗漏。
5. 最终回答用中文，简洁直接地给出结论；不要把原始表格整段贴出来，只提炼结论和关键数字。"""


# ============================================================
# 核心：Agent 循环
# ============================================================

def run_agent(question: str, history: list | None = None, verbose: bool = True) -> str:
    """
    处理一个问题，返回最终回答。

    参数：
        question : 用户的问题
        history  : 之前对话的 messages（多轮对话用，传 None 表示新对话）
        verbose  : 是否打印每轮的思考过程和工具调用

    返回：
        模型的最终自然语言回答
    """
    # ---- 组装消息列表 ----
    messages = history if history is not None else [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": question})

    for step in range(1, MAX_STEPS + 1):
        if verbose:
            print(f"\n  [第 {step} 轮] 请求模型...")

        # ====================================================
        # ★★ 核心块 1：带上工具发起请求 ★★
        # ====================================================
        # tools=TOOLS 是关键 —— 不传这个参数，模型永远不知道自己有工具可用。
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            temperature=0,  # 查数据要稳定，不要创造性
        )

        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        # 模型有时会先「自言自语」一句（比如"我先看看有哪些表"），顺手打印出来
        if verbose and msg.content:
            print(f"  思考：{msg.content.strip()}")

        # ====================================================
        # ★★ 核心块 2：判断结束条件 ★★
        # ====================================================
        # 没有 tool_calls，说明模型认为自己可以直接回答了 → 循环结束。
        # 这是唯一的正常退出路径。
        if not msg.tool_calls:
            if verbose:
                print(f"  [第 {step} 轮] 模型给出最终回答（无更多工具调用）")
            return msg.content or "（模型没有返回内容）"

        # ====================================================
        # ★★ 核心块 3：执行工具 + 把结果塞回历史 ★★
        # ====================================================
        # 这三行顺序和格式都不能错，是 Function Calling 的协议要求：
        #   a) 先把模型这条带 tool_calls 的消息原样加进历史
        #      —— 漏了这步，模型下一轮会不认自己刚才要调工具，接口会直接报错
        messages.append(msg)

        for call in msg.tool_calls:
            name = call.function.name
            args = call.function.arguments
            if verbose:
                print(f"  → 调用工具 {name}，参数 {args}")

            result = execute_tool(name, args)

            if verbose:
                preview = result.replace("\n", " / ")
                print(f"  ← 工具返回：{preview[:150]}")

            #   b) 工具结果必须以 role="tool" 回传，不能写成 "user"
            #   c) tool_call_id 必须是 call.id 的原值，它负责把「这次结果」
            #      和「刚才那个调用」对号入座。一个都不能错。
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })

        if verbose:
            print(f"  [第 {step} 轮] 工具执行完毕，继续下一轮")

    # ---- 跑满 MAX_STEPS 还没结束 ----
    # 说明模型陷在"反复调工具"里出不来了，必须强行中断，否则会一直烧 API 额度。
    print(f"\n  [!] 已达到最大轮数 {MAX_STEPS}，强制停止。")
    print("      可能原因：问题太复杂，或模型反复尝试失败的 SQL。")
    return "抱歉，这个问题需要太多步骤，我没能在限定轮数内完成。"


# ============================================================
# 入口
# ============================================================

def main() -> None:
    # 方式一：命令行直接问 → python agent_loop.py "问题"
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        print(f"\n问题：{question}")
        print("-" * 60)
        answer = run_agent(question)
        print("\n" + "=" * 60)
        print(f"回答：{answer}")
        print("=" * 60)
        return

    # 方式二：交互模式，支持连续提问（对话历史会保留）
    print("=" * 60)
    print("  学校数据库助手（输入 exit 退出）")
    print("=" * 60)
    print("  示例问题：")
    print("    · 全校有多少学生？")
    print("    · 全校数学平均分是多少？")
    print("    · 张老师带的班里，编程成绩前三名是谁？")
    print()

    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            question = input("你问：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break

        if not question:
            continue
        if question.lower() in ("exit", "quit", "q"):
            print("再见。")
            break

        print("-" * 60)
        answer = run_agent(question, history=history)
        print("\n回答：" + answer)
        print("=" * 60)


if __name__ == "__main__":
    main()
