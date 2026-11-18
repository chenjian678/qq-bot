# qq-bot

这是一个我自己用来挂 QQ 的机器人项目。

它通过 OneBot v11 接收 QQ 消息，再调用 OpenAI 兼容接口生成回复。平时可以用它聊天、看图、搜图片，也可以让它生成图片。项目还保留了一些 QQ 空间和表情相关的功能。

## 目前能做什么

- 私聊直接聊天；
- 群里 @ 机器人后聊天；
- 保存一段时间的聊天上下文；
- 发送图片后让机器人解释图片；
- 搜索图片、壁纸和表情包；
- 调用图片模型生成图片；
- 读取当前账号有权限访问的 QQ 空间动态；
- 使用 QQ 内置表情和自定义表情；
- 用命令查看帮助、运行状态和清空当前会话。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `/帮助` 或 `/help` | 查看使用说明 |
| `/状态` | 查看模型和功能配置状态 |
| `/重置会话` | 清空当前私聊或群聊的上下文 |
| `添加表情 名称` + 图片 | 把图片保存到本地表情库，管理员可用 |

普通聊天不需要加命令。群聊里需要先 @ 机器人。

## 运行环境

- Python 3.10 及以上；
- NapCat、go-cqhttp 或其他 OneBot v11 实现；
- 一个可以使用的聊天模型 API；
- 如果要看图、搜图或生图，还要配置相应的 API。

## 安装

```bash
git clone https://github.com/chenjian678/qq-bot.git
cd qq-bot

python -m venv .venv
```

Windows：

```powershell
.venv\\Scripts\\Activate.ps1
```

Linux 或 macOS：

```bash
source .venv/bin/activate
```

安装依赖：

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 配置

先复制配置模板：

Windows PowerShell：

```powershell
Copy-Item qqbot.env.example .env
```

Linux 或 macOS：

```bash
cp qqbot.env.example .env
```

程序不会自动读取 `.env` 文件，需要把这些变量设置到启动程序的环境中。

最少需要配置：

```dotenv
DEEPSEEK_API_KEY=你的聊天模型密钥
MASTER_ID=你的管理员QQ号
```

常用的可选配置：

```dotenv
VISION_API_KEY=你的视觉模型密钥
BOCHA_API_KEY=你的网页搜索密钥
BRAVE_IMAGE_API_KEY=你的 Brave 图片搜索密钥
ONEBOT_ACCESS_TOKEN=你的 OneBot 访问令牌
```

其他模型地址和模型名称可以直接在 `qqbot.env.example` 里修改。

## 连接 OneBot

启动机器人后，让 OneBot 连接下面这个反向 WebSocket 地址：

```text
ws://127.0.0.1:8090/onebot/v11/ws
```

如果机器人和 OneBot 不在同一台机器，把 `127.0.0.1` 换成机器人所在服务器的地址。

## 启动

推荐使用：

```bash
python bot.py
```

旧的启动文件也保留着：

```bash
python qqbot_enhanced.py
```

这两个入口使用的是同一份实现，后续代码只维护在 `bot.py`。

## 示例

私聊里可以直接发：

```text
你好
帮我解释一下这张图
搜索一张猫猫表情包
画一张雨夜里的赛博朋克城市
```

群聊里先 @ 机器人，再发送内容。

## 文件说明

```text
bot.py                     主程序
qqbot_enhanced.py          兼容旧命令的入口
qqbot.env.example          配置模板
emoji_library.example.json 表情库示例
requirements.txt           Python 依赖
SECURITY.md                安全说明
```

运行后会产生一些本地文件：

- `emoji_library.json`：自定义表情信息；
- `emoji_assets/`：保存的图片和 GIF；
- `humor_memory.json`：少量对话记忆；
- `__pycache__/`：Python 缓存。

这些文件已经写进 `.gitignore`，不会被提交。

## 注意事项

- 不要把 API Key、OneBot Token 或 Cookies 提交到 GitHub；
- 如果密钥以前提交过，需要先撤销并重新生成；
- 图片搜索结果来自第三方网站，使用时要注意版权；
- QQ 空间只能读取当前账号有权限访问的内容；
- 没有配置 `MASTER_ID` 时，机器人仍能聊天，但管理员命令不会生效。

## 检查代码

提交前可以运行：

```bash
python -m py_compile bot.py qqbot_enhanced.py
```

这个项目目前更适合个人使用。如果你遇到问题，可以提交 Issue，并附上运行环境、报错信息和触发步骤，记得先删掉密钥和个人信息。
