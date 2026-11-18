# QQ Bot

基于 NoneBot 2、OneBot v11 和 OpenAI 兼容 API 的 QQ 机器人，支持私聊、群聊 @、上下文对话、图片理解、图片搜索、文生图、QQ 空间动态和表情库。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![OneBot](https://img.shields.io/badge/OneBot-v11-5865F2)
![License](https://img.shields.io/badge/license-not%20declared-lightgrey)

## 功能亮点

- 文字对话：通过 DeepSeek 或其他 OpenAI 兼容 API 进行上下文聊天。
- 图片理解：识别图片、动态表情和 GIF 关键帧。
- 图片搜索：支持 Bing，可选 Brave，并可用视觉模型筛选结果。
- 文生图：调用 OpenAI 兼容图片接口生成图片。
- QQ 空间：读取当前账号有权限访问的空间动态。
- 表情系统：支持 QQ 内置表情、自定义图片/GIF 表情和低频主动发送。
- 会话记忆：私聊、群聊分别维护上下文，可随时重置。
- 内置命令：用户可以直接输入 `/帮助`、`/状态`、`/重置会话`。

## 对话示例

| 你可以发送 | 机器人会做什么 |
| --- | --- |
| `介绍一下自己` | 进行普通 AI 对话 |
| `/帮助` | 显示使用说明 |
| `/状态` | 显示模型和可选功能是否已配置 |
| `/重置会话` | 清空当前私聊或群聊上下文 |
| 发一张图片并说“解释一下” | 调用视觉模型分析图片 |
| `搜索猫猫表情包` | 搜索并发送相关图片 |
| `画一张赛博朋克城市` | 调用生图模型 |

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

复制 `qqbot.env.example`，至少配置：

```dotenv
DEEPSEEK_API_KEY=你的密钥
MASTER_ID=你的管理员QQ号
```

启动：

```bash
python bot.py
```

OneBot 反向 WebSocket 默认连接：

```text
ws://127.0.0.1:8090/onebot/v11/ws
```

## 配置和可选能力

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `DEEPSEEK_MODEL` | 对话模型 | `deepseek-chat` |
| `VISION_API_KEY` | 视觉和生图密钥 | 空 |
| `VISION_MODEL` | 视觉模型 | `qwen3.8-max` |
| `IMAGE_GEN_MODEL` | 生图模型 | `qwen-image-3.0-pro` |
| `BOCHA_API_KEY` | 网页搜索密钥 | 空 |
| `BRAVE_IMAGE_API_KEY` | Brave 图片搜索密钥 | 空 |
| `IMAGE_RERANK_ENABLED` | 是否筛选图片 | `true` |
| `PROACTIVE_EMOJI_RATE` | 主动表情概率 | `0.18` |
| `EMOJI_COOLDOWN_SECONDS` | 表情冷却时间 | `120` |

布尔值支持 `1`、`true`、`yes`、`on`。未设置 `MASTER_ID` 时，机器人仍可运行，但管理员功能关闭。

## 项目结构

```text
bot.py                     # 主程序
qqbot_enhanced.py          # 兼容旧启动命令
qqbot.env.example          # 环境变量模板
emoji_library.example.json # 表情库模板
requirements.txt           # Python 依赖
SECURITY.md                # 安全说明
```

## 安全

不要提交 API Key、OneBot Token、Cookies、QQ 号或运行时数据。密钥如果曾经进入 Git 历史，需要立即撤销并重新生成。

运行时文件包括 `emoji_library.json`、`emoji_assets/` 和 `humor_memory.json`，已加入忽略规则。

## 开发检查

```bash
python -m py_compile bot.py qqbot_enhanced.py
```

欢迎通过 Issue 反馈问题或提出新功能。后续计划包括 Docker 部署示例、自动化测试和更细的权限配置。

仓库当前未声明许可证，二次分发前请补充许可证。
