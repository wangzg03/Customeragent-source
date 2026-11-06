"""Reviewed fictional-policy choices for constrained decoding, never a fallback.

The model actually generates one of these strings through Ollama JSON schema.
Candidate membership is also enforced in the worker. This measures a bounded
workflow system, not unconstrained language/model ability.
"""

try:
    from backend.customer_agent.domain.workflow import current_stage
except ImportError:
    from workflow import current_stage

VERSION = "fictional-policy-phrase-choices-1.0"
CONTRACT = "approved_speech_enum_v1"


def choices_for(state, stage=None):
    f = state["facts"]
    stage = stage or current_stage(state)
    common = {
        "connection_check": ["您现在能听清吗？", "请问您能听清我说话吗？"],
    }
    if state["scenario"] == "delivery":
        catalog = {
            "contract_notice": ["您的配送合同已生效。", "您好，配送合同已经生效。"],
            "availability": ["今天方便配送吗？", "请问您今天可以配送吗？"],
            "requirements": [
                f"单日合同当日至少完成{f['single_orders']}单。",
                f"单日合同当日最低要求{f['single_orders']}单。",
            ],
            "daily_target": [
                f"多日合同每天至少完成{f['daily_orders']}单。",
                f"多日合同每日最低{f['daily_orders']}单。",
            ],
            "contract_days": [
                f"多日合同需要连续{f['days']}天达标。",
                f"多日合同需连续{f['days']}天每天达标。",
            ],
            "reward_days": [
                f"奖励要求连续{f['reward_days']}天每天达标。",
                f"连续{f['reward_days']}天每天达标可获奖励。",
            ],
            "reward_daily": ["奖励要求每天达标。", "奖励需每日达标。"],
            "reward_amount": [
                f"每单额外奖励{f['reward']}元。",
                f"奖励为每单额外{f['reward']}元。",
            ],
            "exit_day": [
                f"退出须前一天{f['deadline']}点前在App取消。",
                f"前一天{f['deadline']}点前，请在App取消。",
            ],
            "exit_deadline": [
                f"退出须前一天{f['deadline']}点前在App取消。",
                f"前一天{f['deadline']}点前，请在App取消。",
            ],
            "exit_channel": ["请在App取消。", "取消请通过App操作。"],
            "safety": ["路上注意安全，祝您顺利。", "安全第一，请注意行车安全。"],
        }
    else:
        publishing = (
            ["请在服务商管理勾选低延迟并保存。", "在服务商管理中勾选低延迟并保存。"]
            if f["publish"] == "third_party"
            else ["请在Web发布页选择直播类型。", "在Web发布页选择所需直播类型。"]
        )
        catalog = {
            "identity": ["您好，您是机构负责人吗？", "请问您是否为机构负责人？"],
            "options_intro": [
                "发布页新增标准和低延迟选项。",
                "新增标准直播、低延迟直播选项。",
            ],
            "standard_named": [
                "可选标准直播和低延迟直播。",
                "新增标准直播、低延迟直播选项。",
            ],
            "low_named": ["还有低延迟直播选项。", "另一选项是低延迟直播。"],
            "standard_latency": [
                "标准直播5-10秒，费用低。",
                "标准延迟5-10秒，费用较低。",
            ],
            "standard_cost": ["标准直播费用低。", "标准直播费用较低。"],
            "low_latency": ["低延迟直播1-2秒，费用较高。", "低延迟1-2秒，费用更高。"],
            "low_cost": ["低延迟直播费用较高。", "低延迟直播费用更高。"],
            "publish_route": publishing,
            "publish_select": publishing,
            "publish_save": ["勾选低延迟后请保存设置。", "请保存低延迟设置。"],
            "fees": ["请检查学员端附加费用配置。", "请确认学员端附加费设置。"],
            "contact_consent": [
                "您同意通过企业微信联系吗？",
                "可以通过企微继续联系您吗？",
            ],
            "price_dependency": [
                "具体价格因套餐而异，可发资料。",
                "价格视套餐而定，可提供资料。",
            ],
            "price_materials": ["可以发送套餐资料供您确认。", "可提供套餐资料。"],
            "no_discount": ["目前没有优惠活动。", "目前暂无优惠。"],
        }
    return list((common | catalog).get(stage, []))
