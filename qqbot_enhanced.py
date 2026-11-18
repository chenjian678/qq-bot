# 兼容旧启动命令，实际实现统一维护在 bot.py。
from bot import *  # noqa: F401,F403

if __name__ == "__main__":
    nonebot.run()
