# QQ Bot

基于 NoneBot 2、OneBot v11 和 OpenAI 兼容 API 的 QQ 机器人，支持私聊、群聊 @、上下文对话、图片理解、图片搜索、文生图、QQ 空间动态和表情库。

## 快速开始

需要 Python 3.10+、NapCat/go-cqhttp 等 OneBot v11 实现，以及至少一个聊天模型 API。

```bash
git clone https://github.com/chenjian678/qq-bot.git
cd qq-bot
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

复制 `qqbot.env.example`，配置至少：

```dotenv
DEEPSEEK_API_KEY=你的密钥
MASTER_ID=你的管理员QQ号
```

启动：

```bash
python bot.py
```

OneBot 反向 WebSocket 默认连接 `ws://127.0.0.1:8090/onebot/v11/ws`。

## 功能

- DeepSeek 兼容 API 对话和工具调用
- 图片与 GIF 关键帧识别
- Bing 图片搜索，可选 Brave 搜索和视觉相关性筛选
- OpenAI 兼容文生图
- QQ 空间动态读取
- QQ 内置表情、自定义表情和会话记忆

## 安全

不要提交 API Key、OneBot Token、Cookies、QQ 号或运行时数据。密钥如果曾经进入 Git 历史，需要立即撤销并重新生成。

常用配置：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `DEEPSEEK_MODEL` | 对话模型 | `deepseek-chat` |
| `VISION_MODEL` | 视觉模型 | `qwen3.8-max` |
| `IMAGE_GEN_MODEL` | 生图模型 | `qwen-image-3.0-pro` |
| `IMAGE_RERANK_ENABLED` | 是否筛选图片 | `true` |
| `PROACTIVE_EMOJI_RATE` | 主动表情概率 | `0.18` |

检查：

```bash
python -m py_compile bot.py qqbot_enhanced.py
```
