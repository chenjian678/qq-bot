# -*- coding: utf-8 -*-
import asyncio
import base64
import io
import json
import mimetypes
import os
import random
import re
import traceback
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import httpx
from PIL import Image
from openai import AsyncOpenAI

import nonebot
from nonebot import on_message
from nonebot.adapters.onebot.v11 import (
    Adapter as ONE_BOT_V11Adapter,
    Bot,
    MessageEvent,
    GroupMessageEvent,
    MessageSegment
)

# ==================== 【1. 核心凭证与模型配置】 ====================
# 【大脑】：官方 DeepSeek API
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# 【眼睛 & 画笔】：千问中转站多模态与生图配置
VISION_API_KEY = os.getenv("VISION_API_KEY", "")
VISION_BASE_URL = os.getenv("VISION_BASE_URL", "https://maas.qianwenaiapi.com/compatible-mode/v1")
VISION_MODEL = os.getenv("VISION_MODEL", "qwen3.8-max")
IMAGE_GEN_MODEL = os.getenv("IMAGE_GEN_MODEL", "qwen-image-3.0-pro")

# 【互联网搜索】：博查 Web Search API 配置
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")
BOCHA_SEARCH_URL = os.getenv("BOCHA_SEARCH_URL", "https://api.bochaai.com/v1/web-search")
CN_TZ = timezone(timedelta(hours=8))
WEEKDAY_NAMES_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
BING_IMAGE_SEARCH_URL = "https://www.bing.com/images/search"
BRAVE_IMAGE_API_KEY = os.getenv("BRAVE_IMAGE_API_KEY", "")
BRAVE_IMAGE_SEARCH_URL = "https://api.search.brave.com/res/v1/images/search"
ONEBOT_ACCESS_TOKEN = os.getenv("ONEBOT_ACCESS_TOKEN", "")
def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


IMAGE_RERANK_ENABLED = _env_bool("IMAGE_RERANK_ENABLED", True)
EMOJI_LIBRARY_PATH = os.getenv("EMOJI_LIBRARY_PATH", "emoji_library.json")
EMOJI_ASSET_DIR = os.getenv("EMOJI_ASSET_DIR", "emoji_assets")
HUMOR_MEMORY_PATH = os.getenv("HUMOR_MEMORY_PATH", "humor_memory.json")
PROACTIVE_EMOJI_ENABLED = _env_bool("PROACTIVE_EMOJI_ENABLED", True)
PROACTIVE_EMOJI_RATE = min(max(_env_float("PROACTIVE_EMOJI_RATE", 0.18), 0.0), 1.0)
EMOJI_COOLDOWN_SECONDS = max(_env_int("EMOJI_COOLDOWN_SECONDS", 120), 0)
MAX_SEARCH_IMAGES = 4
MAX_IMAGE_CANDIDATES = 16
MAX_RERANK_IMAGES = 8
MAX_VISION_IMAGES = 6
MAX_MEDIA_BYTES = 10 * 1024 * 1024

# 管理员 QQ 从环境变量读取，避免公开仓库泄露个人标识。
MASTER_ID = os.getenv("MASTER_ID", "").strip()
if not MASTER_ID:
    print("[配置提醒] MASTER_ID 未设置，管理员专属功能将关闭。")

ds_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
vision_client = AsyncOpenAI(api_key=VISION_API_KEY, base_url=VISION_BASE_URL)

# ==================== 【2. 工具底层动作库】 ====================

def calculate_bkn(skey: str) -> int:
    t = 5381
    for char in skey:
        t += (t << 5) + ord(char)
    return t & 2147483647


def current_time_line_cn() -> str:
    """给模型注入明确的中国时间，避免它凭训练记忆猜当前日期。"""
    now = datetime.now(CN_TZ)
    return (
        f"今天：{now.year}年{now.month:02d}月{now.day:02d}日，"
        f"{WEEKDAY_NAMES_CN[now.weekday()]}，北京时间{now:%H:%M}。"
    )

async def tool_search_web_bocha(query: str) -> str:
    """调用博查 API 全网搜索；时效性问题默认限制在一周内。"""
    try:
        print(f"--> [调用博查搜索]: {query}")
        headers = {
            "Authorization": f"Bearer {BOCHA_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "query": query,
            "freshness": "oneWeek",
            "summary": True,
            "count": 4
        }
        async with httpx.AsyncClient(timeout=12.0) as client:
            res = await client.post(BOCHA_SEARCH_URL, headers=headers, json=payload)
            if res.status_code == 200:
                data = res.json()
                web_pages = data.get("data", {}).get("webPages", {}).get("value", [])
                if not web_pages:
                    return f"网上没搜到关于 '{query}' 的靠谱内容"
                
                clean_results = []
                for p in web_pages:
                    clean_results.append({
                        "title": p.get("name") or p.get("title", ""),
                        "summary": p.get("snippet") or p.get("summary", ""),
                        "url": p.get("url", ""),
                        "date": p.get("datePublished") or p.get("dateLastCrawled") or ""
                    })
                return json.dumps(clean_results, ensure_ascii=False)
            else:
                return f"搜索接口抽风了: {res.status_code}"
    except Exception as e:
        print(f"[博查搜索异常]: {e}")
        return f"搜索报错: {e}"


def _normalise_image_url(value: object) -> str:
    """只接受可直接交给 OneBot 的 http(s) 图片地址。"""
    if not isinstance(value, str):
        return ""
    value = unescape(value).strip().replace("\\/", "/")
    if not value.startswith(("http://", "https://")):
        return ""
    return value


def clean_image_query(query: str) -> str:
    """把“帮我搜索 xxx 图片发给我”还原成真正的主题词。"""
    original = (query or "").strip()
    query = re.sub(r"[，。！？、,.!?]+", " ", original).strip()
    query = re.sub(
        r"^(?:(?:麻烦|请)?(?:帮我)?(?:找|搜索|搜|查)(?:一下|一张|几张|点|些)?|(?:麻烦|请)?帮我)",
        " ",
        query,
        flags=re.IGNORECASE
    )
    query = re.sub(
        r"(?:请|帮我|麻烦|给我|发给我|发过来|发一下|来一张|来个|搜索|搜一下|搜|找一下|找|查一下|查|图片|照片|壁纸|头像|表情包)",
        " ",
        query,
        flags=re.IGNORECASE
    )
    query = re.sub(r"\s+", " ", query).strip()
    return query or original or "图片"


def _collect_image_items(payload: object) -> List[Dict]:
    """兼容不同搜索供应商的图片字段命名。"""
    if not isinstance(payload, dict):
        return []

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    root_images = payload.get("images")
    data_images = data.get("images")
    items = []
    containers = [
        root_images,
        root_images.get("value") if isinstance(root_images, dict) else None,
        payload.get("imageResults"),
        payload.get("image_results"),
        payload.get("imagePages"),
        data_images,
        data_images.get("value") if isinstance(data_images, dict) else None,
    ]
    for container in containers:
        if not isinstance(container, list):
            continue
        for item in container:
            if isinstance(item, str):
                url = _normalise_image_url(item)
                if url:
                    items.append({"url": url, "title": ""})
                continue
            if not isinstance(item, dict):
                continue
            url = ""
            for key in ("contentUrl", "content_url", "imageUrl", "image_url", "murl", "mediaUrl", "url"):
                url = _normalise_image_url(item.get(key))
                if url:
                    break
            if url:
                items.append({
                    "url": url,
                    "title": item.get("name") or item.get("title") or item.get("caption") or "",
                    "source": item.get("hostPageUrl") or item.get("pageUrl") or item.get("sourcePage") or "",
                    "provider": item.get("provider") or "bocha"
                })
    return items


async def _search_images_bing(query: str) -> List[Dict]:
    """博查没有返回图片字段时，用 Bing 图片页作无密钥回退。"""
    try:
        params = {
            "q": query,
            "form": "HDRSC2",
            "first": "1",
            "mkt": "zh-CN",
            "setLang": "zh-CN"
        }
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with httpx.AsyncClient(headers=headers, timeout=12.0, follow_redirects=True) as client:
            response = await client.get(BING_IMAGE_SEARCH_URL, params=params)
            response.raise_for_status()

        found = []
        for raw_meta in re.findall(r'\bm="([^"\\]*(?:\\.[^"\\]*)*)"', response.text):
            try:
                meta = json.loads(unescape(raw_meta))
            except json.JSONDecodeError:
                continue
            url = _normalise_image_url(meta.get("murl") or meta.get("turl"))
            if url:
                found.append({
                    "url": url,
                    "title": meta.get("t") or meta.get("desc") or "",
                    "source": meta.get("purl") or ""
                })
            if len(found) >= MAX_SEARCH_IMAGES:
                break
        return found
    except Exception as e:
        print(f"[Bing 图片搜索异常]: {e}")
        return []


async def _search_images_brave(query: str) -> List[Dict]:
    """使用 Brave 官方图片搜索 API；密钥由 BRAVE_IMAGE_API_KEY 提供。"""
    if not BRAVE_IMAGE_API_KEY:
        return []
    try:
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": BRAVE_IMAGE_API_KEY
        }
        params = {
            "q": query,
            "count": MAX_SEARCH_IMAGES,
            "search_lang": "zh-hans",
            "country": "CN",
            "safesearch": "strict"
        }
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            response = await client.get(BRAVE_IMAGE_SEARCH_URL, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()

        found = []
        for item in payload.get("results", []):
            if not isinstance(item, dict):
                continue
            properties = item.get("properties") or {}
            thumbnail = item.get("thumbnail") or {}
            url = _normalise_image_url(properties.get("url") or thumbnail.get("src"))
            if url:
                found.append({
                    "url": url,
                    "title": item.get("title") or "",
                    "source": item.get("url") or "",
                    "provider": "brave"
                })
            if len(found) >= MAX_SEARCH_IMAGES:
                break
        return found
    except Exception as e:
        print(f"[Brave 图片搜索异常]: {e}")
        return []


def _image_query_variants(query: str) -> List[str]:
    """二创/角色图增加 Pixiv 与米游社定向检索。"""
    variants = [query]
    if re.search(r"(二创|同人|插画|立绘|角色|原神|崩坏|星穹铁道|绝区零)", query, re.IGNORECASE):
        variants.extend([
            f"{query} site:pixiv.net",
            f"{query} site:miyoushe.com"
        ])
    return variants


async def tool_search_images(query: str) -> str:
    """搜索可直接发送的图片 URL，而不是只返回网页链接。"""
    query = clean_image_query(query)
    if not query:
        return "[]"
    print(f"--> [图片搜索主题]: {query}")

    results = []
    if BOCHA_API_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {BOCHA_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "query": f"{query} 图片",
                "freshness": "noLimit",
                "summary": True,
                "count": MAX_SEARCH_IMAGES
            }
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(BOCHA_SEARCH_URL, headers=headers, json=payload)
                if response.status_code == 200:
                    results = _collect_image_items(response.json())
        except Exception as e:
            print(f"[博查图片搜索异常]: {e}")

    # 不同图片索引并行搜索，角色二创额外查询 Pixiv 和米游社。
    search_tasks = [_search_images_bing(query)]
    if BRAVE_IMAGE_API_KEY:
        search_tasks.extend(_search_images_brave(variant) for variant in _image_query_variants(query))
    provider_results = await asyncio.gather(*search_tasks, return_exceptions=True)
    result_lists = [item for item in provider_results if isinstance(item, list)]
    for result_index in range(max((len(items) for items in result_lists), default=0)):
        for items in result_lists:
            if result_index < len(items):
                results.append(items[result_index])

    deduped = []
    seen = set()
    for item in results:
        url = item.get("url", "")
        if url and url not in seen:
            seen.add(url)
            deduped.append(item)
        if len(deduped) >= MAX_IMAGE_CANDIDATES:
            break
    deduped = await rerank_image_results(query, deduped)
    return json.dumps(deduped, ensure_ascii=False)

async def tool_get_qzone(bot: Bot, target_qq: str) -> str:
    """调阅指定好友的最新空间动态"""
    try:
        print(f"--> [调阅QQ空间]: 目标 {target_qq}")
        cookies_data = await bot.get_cookies(domain="qzone.qq.com")
        cookies_str = cookies_data.get("cookies", "")
        skey_match = re.search(r"skey=@?([a-zA-Z0-9]+)", cookies_str)
        bkn = calculate_bkn(skey_match.group(0).split("=")[1]) if skey_match else 0

        url = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
        params = {
            "uin": target_qq, "hostUin": target_qq, "num": "3",
            "code_version": "1", "format": "json", "g_tk": str(bkn)
        }
        headers = {
            "Cookie": cookies_str, "User-Agent": "Mozilla/5.0",
            "Referer": f"https://user.qzone.qq.com/{target_qq}"
        }
        async with httpx.AsyncClient(headers=headers, timeout=10) as client:
            res = await client.get(url, params=params)
            raw = res.text.strip()
            match = re.search(r"^\w+\((.*)\);?$", raw, re.DOTALL)
            try:
                data = json.loads(match.group(1)) if match else res.json()
            except Exception:
                return "空间数据没解出来，估计被腾讯拦了"
            
            if data.get("code") != 0:
                return "空间锁着呢，或者没给权限，进不去"
            
            msgs = []
            for item in data.get("msglist", []) or []:
                msgs.append(f"[{item.get('createTime', '近期')}] {item.get('content', '纯图片动态')}")
            return "\n".join(msgs) if msgs else "最近空间里毛都没发一条"
    except Exception as e:
        print(f"[调阅空间异常]: {e}")
        return f"看空间失败了: {e}"

async def tool_generate_image(prompt: str) -> Tuple[str, Optional[str]]:
    """调用千问生图接口"""
    try:
        print(f"--> [调用画图]: {prompt}")
        res = await vision_client.images.generate(
            model=IMAGE_GEN_MODEL,
            prompt=prompt,
            n=1,
            size="1024x1024"
        )
        img_url = res.data[0].url
        if img_url:
            return "图画好了，一会儿直接发出去。你顺嘴回一句就行，别贴链接。", img_url
        return "没画出来，接口没给图", None
    except Exception as e:
        print(f"[生图异常]: {e}")
        return f"画图崩了: {e}", None

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "查实时天气、找视频链接、看最新新闻和帖子、或者遇到你不知道的事儿，直接用这个上网搜。搜索结果默认限最近一周，并带日期字段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，比如 '黑神话悟空 视频 bilibili'、'成都 今天天气'"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_images",
            "description": "用户明确要找图片、照片、壁纸或表情包时调用。query 只填主题词，不要填“帮我搜索、发给我”等指令。返回空数组表示没有通过相关性检查的图片，不得声称已经发图。通过筛选的图片会自动发送。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "图片搜索关键词，例如 '猫猫表情包'、'赛博朋克城市壁纸'"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_qzone_feed",
            "description": "去看对方最近的 QQ 空间说说发了啥。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_qq": {"type": "string", "description": "传 'self' 就行"}
                },
                "required": ["target_qq"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "对方让你画图、整张海报或做表情包时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "画面描述词"}
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_last_emoji",
            "description": "用户要求把刚才收到的 QQ 表情、动态表情或表情包再发一次时调用。没有收到表情时不要调用。",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_emoji",
            "description": "根据当前语境从表情库选一个合适的 QQ 内置表情或自定义图片/GIF 表情。只在真的能增强语气时调用，不要连续发送。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "想用的表情名称，可留空"},
                    "mood": {"type": "string", "description": "语境，例如开心、吐槽、震惊、无语、鼓励"}
                }
            }
        }
    }
]

# ==================== 【3. NoneBot 初始化、记忆池与清洗】 ====================
nonebot.init(
    host="0.0.0.0",
    port=8090,
    onebot_access_token=ONEBOT_ACCESS_TOKEN or None,
    api_timeout=120.0,
    websocket_timeout=120.0,
    uvicorn_kwargs={"ws_ping_interval": 10.0, "ws_ping_timeout": 30.0}
)
driver = nonebot.get_driver()
driver.register_adapter(ONE_BOT_V11Adapter)

chat_handler = on_message(priority=10, block=True)

# 并发锁 + 上下文记忆池（核心！）
session_locks = {}
session_histories = {}  # { session_id: [ {"role": "user", ...}, {"role": "assistant", ...} ] }
session_last_emoji_at = {}
MAX_HISTORY_LEN = 16    # 记住最近 16 条对话（8来回），既不断片又不撑爆 Token


def _read_json_file(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data
    except (OSError, ValueError, TypeError):
        return default


def _write_json_file(path: str, data) -> None:
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    except OSError as e:
        print(f"[持久化失败] {path}: {e}")


def load_emoji_library() -> List[Dict]:
    data = _read_json_file(EMOJI_LIBRARY_PATH, [])
    custom = data if isinstance(data, list) else []
    builtins = [
        {
            "type": "face",
            "id": face_id,
            "name": name,
            "meaning": name,
            "keywords": [name],
            "weight": 1,
            "enabled": True
        }
        for face_id, name in QQ_FACE_NAMES.items()
    ]
    known = {str(item.get("id")) for item in custom if item.get("type") == "face"}
    return custom + [item for item in builtins if str(item.get("id")) not in known]


def save_emoji_library(items: List[Dict]) -> None:
    _write_json_file(EMOJI_LIBRARY_PATH, items)


def load_humor_memory() -> Dict:
    data = _read_json_file(HUMOR_MEMORY_PATH, {})
    return data if isinstance(data, dict) else {}


def save_humor_memory(data: Dict) -> None:
    _write_json_file(HUMOR_MEMORY_PATH, data)


def emoji_entry_matches(entry: Dict, text: str, mood: str = "") -> bool:
    haystack = " ".join([
        str(entry.get("name", "")),
        str(entry.get("meaning", "")),
        " ".join(str(item) for item in entry.get("keywords", []) or []),
        mood
    ]).lower()
    return any(token and token.lower() in haystack for token in re.findall(r"[\w\u4e00-\u9fff]+", text or ""))


def choose_emoji_entry(text: str, mood: str = "", preferred_name: str = "") -> Optional[Dict]:
    entries = load_emoji_library()
    if preferred_name:
        preferred = preferred_name.lower()
        exact = [item for item in entries if preferred in str(item.get("name", "")).lower()]
        if exact:
            return random.choice(exact)
    matches = [item for item in entries if emoji_entry_matches(item, text, mood)]
    if not matches:
        matches = [item for item in entries if item.get("enabled", True)]
    if not matches:
        return None
    weighted = []
    for item in matches:
        try:
            weight = max(1, int(item.get("weight", 1)))
        except (TypeError, ValueError):
            weight = 1
        weighted.extend([item] * min(weight, 10))
    return random.choice(weighted or matches)


def emoji_library_for_prompt() -> str:
    entries = load_emoji_library()
    descriptions = []
    for item in entries[:40]:
        name = item.get("name") or item.get("id") or "未命名表情"
        meaning = item.get("meaning") or ""
        descriptions.append(f"{name}({meaning})" if meaning else str(name))
    return "、".join(descriptions) if descriptions else "暂无自定义表情库"


async def save_custom_emoji_from_segment(
    bot: Bot,
    segment: MessageSegment,
    name: str,
    meaning: str,
    keywords: List[str]
) -> Optional[Dict]:
    """把收到的图片/GIF保存到自己的表情库；QQ 收藏接口不统一，因此不依赖收藏 API。"""
    if segment.type not in ("image", "mface"):
        return None
    source = await resolve_media_source(bot, segment)
    if not source:
        return None
    data = await _load_media_bytes(source)
    if not data:
        return None

    extension = os.path.splitext(urlparse(source).path)[1].lower()
    try:
        with Image.open(io.BytesIO(data)) as image:
            detected = (image.format or "PNG").lower()
            if detected == "jpeg":
                detected = "jpg"
            extension = f".{detected}"
    except Exception:
        extension = extension if extension in (".gif", ".png", ".jpg", ".jpeg", ".webp") else ".bin"

    os.makedirs(EMOJI_ASSET_DIR, exist_ok=True)
    filename = f"emoji_{datetime.now(CN_TZ).strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}{extension}"
    path = os.path.join(EMOJI_ASSET_DIR, filename)
    try:
        with open(path, "wb") as file:
            file.write(data)
    except OSError as e:
        print(f"[保存自定义表情失败]: {e}")
        return None

    entry = {
        "type": "image",
        "path": path,
        "name": name or "自定义表情",
        "meaning": meaning or name or "自定义表情",
        "keywords": sorted(set([item for item in keywords if item])),
        "weight": 3,
        "enabled": True
    }
    library = _read_json_file(EMOJI_LIBRARY_PATH, [])
    if not isinstance(library, list):
        library = []
    library.append(entry)
    save_emoji_library(library)
    return entry


def emoji_entry_to_segment(entry: Dict) -> Optional[MessageSegment]:
    entry_type = entry.get("type")
    if entry_type == "face" and entry.get("id") is not None:
        return MessageSegment("face", {"id": str(entry.get("id"))})
    source = entry.get("path") or entry.get("url") or entry.get("file")
    if source:
        return MessageSegment.image(source)
    return None


def should_proactively_send_emoji(session_id: str, user_text: str, reply: str) -> bool:
    if not PROACTIVE_EMOJI_ENABLED or random.random() > PROACTIVE_EMOJI_RATE:
        return False
    now = datetime.now(CN_TZ).timestamp()
    if now - session_last_emoji_at.get(session_id, 0) < EMOJI_COOLDOWN_SECONDS:
        return False
    if re.search(r"(?:密码|验证码|转账|身份证|地址|投诉|报错|异常|删除|禁止|不要发|别发)", user_text + reply):
        return False
    return bool(re.search(r"(?:哈哈|笑死|好家伙|离谱|绝了|牛|收到|谢谢|可以|行|好|不愧|太|气死|无语|震惊|懂了|确实)", user_text + reply))


def remember_positive_humor(session_id: str, history: List[Dict], user_text: str) -> None:
    """用户明显觉得上一句好笑/好用时，保存少量示例供后续模仿。"""
    if not re.search(r"(?:哈哈|笑死|好好笑|太逗|绝了|牛|有梗|绷不住|笑麻了)", user_text):
        return
    previous = next((item.get("content", "") for item in reversed(history) if item.get("role") == "assistant"), "")
    if not previous or len(previous) > 240:
        return
    memory = load_humor_memory()
    examples = memory.get("examples", [])
    if not isinstance(examples, list):
        examples = []
    if previous not in examples:
        examples.append(previous)
    memory["examples"] = examples[-30:]
    save_humor_memory(memory)


def humor_examples_for_prompt() -> str:
    examples = load_humor_memory().get("examples", [])
    if not isinstance(examples, list):
        return "暂无"
    return "；".join(str(item) for item in examples[-6:] if item) or "暂无"

def get_lock(session_id: str) -> asyncio.Lock:
    if session_id not in session_locks:
        session_locks[session_id] = asyncio.Lock()
    return session_locks[session_id]


def build_help_text() -> str:
    return (
        "QQ Bot 使用帮助\\n"
        "私聊：直接发送消息\\n"
        "群聊：@我后发送消息\\n\\n"
        "常用命令：\\n"
        "/帮助 或 /help：查看帮助\\n"
        "/状态：查看运行配置状态\\n"
        "/重置会话：清空当前聊天记忆\\n\\n"
        "你还可以发送图片并说“解释一下”，或发送“搜索图片/壁纸/表情包”。"
    )


def build_status_text() -> str:
    features = [
        f"对话模型：{DEEPSEEK_MODEL}",
        f"视觉功能：{'已配置' if VISION_API_KEY else '未配置'}",
        f"网页搜索：{'已配置' if BOCHA_API_KEY else '未配置'}",
        f"图片筛选：{'开启' if IMAGE_RERANK_ENABLED else '关闭'}",
        f"主动表情：{'开启' if PROACTIVE_EMOJI_ENABLED else '关闭'}",
        f"管理员功能：{'已配置' if MASTER_ID else '未配置'}",
    ]
    return "QQ Bot 当前状态\\n" + "\\n".join(features)


def clean_output(text: str, user_id: str, bot_id: str) -> str:
    """强力清洗：去思考标签、去QQ号、去Markdown、砍掉结尾AI式追问"""
    if not text:
        return "？"
    
    # 1. 剥离 <think> 标签及各种内心戏括号
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>[\s\S]*$", "", text, flags=re.DOTALL)
    text = re.sub(r"^(?:思考过程|思考|分析|推理|内心独白|系统提示)[:：].*?\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"[（\(【\[].*?(?:思考|分析|注|内心|动作|调用|搜索|语气).*?[）\)】\]]", "", text)

    # 2. 抹除任何 QQ 号
    for qid in [MASTER_ID, user_id, bot_id]:
        if qid:
            text = text.replace(str(qid), "")
    text = re.sub(r"(?:QQ号?|qq号?|账号|UIN)[:：\s]*\d{5,11}", "", text)

    # 3. 干掉 Markdown 符号和列表序号
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"^\d+[\.、]\s*", "", text, flags=re.MULTILINE)

    # 4. 物理切除结尾典型的 AI 客服式反问/延伸追问（比如“你觉得呢？”“需要我帮你...吗？”）
    ai_tail_patterns = [
        r"[，,。！!\s]*(?:你觉得呢|你怎么看|你呢|你说呢|对吧|是吧)\s*[？\?]+$",
        r"[，,。！!\s]*(?:还有什么|还需要|要不要我|需要我|有什么我可以).*?[？\?。！!]*$",
        r"[，,。！!\s]*(?:如果你需要|如果还有其他|随时告诉我|尽管问我).*?[？\?。！!]*$"
    ]
    for pat in ai_tail_patterns:
        text = re.sub(pat, "", text).strip()

    return text.strip() if text.strip() else "？"

QQ_FACE_NAMES = {
    "0": "微笑", "1": "撇嘴", "2": "色", "3": "发呆", "4": "得意",
    "5": "流泪", "6": "害羞", "7": "闭嘴", "8": "睡", "9": "大哭",
    "10": "尴尬", "11": "发怒", "12": "调皮", "13": "呲牙", "14": "惊讶",
    "15": "难过", "16": "酷", "18": "抓狂", "19": "吐", "20": "偷笑",
    "21": "可爱", "22": "白眼", "23": "傲慢", "24": "饥饿", "25": "困",
    "26": "惊恐", "27": "流汗", "28": "憨笑", "29": "大兵", "30": "奋斗",
    "31": "咒骂", "32": "疑问", "33": "嘘", "34": "晕", "35": "折磨",
    "36": "衰", "37": "骷髅", "38": "敲打", "39": "再见", "40": "发抖",
    "41": "爱情", "42": "跳跳", "43": "企鹅", "44": "闪电", "45": "足球",
    "46": "瓢虫", "47": "便便", "48": "月亮", "49": "太阳", "50": "礼物",
    "51": "拥抱", "52": "强", "53": "弱", "54": "握手", "55": "胜利",
    "56": "抱拳", "57": "勾引", "58": "拳头", "59": "差劲", "60": "爱你",
    "61": "NO", "62": "OK", "63": "转圈", "64": "挥手", "66": "喝彩",
    "67": "爆筋", "68": "棒棒糖", "69": "茶", "70": "蛋糕", "71": "咖啡"
}


def _local_path(source: str) -> str:
    if not source.startswith("file://"):
        return source
    parsed = urlparse(source)
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path


async def _load_media_bytes(source: str) -> Optional[bytes]:
    """从本地路径或远程 URL 读取媒体，限制大小避免卡住事件循环。"""
    if not source:
        return None
    try:
        path = _local_path(source)
        if os.path.isfile(path):
            if os.path.getsize(path) > MAX_MEDIA_BYTES:
                return None
            with open(path, "rb") as file:
                return file.read()
        if source.startswith(("http://", "https://")):
            async with httpx.AsyncClient(
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=12.0,
                follow_redirects=True
            ) as client:
                response = await client.get(source)
                response.raise_for_status()
                if len(response.content) > MAX_MEDIA_BYTES:
                    return None
                return response.content
    except Exception as e:
        print(f"[媒体读取失败]: {e}")
    return None


async def resolve_media_source(bot: Bot, segment: MessageSegment) -> str:
    """优先使用 URL/path；只有 QQ 只给 file ID 时才调用 get_image 补全。"""
    data = dict(segment.data or {})
    for key in ("url", "path"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value

    file_id = data.get("file")
    if not isinstance(file_id, str) or not file_id:
        return ""
    if file_id.startswith(("http://", "https://", "file://")) or os.path.exists(file_id):
        return file_id

    try:
        result = await bot.get_image(file=file_id)
    except Exception:
        try:
            result = await bot.call_api("get_image", file=file_id)
        except Exception as e:
            print(f"[get_image 失败]: {e}")
            return ""

    result = result.get("data", result) if isinstance(result, dict) else {}
    for key in ("url", "path", "file"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def clone_segment(segment: MessageSegment) -> MessageSegment:
    try:
        return MessageSegment(segment.type, dict(segment.data or {}))
    except Exception:
        return segment


def segment_fallback_url(segment: MessageSegment) -> str:
    data = dict(segment.data or {})
    for key in ("url", "path"):
        value = data.get(key)
        if isinstance(value, str) and (
            value.startswith(("http://", "https://", "file://")) or os.path.exists(value)
        ):
            return value
    return ""


async def media_to_vision_data_urls(source: str) -> List[str]:
    """静图取一帧，动图取首/中/末帧，发送源本身不会被改写。"""
    data = await _load_media_bytes(source)
    if not data:
        return [source] if source.startswith(("http://", "https://", "data:")) else []

    try:
        with Image.open(io.BytesIO(data)) as image:
            frame_count = int(getattr(image, "n_frames", 1) or 1)
            indexes = [0] if frame_count <= 1 else sorted({0, frame_count // 2, frame_count - 1})
            previews = []
            for index in indexes:
                image.seek(index)
                frame = image.convert("RGB")
                frame.thumbnail((1024, 1024))
                buffer = io.BytesIO()
                frame.save(buffer, format="JPEG", quality=84, optimize=True)
                encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
                previews.append(f"data:image/jpeg;base64,{encoded}")
            return previews
    except Exception as e:
        print(f"[视觉预览转换失败]: {e}")
        encoded = base64.b64encode(data).decode("ascii")
        mime = mimetypes.guess_type(urlparse(source).path)[0] or "application/octet-stream"
        return [f"data:{mime};base64,{encoded}"]


async def rerank_image_results(query: str, items: List[Dict]) -> List[Dict]:
    """用视觉模型剔除“搜索词相关、图片内容不相关”的候选图。"""
    if not IMAGE_RERANK_ENABLED:
        return items
    if not VISION_API_KEY:
        print("[图片筛选未执行]: 未设置 VISION_API_KEY，拒绝发送未验证图片")
        return []
    if not items:
        return []
    try:
        contents = [{
            "type": "text",
            "text": (
                f"用户要找：{query}\n"
                "下面是候选图片。只输出一个 JSON 整数数组，列出和主题真正相关的候选编号，"
                "按相关性从高到低排列；完全不相关的不要列出。不要输出解释。"
            )
        }]
        usable_indexes = []
        for index, item in enumerate(items[:MAX_RERANK_IMAGES], start=1):
            previews = await media_to_vision_data_urls(item.get("url", ""))
            if not previews:
                continue
            usable_indexes.append(index)
            contents.append({"type": "text", "text": f"候选图片 {index}"})
            contents.append({"type": "image_url", "image_url": {"url": previews[0]}})

        if not usable_indexes:
            print("[图片筛选未执行]: 没有可供视觉模型检查的候选图")
            return []
        response = await vision_client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": "你是图片搜索结果相关性筛选器。"},
                {"role": "user", "content": contents}
            ],
            max_tokens=80,
            temperature=0
        )
        text = (response.choices[0].message.content or "").strip()
        match = re.search(r"\[[\s\S]*?\]", text)
        if not match:
            print("[图片筛选失败]: 视觉模型未返回有效编号")
            return []
        selected = json.loads(match.group(0))
        if not isinstance(selected, list):
            return []
        selected_indexes = []
        for index in selected:
            if isinstance(index, int) and index in usable_indexes and index not in selected_indexes:
                selected_indexes.append(index)
        return [items[i - 1] for i in selected_indexes[:2]]
    except Exception as e:
        print(f"[图片相关性筛选跳过]: {e}")
        return []


async def url_to_base64(img_path_or_url: str) -> str:
    """兼容旧调用方：返回第一张视觉预览。"""
    previews = await media_to_vision_data_urls(img_path_or_url)
    return previews[0] if previews else img_path_or_url


async def send_to_event(bot: Bot, event: MessageEvent, message) -> None:
    if isinstance(event, GroupMessageEvent):
        await bot.send_group_msg(group_id=event.group_id, message=message)
        return
    user_id = str(event.get_user_id())
    try:
        await bot.send_private_msg(user_id=int(user_id), message=message)
    except (TypeError, ValueError):
        await bot.send_private_msg(user_id=user_id, message=message)


async def send_media_segment(
    bot: Bot,
    event: MessageEvent,
    segment: MessageSegment,
    fallback_url: str = ""
) -> None:
    """优先发送原始 mface/face；协议端不支持时回退到图片。"""
    try:
        await send_to_event(bot, event, segment)
    except Exception as e:
        if fallback_url:
            print(f"[原始表情发送失败，回退图片]: {e}")
            await send_to_event(bot, event, MessageSegment.image(fallback_url))
        else:
            print(f"[媒体发送失败]: {e}")

# ==================== 【4. 智能体核心处理流】 ====================
@chat_handler.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        user_id = str(event.get_user_id())
        bot_id = str(event.self_id)
        if user_id == bot_id:
            return

        is_group = isinstance(event, GroupMessageEvent)
        if is_group and not event.is_tome():
            return

        sender_name = event.sender.card or event.sender.nickname or "哥们"
        is_master = (user_id == MASTER_ID)
        session_id = f"g_{event.group_id}" if is_group else f"p_{user_id}"

        # 提取文字、原始表情段与图片。发送源和视觉预览分开保存，避免 GIF 被转成静图后无法转发。
        raw_text = ""
        plain_text = ""
        media_inputs = []
        emoji_segments = []
        custom_media_segments = []
        has_real_image = False
        emoji_fallback_urls = {}
        for seg in event.get_message():
            data = dict(seg.data or {})
            if seg.type == "text":
                text = data.get("text", "")
                raw_text += text
                plain_text += text
            elif seg.type == "face":
                face_id = str(data.get("id", ""))
                face_name = data.get("text") or data.get("name") or QQ_FACE_NAMES.get(face_id, "系统表情")
                raw_text += f" [QQ表情:{face_name},ID:{face_id}] "
                emoji_segments.append(clone_segment(seg))
            elif seg.type == "mface":
                summary = data.get("summary") or data.get("text") or "动态表情"
                emoji_id = data.get("emoji_id") or data.get("id") or ""
                raw_text += f" [QQ动态表情:{summary}{f',ID:{emoji_id}' if emoji_id else ''}] "
                source = await resolve_media_source(bot, seg)
                emoji_segment = clone_segment(seg)
                emoji_segments.append(emoji_segment)
                custom_media_segments.append(seg)
                if source:
                    emoji_key = json.dumps(
                        {"type": emoji_segment.type, "data": dict(emoji_segment.data or {})},
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str
                    )
                    emoji_fallback_urls[emoji_key] = source
                if source:
                    media_inputs.append({"source": source, "kind": "动态表情", "label": summary})
            elif seg.type == "image":
                has_real_image = True
                img_summary = data.get("summary", "")
                if img_summary:
                    raw_text += f" {img_summary} "
                source = await resolve_media_source(bot, seg)
                if source:
                    media_inputs.append({"source": source, "kind": "图片", "label": img_summary})
                custom_media_segments.append(seg)

        raw_text = raw_text.strip()
        plain_text = plain_text.strip()

        # 内置轻量命令：让用户不依赖模型也能快速了解和控制机器人。
        command = re.sub(r"\s+", "", plain_text).lower()
        if command in {"/帮助", "/help", "帮助", "菜单"}:
            await send_to_event(bot, event, build_help_text())
            return
        if command in {"/状态", "/status", "状态"}:
            await send_to_event(bot, event, build_status_text())
            return
        if command in {"/重置会话", "/清空记忆", "/reset"}:
            async with get_lock(session_id):
                session_histories.pop(session_id, None)
            await send_to_event(bot, event, "这段聊天记忆清空了，咱们重新开始。")
            return

        resend_requested = bool(
            emoji_segments and re.search(r"(?:再发|发一下|发这个|把这个|重复|转发|来一个)", plain_text)
        )
        emoji_analysis_requested = bool(re.search(
            r"(?:这是什么|什么意思|啥意思|解释一下|识别一下|看一下|分析一下|描述一下|表情含义|表情意思)",
            plain_text
        ))

        # 管理员可直接用“添加表情 开心/震惊”+图片，把收到的 GIF/图片存进本地表情库。
        add_emoji_match = re.search(r"(?:添加|收录|保存)(?:一个|这个|这张)?表情(?:包)?(?:\s+(.+))?", plain_text, re.IGNORECASE)
        if add_emoji_match and is_master:
            if not custom_media_segments:
                await send_to_event(bot, event, "把图片或动图和“添加表情 名称/含义”一起发过来，我才能存进表情库。")
                return
            metadata = (add_emoji_match.group(1) or "自定义表情").strip()
            metadata = re.sub(r"[，,：:]+", " ", metadata)
            words = [item for item in re.split(r"\s+", metadata) if item]
            entry = await save_custom_emoji_from_segment(
                bot,
                custom_media_segments[0],
                words[0] if words else "自定义表情",
                metadata,
                words
            )
            if entry:
                await send_to_event(bot, event, f"收好了，记成“{entry['name']}”，以后语境合适我会拿出来用。")
            else:
                await send_to_event(bot, event, "这张表情没保存成功，可能是 NapCat 没给我可下载的图片地址。")
            return

        # 视觉模型解析图片
        image_desc = ""
        # QQ 表情默认只表示语气，不送视觉模型，避免机器人对表情做多余点评。
        vision_inputs = media_inputs if (has_real_image or emoji_analysis_requested) else []
        if vision_inputs and VISION_API_KEY:
            try:
                print("--> [视觉感知触发]: 正在看图...")
                prompt_hint = raw_text if raw_text else "（只发了张图，没打字）"
                vision_contents = [{"type": "text", "text": f"客观详细描述图里的文字、内容或表情包梗点。附加文字: {prompt_hint}"}]
                preview_count = 0
                for media in vision_inputs:
                    for preview in await media_to_vision_data_urls(media["source"]):
                        if preview_count >= MAX_VISION_IMAGES:
                            break
                        vision_contents.append({
                            "type": "text",
                            "text": f"下面是{media['kind']}的视觉预览：{media['label']}"
                        })
                        vision_contents.append({"type": "image_url", "image_url": {"url": preview}})
                        preview_count += 1
                    if preview_count >= MAX_VISION_IMAGES:
                        break

                v_res = await vision_client.chat.completions.create(
                    model=VISION_MODEL,
                    messages=[
                        {"role": "system", "content": "直接输出对图片的文字提取、梗点解读或画面描述。"},
                        {"role": "user", "content": vision_contents}
                    ],
                    max_tokens=300
                )
                image_desc = v_res.choices[0].message.content.strip()
            except Exception as e:
                print(f"[视觉解析异常]: {e}")
                image_desc = "这图裂了或者太糊，没看清"
        elif has_real_image:
            image_desc = "收到图片或QQ表情，但视觉模型没有配置，先按消息里的文字处理"

        if not raw_text and not image_desc:
            return

        role_relation = "你的死党兼老大" if is_master else "群里熟人"
        current_date_line = current_time_line_cn()
        emoji_catalog = emoji_library_for_prompt()
        humor_examples = humor_examples_for_prompt()
        
        # 活人感拉满的 System Prompt
        system_prompt = f"""你是一个真实的人，正在挂着 QQ 聊天，绝不是什么 AI 助手。
和你聊天的人叫“{sender_name}”，是{role_relation}。当前在{'群里' if is_group else '私聊'}。
{current_date_line} 如果用户问“今天”“现在”“最新”，按这个时间理解；不要凭训练记忆推测当前日期。

【像真人一样说话的铁律】：
1. 说话必须完全口语化，自然带点语气词（啊、吧、嘛、其实、说实话、啧、害）。
2. 允许有思考时的犹豫、停顿和临时改口，比如“我想想……”、“不对，应该说是……”、“怎么讲呢”。
3. 要有你自己的个人喜好、主见和情绪，觉得扯淡就直接吐槽，觉得好就夸，千万别中立客观地两边端水、罗列观点。
4. 严禁用“首先、其次、最后、总之、综上所述”这种公文结构！也严禁用 Markdown 加粗、井号或 1.2.3. 列表。
5. 句子长短要随意，有时候顺嘴回个短句、甚至半句话就行，别长篇大论。
6. **最关键的一条：把话说完就立刻停！严禁每段话结尾去总结升华，更严禁在回答最后衍生出新问题反问对方（绝对不准出现“你觉得呢？”、“需要我再帮你看看吗？”、“你今天打算干嘛？”这种客服式追问）！**
7. 绝对不准说出任何人的 QQ 号数字，也绝对不准把你的思考过程（比如“我先去搜一下”）说出来。要查天气、搜视频链接、看空间、画图，直接闷声调用工具，拿到结果后直接像人一样聊出来。
8. 表情不是每句话都发。语气明显开心、吐槽、震惊、无语、鼓励时，可以调用 send_emoji 发一个；严肃、敏感、解释技术问题时不要发。自定义表情库：{emoji_catalog}
9. 用户明确要找图片、照片、壁纸或表情包时调用 search_images；用户明确要把刚才收到的 QQ 表情再发一次时调用 send_last_emoji。只有筛选通过的图片和表情会自动发送；工具返回空数组时不要声称找到或发出了图片，也不要编造搜索来源。
10. 下面是用户曾经明确觉得好笑的表达，只参考语气和节奏，不要机械复制：{humor_examples}
11. 用户发 QQ 内置表情或动态表情时，把它当作一句话里的语气和情绪，不要点评、解说、猜图、夸“好可爱”、复述“你发了一个表情”。除非用户明确要求解释或识别，否则直接结合上下文正常回复；如果没有文字内容，可以只回一句很短的话或回一个合适的表情。
"""

        user_turn_text = f"{sender_name}: {raw_text}" if raw_text else f"{sender_name} 发了张图"
        if image_desc:
            user_turn_text += f" [图中内容：{image_desc}]"

        async with get_lock(session_id):
            # 初始化或获取当前会话的上下文历史
            if session_id not in session_histories:
                session_histories[session_id] = []
            
            history = session_histories[session_id]
            remember_positive_humor(session_id, history, plain_text)

            # 组装带上下文的请求列表：System + 历史对话 + 当前最新发言
            messages = [{"role": "system", "content": system_prompt}] + history + [
                {"role": "user", "content": user_turn_text}
            ]
            generated_image_urls = []
            searched_image_urls = []
            emoji_segments_to_send = list(emoji_segments) if resend_requested else []
            selected_emoji_entries = []

            # ReAct 工具调用循环
            for _ in range(3):
                response = await ds_client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=messages,
                    tools=AGENT_TOOLS,
                    tool_choice="auto",
                    temperature=0.85,  # 稍微调高温度，让说话更有活人随机感和情绪波动
                    max_tokens=1500
                )
                msg = response.choices[0].message
                messages.append(msg)

                if not msg.tool_calls:
                    break

                for call in msg.tool_calls:
                    name = call.function.name
                    try:
                        args = json.loads(call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    tool_output = ""

                    if name == "search_web":
                        tool_output = await tool_search_web_bocha(args.get("query", ""))
                    elif name == "search_images":
                        tool_output = await tool_search_images(args.get("query", ""))
                        try:
                            for item in json.loads(tool_output):
                                url = _normalise_image_url(item.get("url")) if isinstance(item, dict) else ""
                                if url and url not in searched_image_urls:
                                    searched_image_urls.append(url)
                        except (TypeError, json.JSONDecodeError):
                            pass
                    elif name == "get_qzone_feed":
                        target_arg = args.get("target_qq", "self")
                        target = user_id if (target_arg == "self" or not target_arg.isdigit()) else target_arg
                        tool_output = await tool_get_qzone(bot, target)
                    elif name == "generate_image":
                        tool_output, img_url = await tool_generate_image(args.get("prompt", ""))
                        if img_url:
                            generated_image_urls.append(img_url)
                    elif name == "send_last_emoji":
                        if emoji_segments:
                            emoji_segments_to_send.extend(emoji_segments)
                            tool_output = "已准备好把刚才收到的 QQ 表情原样发回去。"
                        else:
                            tool_output = "这条消息里没有可转发的 QQ 表情。"
                    elif name == "send_emoji":
                        entry = choose_emoji_entry(
                            plain_text + " " + (args.get("mood", "") or ""),
                            args.get("mood", "") or "",
                            args.get("name", "") or ""
                        )
                        if entry:
                            selected_emoji_entries.append(entry)
                            tool_output = json.dumps({
                                "selected": entry.get("name", "表情"),
                                "meaning": entry.get("meaning", "")
                            }, ensure_ascii=False)
                        else:
                            tool_output = "表情库里暂时没有合适的表情，不要硬发。"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": tool_output
                    })

            # 模型偶尔会只回答“我去找找”，这里对明确的找图指令做一次兜底，确保真的发图。
            if not searched_image_urls and re.search(r"(?:找|搜|来|发).{0,8}(?:图片|照片|壁纸|表情包|头像)", plain_text):
                direct_search = await tool_search_images(clean_image_query(plain_text))
                try:
                    for item in json.loads(direct_search):
                        url = _normalise_image_url(item.get("url")) if isinstance(item, dict) else ""
                        if url and url not in searched_image_urls:
                            searched_image_urls.append(url)
                except (TypeError, json.JSONDecodeError):
                    pass

            raw_reply = messages[-1].content or "？"
            final_reply = clean_output(raw_reply, user_id, bot_id)

            if not selected_emoji_entries and should_proactively_send_emoji(session_id, plain_text, final_reply):
                entry = choose_emoji_entry(plain_text + " " + final_reply, final_reply)
                if entry:
                    selected_emoji_entries.append(entry)

            # 将这轮纯文本对话存入历史记忆池（只存 user 和最终 assistant 回复，避免工具 JSON 污染历史）
            history.append({"role": "user", "content": user_turn_text})
            history.append({"role": "assistant", "content": final_reply})
            if len(history) > MAX_HISTORY_LEN:
                session_histories[session_id] = history[-MAX_HISTORY_LEN:]

            # 拟真打字延迟
            delay = 0.8 if len(final_reply) <= 6 else min(len(final_reply) * 0.02 + 0.8, 2.5)
            await asyncio.sleep(delay)

            # 发送文字
            chunks = [final_reply[i:i+600] for i in range(0, len(final_reply), 600)]
            for chunk in chunks:
                await send_to_event(bot, event, chunk)
                await asyncio.sleep(0.4)

            # 原样发送 QQ 表情/动态表情。mface 不被协议端接受时自动降级为图片。
            sent_emoji_keys = set()
            for emoji_segment in emoji_segments_to_send:
                key = json.dumps(
                    {"type": emoji_segment.type, "data": dict(emoji_segment.data or {})},
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str
                )
                if key in sent_emoji_keys:
                    continue
                sent_emoji_keys.add(key)
                fallback_url = segment_fallback_url(emoji_segment) or emoji_fallback_urls.get(key, "")
                await send_media_segment(bot, event, emoji_segment, fallback_url)

            # 发送模型主动选择或低频规则补充的表情。
            sent_library_keys = set()
            for entry in selected_emoji_entries[:1]:
                entry_key = json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
                if entry_key in sent_library_keys:
                    continue
                segment = emoji_entry_to_segment(entry)
                if segment:
                    await send_to_event(bot, event, segment)
                    sent_library_keys.add(entry_key)
                    session_last_emoji_at[session_id] = datetime.now(CN_TZ).timestamp()

            # 发送生图结果和网页图片搜索结果。
            for img_url in generated_image_urls + searched_image_urls:
                await send_to_event(bot, event, MessageSegment.image(img_url))

    except Exception as e:
        traceback.print_exc()
        print(f"[异常拦截]: {e}")

if __name__ == "__main__":
    nonebot.run()
