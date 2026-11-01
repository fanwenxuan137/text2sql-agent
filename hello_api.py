"""
hello_api.py — 第一步：验证大模型 API 是否调通
================================================
作用：
  读 .env 里的 Key，发一句最简单的话，把模型回复打印出来。
  这一步跑通，说明「环境 + 依赖 + Key + 网络」四件事都没问题，
  后面 tools.py / agent_loop.py 才有意义。

运行：
  python hello_api.py
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# Windows 控制台中文输出修复
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 从脚本所在目录读取 .env（不管从哪个目录运行都能找到）
load_dotenv(Path(__file__).parent / ".env")

API_KEY = os.getenv("LLM_API_KEY", "").strip()
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").strip()
MODEL = os.getenv("LLM_MODEL", "deepseek-chat").strip()


def main() -> None:
    # ---- 先检查配置，报错要说得明白，别让人猜 ----
    if not API_KEY or "xxxx" in API_KEY:
        print("[X] 没有读到有效的 LLM_API_KEY")
        print("    1) 把 .env.example 复制成 .env")
        print("    2) 打开 .env，把 LLM_API_KEY= 后面换成你自己的 Key")
        print("    3) 再运行一次 python hello_api.py")
        sys.exit(1)

    print(f"模型   : {MODEL}")
    print(f"Base   : {BASE_URL}")
    print(f"Key    : {API_KEY[:6]}...{API_KEY[-4:]}（已脱敏）")
    print("-" * 50)

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "user", "content": "用一句话介绍你自己，20 字以内。"}
            ],
            temperature=0.7,
        )
    except Exception as e:
        print(f"[X] 调用失败：{type(e).__name__}")
        print(f"    {e}")
        print("\n    常见原因：")
        print("    - Key 写错或已失效（注意不要有空格、不要带引号）")
        print("    - 账户余额为 0 / 未实名")
        print("    - base_url 与 model 名字不匹配（DeepSeek 用 deepseek-chat）")
        print("    - 网络无法访问该域名")
        sys.exit(1)

    print("[OK] 调用成功")
    print(f"回复   : {resp.choices[0].message.content}")
    print(f"用量   : 输入 {resp.usage.prompt_tokens} tokens / 输出 {resp.usage.completion_tokens} tokens")
    print("\n下一步：运行 python init_db.py 生成 school.db")


if __name__ == "__main__":
    main()
