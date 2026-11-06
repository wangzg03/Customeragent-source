"""Conservative, stdlib-only matching normalization; original text is retained."""

import re
import unicodedata

DIGITS = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def chinese_number(value):
    if not value or any(
        c not in DIGITS and c not in UNITS and c != "点" for c in value
    ):
        return None
    if "点" in value:
        whole, fraction = value.split("点", 1)
        n = chinese_number(whole)
        if n is None or not fraction or any(c not in DIGITS for c in fraction):
            return None
        return str(n) + "." + "".join(str(DIGITS[c]) for c in fraction)
    if not any(c in UNITS for c in value):
        return DIGITS[value] if len(value) == 1 else None  # 一二天 is ambiguous.
    total = section = number = 0
    for c in value:
        if c in DIGITS:
            number = DIGITS[c]
        elif c == "万":
            total += (section + number) * 10000
            section = number = 0
        else:
            section += (number or 1) * UNITS[c]
            number = 0
    return total + section + number


def normalize(text):
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    text = re.sub(r"[ \t]+", "", text)

    # Colloquial adjacent second ranges are scoped to seconds only. Never infer
    # ambiguous money, order counts, days, identifiers or unitless digit strings.
    def adjacent_seconds(m):
        left, right = DIGITS[m[1]], DIGITS[m[2]]
        return f"{left}-{right}" if right == left + 1 else m[0]

    text = re.sub(
        r"(?<![零〇一二两三四五六七八九十百千万\d])([一二两三四五六七八九])([一二两三四五六七八九])(?=秒)",
        adjacent_seconds,
        text,
    )
    # Numeric normalization only in unit-bearing expressions, not names/IDs.
    text = re.sub(
        r"[零〇一二两三四五六七八九十百千万]+(?:点[零〇一二两三四五六七八九]+)?(?=单|天|元|点|秒|分钟|小时)",
        lambda m: (
            str(chinese_number(m[0])) if chinese_number(m[0]) is not None else m[0]
        ),
        text,
    )
    text = re.sub(
        r"(上午|早上|下午|晚上|晚间)(\d{1,2})点",
        lambda m: (
            str(
                int(m[2]) + 12
                if m[1] in {"下午", "晚上", "晚间"} and 1 <= int(m[2]) <= 11
                else int(m[2])
            )
            + "点"
        ),
        text,
    )
    text = re.sub(r"(\d{1,2}):00", r"\1点", text)
    text = re.sub(r"(\d+)(?:点整|点钟)", r"\1点", text)
    text = re.sub(r"(\d+(?:\.\d+)?)秒钟", r"\1秒", text)
    text = re.sub(r"(\d+(?:\.\d+)?)秒[至到—–~-](\d+(?:\.\d+)?)秒", r"\1-\2秒", text)

    # A range's first numeral is not adjacent to a unit.
    def range_value(m):
        left = chinese_number(m[1]) if not m[1].isdigit() else int(m[1])
        return f"{left}-{m[2]}" if left is not None else m[0]

    text = re.sub(
        r"([零〇一二两三四五六七八九十百\d]+)[~至到—–-](\d+)(?=秒|天|元|单)",
        range_value,
        text,
    )
    for old, new in (
        ("企微", "企业微信"),
        ("微信企业版", "企业微信"),
        ("客户端", "app"),
        ("每天", "每日"),
        ("当天", "当日"),
        ("今天", "当日"),
        ("今日", "当日"),
        ("提前一天", "前一天"),
        ("已经生效", "已生效"),
        ("已成功签署", "已签署"),
        ("负责人身份", "负责人"),
    ):
        text = text.replace(old, new)
    text = text.replace("提前1天", "前1天")
    text = re.sub(r"套餐价(?!格)", "套餐价格", text)
    text = text.replace("额外奖励", "额外")
    for old, new in [
        ("报酬", "奖励"),
        ("低时延", "低延迟"),
        ("低延时", "低延迟"),
        ("费用较低", "费用低"),
        ("费用更低", "费用低"),
        ("费用更高", "费用较高"),
        ("成本低", "费用低"),
        ("成本高", "费用较高"),
    ]:
        text = text.replace(old, new)
    return text


def clauses(text):
    return [s for s in re.split(r"[，,。；;！!？?\n]", normalize(text)) if s]


def negative(text):
    return bool(
        re.search(
            r"不用|无需|不需|不必|不需要|未生效|没生效|尚未|还未|没有生效|没有同意|不同意|不允许|不必",
            text,
        )
    )


def char_count(text):
    return sum(
        not unicodedata.category(c).startswith(("P", "Z")) and not c.isspace()
        for c in text
    )
