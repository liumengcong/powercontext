"""Deterministic game rules. The LLM narrates; it cannot change saved facts."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256

CARDS = [
    {"title": "情绪安检", "scene": "安检门坚持认为失物有三公斤委屈，拒绝放行。",
     "options": {"A": ["给它开一张情绪病假条", 2, "情绪病假条"], "B": ["让它和安检门互相道歉", 1, "安检门的道歉信"], "C": ["把委屈折成纸飞机寄走", 0, "折叠的委屈"]}},
    {"title": "时间称重", "scene": "电子秤称出了负七分钟，窗口要求你补齐时间差。",
     "options": {"A": ["预支明天的一个哈欠", 1, "预支哈欠凭证"], "B": ["用等电梯的七分钟抵账", 2, "时间抵用券"], "C": ["申请把时钟倒挂", 0, "倒挂时钟许可证"]}},
    {"title": "身份复核", "scene": "失物突然声称自己才是主人，而你只是它弄丢的人类。",
     "options": {"A": ["与失物协商轮流当主人", 2, "共同监护协议"], "B": ["请柜员检查失物的工牌", 0, "失物临时工牌"], "C": ["承认自己确实需要被认领", 1, "人类认领申请表"]}},
    {"title": "语言补税", "scene": "收音机宣布：所有形容词都要缴税，越离谱税率越高。",
     "options": {"A": ["改用三个拟声词说明情况", 1, "免税拟声词"], "B": ["证明这是一件公益失物", 2, "公益免税章"], "C": ["把税单也报失", 0, "失踪的税单"]}},
    {"title": "梦境检疫", "scene": "梦境检疫员发现失物正在偷做你的梦，要求现场处理。",
     "options": {"A": ["为梦境加一段片尾字幕", 1, "梦境署名权"], "B": ["和失物签订梦境共享协议", 2, "共享梦境协议"], "C": ["让检疫员客串梦里的路人", 0, "路人乙通行证"]}},
    {"title": "存在证明", "scene": "打印机只能打印不存在的东西，恰好轮到你的失物。",
     "options": {"A": ["提交失物造成的具体后果", 2, "间接存在证明"], "B": ["请打印机打印自己的辞职信", 0, "打印机辞职信"], "C": ["用空白纸给失物画张肖像", 1, "空白肖像"]}},
]

TRAITS = ["每听见一次‘好的’就打嗝", "坚信自己是一颗带薪休假的土豆", "只在星期八愿意配合", "会把所有叹号折成小船", "想申请成为你的编外同事"]


class RuleError(ValueError):
    pass


def fresh_state() -> dict:
    return {"schema": 1, "episode": 0, "current": None, "album": [], "history": [], "requests": []}


def card_for(current: dict) -> dict:
    return CARDS[current["deck"][min(current["round"], 2)]]


def transition(state: dict, action: str, message: str, player_hash: str) -> tuple[dict, dict, bool]:
    next_state = deepcopy(state)
    current = next_state["current"]
    event = {"action": action, "message": message, "effect": "", "callback": ""}
    mutated = action in ("寄存", "A", "B", "C", "重开")
    if action == "寄存":
        if current is not None:
            raise RuleError("已有失物档案。请继续 A/B/C，或选择‘重开’后再寄存。")
        if not message.strip() or len(message) > 120:
            raise RuleError("寄存时请用 1—120 字写出一件离谱失物，例如‘会打嗝的月亮’。")
        next_state["episode"] += 1
        seed = sha256((message + str(next_state["episode"])).encode()).digest()
        deck = sorted(range(len(CARDS)), key=lambda i: (seed[i], i))[:3]
        current = {"item": message, "trait": TRAITS[seed[8] % len(TRAITS)], "round": 0,
                   "score": 0, "deck": deck, "stamps": [], "choices": [], "ending": "",
                   "receipt": "LP-" + sha256((player_hash + ':' + str(next_state["episode"])).encode()).hexdigest()[:10].upper()}
        next_state["current"] = current
        event["effect"] = "已登记失物，进入第一关。"
    elif action in ("A", "B", "C"):
        if current is None:
            raise RuleError("还没有失物。先选择‘寄存’并写出物品。")
        if current["ending"]:
            raise RuleError("这件失物已经结案。可以‘回访’看彩蛋，或‘重开’寄存新物品。")
        card = card_for(current)
        text, points, stamp = card["options"][action]
        current["choices"].append({"stage": card["title"], "choice": action, "text": text, "note": message})
        current["stamps"].append(stamp)
        current["score"] += points
        current["round"] += 1
        event["effect"] = f"你选择了“{text}”，获得“{stamp}”，默契值 +{points}。"
        if current["round"] == 3:
            current["ending"] = ("编外管理员：失物决定留下来和你一起上班" if current["score"] >= 5 else
                                 "共同冒险人：你和失物共同获得无限期探险许可" if current["score"] >= 3 else
                                 "反向认领：失物成功把你领回了家")
            next_state["album"].append({"item": current["item"], "ending": current["ending"], "receipt": current["receipt"]})
            next_state["album"] = next_state["album"][-20:]
            event["effect"] += " 三关完成，已生成结局。"
    elif action == "重开":
        next_state["current"] = None
        current = None
        event["effect"] = "当前失物已归档离场；收藏册和历史记忆保留。可以重新寄存。"
    elif action in ("回访", "档案"):
        event["effect"] = "从 PowerContext 读取已有存档；本操作不推进关卡。"
    else:
        raise RuleError("操作只能选择：寄存、A、B、C、回访、档案、重开。")

    # A callback is drawn from real historical input and choices, never invented.
    if current and current["choices"]:
        old = current["choices"][-1]
        event["callback"] = f"你上次在‘{old['stage']}’选择了‘{old['text']}’。" + (f"当时还说：{old['note']}" if old["note"] else "")
    elif next_state["album"]:
        event["callback"] = "收藏册里还有：" + next_state["album"][-1]["item"]
    if mutated:
        next_state["history"] = (next_state["history"] + [event])[-20:]
    return next_state, event, mutated


def presentation(state: dict, event: dict) -> dict:
    current = state["current"]
    data = {"event": event, "item": None, "card": None, "album": state["album"][-5:],
            "progress": "尚未寄存", "ending": "", "stamps": [], "score": 0, "receipt": "未生成"}
    if current:
        data.update(item=current["item"], trait=current["trait"], progress=f"已完成 {current['round']}/3 关",
                    ending=current["ending"], score=current["score"], stamps=current["stamps"], receipt=current["receipt"],
                    choices=current["choices"])
        if not current["ending"]:
            data["card"] = card_for(current)
    return data


def fallback_narration(context: dict) -> str:
    # Used only in the clearly labelled no-model diagnostic workflow.
    item = context["item"] or "尚未入库的脑洞"
    lines = [f"柜员把眼镜往上推了推：关于“{item}”，本处已经认真地离谱了起来。", context["event"]["effect"]]
    if context["event"]["callback"]:
        lines.append("老规矩，我可记着呢：" + context["event"]["callback"])
    if context.get("trait"):
        lines.append("顺便提醒，这件失物" + context["trait"] + "。")
    return "\n\n".join(lines)


def canonical_receipt(context: dict) -> str:
    lines = ["### 失物档案", f"物品：{context['item'] or '尚未寄存'}", f"进度：{context['progress']}｜默契值：{context['score']}/6",
             "纪念章：" + ("、".join(context["stamps"]) or "暂无"), "档案号：" + context["receipt"]]
    if context["ending"]:
        lines += ["结局：" + context["ending"], "下一步：回访看彩蛋；档案看收藏；重开后可寄存新物品。"]
    elif context["card"]:
        card = context["card"]
        lines += ["", "### 当前窗口：" + card["title"], card["scene"]]
        lines += [f"{key}：{value[0]}" for key, value in card["options"].items()]
        lines += ["下一次运行请选择 A/B/C；message 可以补一句你的脑洞。"]
    else:
        lines += ["下一步：选择‘寄存’，message 填一件不可能丢失的东西。"]
    if context["album"]:
        lines += ["", "### 收藏册"] + [f"{x['item']} → {x['ending']}" for x in context["album"]]
    return "\n\n".join(lines)
