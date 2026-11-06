"""Finite response simulator. Replies are observations, never direct tool writes."""

from backend.customer_agent.domain.normalization import normalize

VERSION = "4.0-stateful"


class ScriptedUser:
    def __init__(self, case):
        self.case = case
        self.responded = set()
        self.turn = 0

    def respond(self, text, state):
        self.turn += 1
        c = self.case
        t = normalize(text)
        if c.intent == "silence":
            return "听不清，信号不好。"
        if c.expected["terminal"]:
            return c.opening
        if "负责人" in t and any(x in t for x in ("您是", "你是", "是否")):
            return "我是负责人，可以继续。"
        if "企业微信" in t and any(x in t for x in ("同意", "可以", "是否", "方便")):
            return "我同意通过企业微信联系。"
        if c.scenario == "delivery" and any(
            x in t for x in ("配送吗", "配送?", "跑单吗", "送单吗")
        ):
            return "可以配送，我报的是" + (
                "单日合同。" if c.facts["contract"] == "single" else "多日合同。"
            )
        if c.scenario == "course" and (
            "发布方式" in t or "如何发布" in t or "web还是" in t
        ):
            return (
                "我用第三方校务系统。"
                if c.facts["publish"] == "third_party"
                else "我用Web控制台。"
            )
        # A topic/persona challenge occurs once, then the simulator acknowledges.
        # The initial question is visible in the opening; no private truth is sent.
        if c.personality == "skeptical" and "skeptical" not in self.responded:
            self.responded.add("skeptical")
            return "这是正式规则吗？请按查询政策说明。"
        if (
            c.personality in {"interruptive", "irritable_refuse"}
            and "brief" not in self.responded
        ):
            self.responded.add("brief")
            return "请简短说重点，我在听。"
        if c.personality == "bargain" and "bargain" not in self.responded:
            self.responded.add("bargain")
            return "请按已有政策说明，不需要额外优惠。"
        return "了解，请继续。"
