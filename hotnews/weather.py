"""天气查询：中国天气网 7 天预报页面解析（免费、无需 key）。"""
import requests
from bs4 import BeautifulSoup

from .config import CITY_CODES, DEFAULT_CITY, REQUEST_TIMEOUT

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def fetch_weather(city: str = DEFAULT_CITY) -> list[dict]:
    """抓取指定城市 7 天预报，返回前 3 天 [{label, weather, temp}]。

    label 形如"26日（今天）"/"27日（明天）"。失败抛异常，由上层兜底。
    """
    code = CITY_CODES.get(city, CITY_CODES.get(DEFAULT_CITY, "101110101"))
    resp = requests.get(
        f"https://www.weather.com.cn/weather/{code}.shtml",
        headers={"User-Agent": UA},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    days = []
    for li in soup.select("ul.t.clearfix li")[:3]:
        parts = li.get_text(" ", strip=True).split()
        if len(parts) < 2:
            continue
        # 温度在 parts[2:]，形如 ["23℃","/","18℃","<3级"] 或 ["18℃","<3级"]；跳过 "/" 和风级
        temp_parts = [p for p in parts[2:] if p not in ("/",) and not p.startswith("<")]
        temp = "/".join(temp_parts[:2])
        days.append({"label": parts[0], "weather": parts[1], "temp": temp})
    return days


def _pick(days: list[dict], day: str) -> dict | None:
    """从预报里挑出今天/明天/后天。"""
    for d in days:
        if day in d["label"]:
            return d
    return None


SUPPORTED_CITY_MSG = "暂不支持该城市。目前内置支持：" + "、".join(CITY_CODES) + "。告诉我城市名，我可以帮您添加。"


def weather_answer(city: str, day: str = "今天", city_kind: str = "absent") -> str:
    """"西安明天什么天气"类问题的回答。"""
    if city_kind == "unknown":
        return SUPPORTED_CITY_MSG
    try:
        days = fetch_weather(city)
    except Exception:
        return "天气服务暂时不可用（网络或站点问题），请稍后再试。"
    d = _pick(days, day)
    if not d:
        if day in ("昨天", "前天"):
            return f"天气预报只覆盖今天起7天，查不到{day}的天气。想看实时天气可以改问今天或明天。"
        return f"暂未获取到{city}{day}的天气信息。"
    temp = f"，气温{d['temp']}" if d["temp"] else ""
    return f"{city}{day}：{d['weather']}{temp}。"


def rain_answer(city: str, day: str = "明天", city_kind: str = "absent") -> str:
    """"明天会下雨吗"类问题的回答。"""
    if city_kind == "unknown":
        return SUPPORTED_CITY_MSG
    try:
        days = fetch_weather(city)
    except Exception:
        return "天气服务暂时不可用（网络或站点问题），请稍后再试。"
    d = _pick(days, day)
    if not d:
        if day in ("昨天", "前天"):
            return f"天气预报只覆盖今天起7天，查不到{day}的天气。想看实时天气可以改问今天或明天。"
        return f"暂未获取到{city}{day}的天气信息。"
    temp = f"，气温{d['temp']}" if d["temp"] else ""
    if any(w in d["weather"] for w in ("雨", "雪", "雷")):
        return f"{city}{day}会{d['weather']}{temp}，出门记得带伞。"
    return f"{city}{day}不会下雨，{d['weather']}{temp}。"
