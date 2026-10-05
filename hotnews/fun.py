"""趣味功能：笑话 / 谜语 / 颜文字 / 结束语。

颜文字只使用 GBK 可编码字符（避免在传统 Windows 终端 cp936 下输出
emoji 导致乱码或 UnicodeEncodeError）；扩展库中的候选在运行时经
gbk_safe 过滤，坏的符号自动剔除，保证安全。
"""
import random


def gbk_safe(text: str) -> str:
    """过滤掉无法用 GBK 编码的字符（防止传统 Windows 终端乱码/报错）。"""
    if not text:
        return ""
    out = []
    for ch in text:
        try:
            ch.encode("gbk")
            out.append(ch)
        except UnicodeEncodeError:
            pass  # 剔除不可编码字符（emoji 等）
    return "".join(out).strip()


# GBK 安全的颜文字候选库（运行时经 gbk_safe 过滤，无法编码的自动剔除）
FACES = [
    "^_^", "(^_^)", "O(∩_∩)O", "T_T", ">_<", ":-)", ";-)", "XD", "(=_=)",
    "(^▽^)", "(*^▽^*)", "(*¯︶¯*)", "(☆ω☆)", "(≧▽≦)", "(￣▽￣)",
    "^o^", "^-^", "T^T", "ToT", "QAQ", "OvO", "OwO", "0_0", "O_o", "o_O",
    "-_-|||", "←_←", "→_→", "↑_↑", "↓_↓", "↖(^ω^)↗", "(・∀・)",
    "(ノωヽ)", "Orz", "(=￣ω￣=)", "(｡･ω･｡)", "(。・ω・。)", "ヽ(✿ﾟ▽ﾟ)ノ",
]
FACES = [f for f in FACES if f == gbk_safe(f)]  # 启动时剔除 GBK 不可编码项


# 结束语模板库（LLM 不可用时的降级方案，随机选取 + 随机颜文字）
FAREWELLS = [
    "本次对话已结束，再见！",
    "聊得很开心，下次再见！",
    "今天的对话就到这里，随时欢迎再来！",
    "再见啦，期待下次见面！",
    "本次对话结束，祝你一切顺利！",
    "就先聊到这里，后会有期！",
    "希望刚才的回答帮到了你，再见！",
    "本次对话结束，保持好心情！",
]


def random_face() -> str:
    return random.choice(FACES)


def random_farewell() -> str:
    """降级结束语：随机模板 + 随机颜文字。"""
    return random.choice(FAREWELLS) + " " + random_face()

JOKES = [
    "为什么 C++ 比 C 更受欢迎？因为它有更多的『对象』(object)！",
    "程序员最讨厌的两件事：1. 写注释；2. 别人不写注释。",
    "为什么数学书总是很忧郁？因为它有太多问题要解决。",
    "为什么 WiFi 和爸爸很像？都连不上的时候让人抓狂。",
    "为什么鱼不用电脑？因为一上网就会被『钓』走。",
    "为什么电脑总是很冷？因为它的窗户(Windows)常年开着。",
    "为什么程序员分不清万圣节和圣诞节？因为 Oct 31 == Dec 25。",
    "为什么咖啡特别擅长写代码？因为它总能让人『提神』醒脑。",
    "为什么书包总是不开心？因为里面装满了『沉甸甸』的知识。",
    "为什么 Python 开发者出门不用带钥匙？因为他的蛇(snake)会自己爬。",
]

RIDDLES = [
    {"q": "麻屋子，红帐子，里面住个白胖子。打一食物", "a": "花生", "hint": "常见坚果，下酒菜"},
    {"q": "千条线，万条线，掉到水里看不见。打一自然现象", "a": "雨", "hint": "出门要带伞"},
    {"q": "上边毛，下边毛，中间一颗黑葡萄。打一五官", "a": "眼睛", "hint": "心灵的窗户"},
    {"q": "红口袋，绿口袋，有人害怕有人爱。打一蔬菜", "a": "辣椒", "hint": "很辣的那种"},
    {"q": "远看山有色，近听水无声。春去花还在，人来鸟不惊。打一物", "a": "画", "hint": "挂在墙上的艺术品"},
    {"q": "耳朵长，尾巴短，红眼睛，白毛衫，三瓣嘴儿胆子小，青菜萝卜吃个饱。打一动物", "a": "兔子", "hint": "爱吃胡萝卜"},
    {"q": "什么门永远关不上？", "a": "球门", "hint": "踢足球的那个"},
    {"q": "什么东西越洗越脏？", "a": "水", "hint": "洗手用的"},
    {"q": "小小年纪，却有白胡一把，抓把胡须，就叫妈妈。打一农作物", "a": "玉米", "hint": "煮着吃很香"},
    {"q": "什么车没有轮子也不能开？", "a": "风车", "hint": "会转但不动"},
]

# 猜谜状态下，这些词视为新命令而非答案（避免用户想干别的事却被当成猜谜）
NEW_COMMAND_WORDS = (
    "天气", "下雨", "下雪", "气温", "周几", "星期几", "几月几号", "几号",
    "热搜", "热榜", "榜单", "十大", "TOP", "top", "排名",
    "笑话", "搞笑", "谜语", "猜谜",
    "退出", "再见", "拜拜", "结束", "下次再聊", "聊天结束",
)


def random_face() -> str:
    return random.choice(FACES)


def tell_joke() -> str:
    return random.choice(JOKES) + " " + random_face()


def new_riddle() -> dict:
    """随机出一道谜语。返回 {"q": 谜面, "a": 谜底, "hint": 提示}。"""
    return random.choice(RIDDLES)


def check_answer(user_input: str, answer: str) -> bool:
    """判断猜谜答案是否正确（支持谜底包含在回答里，如"答案是月亮"）。"""
    u = (user_input or "").strip().lower().replace("答案是", "").strip()
    a = (answer or "").strip().lower()
    return bool(a) and (a in u or u in a)


def looks_like_new_command(text: str) -> bool:
    """判断输入是否是新命令（而非猜谜答案）。"""
    t = text.strip()
    return any(w in t for w in NEW_COMMAND_WORDS) or t.startswith(("帮我", "给我"))
