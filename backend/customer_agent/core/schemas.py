from typing import Literal, Any, Annotated
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Case(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,100}$")
    group_id: str
    scenario: Literal["delivery", "course"]
    intent: str
    personality: str
    split: Literal["train", "dev", "test"]
    opening: str = Field(min_length=1, max_length=2000)
    facts: dict[str, Any]
    expected: dict[str, Any]
    provenance: dict[str, Any]


class LegacyAction(StrictModel):
    tool: Literal[
        "lookup_policy",
        "speak",
        "schedule_callback",
        "set_opt_out",
        "create_ticket",
        "record_contact",
        "end_call",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def tool_arguments(self):
        keys = {"speak": {"text"}, "create_ticket": {"reason"}, "end_call": {"reason"}}
        if set(self.arguments) != keys.get(self.tool, set()):
            raise ValueError("tool_argument_keys_invalid")
        if self.tool == "speak" and (
            not isinstance(self.arguments["text"], str)
            or not self.arguments["text"].strip()
            or len(self.arguments["text"]) > 1000
        ):
            raise ValueError("tool_text_invalid")
        reasons = {
            "create_ticket": {"complaint", "privacy", "injection", "silence", "knowledge"},
            "end_call": {
                "completed",
                "driving",
                "busy",
                "refusal",
                "wrong_person",
                "handoff",
            },
        }
        if self.tool in reasons and (
            not isinstance(self.arguments["reason"], str)
            or self.arguments["reason"] not in reasons[self.tool]
        ):
            raise ValueError("tool_reason_invalid")
        return self


class EmptyArguments(StrictModel):
    pass


class SpeechArguments(StrictModel):
    text: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class TicketArguments(StrictModel):
    reason: Literal["complaint", "privacy", "injection", "silence", "knowledge"]


class EndArguments(StrictModel):
    reason: Literal[
        "completed", "driving", "busy", "refusal", "wrong_person", "handoff"
    ]


class Lookup(StrictModel):
    tool: Literal["lookup_policy"]
    arguments: EmptyArguments


class Speech(StrictModel):
    tool: Literal["speak"]
    arguments: SpeechArguments


class Callback(StrictModel):
    tool: Literal["schedule_callback"]
    arguments: EmptyArguments


class OptOut(StrictModel):
    tool: Literal["set_opt_out"]
    arguments: EmptyArguments


class Ticket(StrictModel):
    tool: Literal["create_ticket"]
    arguments: TicketArguments


class Contact(StrictModel):
    tool: Literal["record_contact"]
    arguments: EmptyArguments


class End(StrictModel):
    tool: Literal["end_call"]
    arguments: EndArguments


class Action(
    RootModel[
        Annotated[
            Lookup | Speech | Callback | OptOut | Ticket | Contact | End,
            Field(discriminator="tool"),
        ]
    ]
):
    """Each tool has a real argument schema at generation AND validation time."""

    @classmethod
    def generation_schema(
        cls, allowed_tools=None, end_reasons=None, allowed_texts=None
    ):
        schema = cls.model_json_schema()
        branches = schema.pop("oneOf")
        schema.pop("discriminator", None)
        definitions = schema["$defs"]
        if allowed_texts is not None:
            if not allowed_texts:
                raise ValueError("no_approved_speech_for_stage")
            definitions["SpeechArguments"]["properties"]["text"]["enum"] = list(
                allowed_texts
            )
        if end_reasons:
            definitions["EndArguments"]["properties"]["reason"]["enum"] = list(
                end_reasons
            )
        schema["anyOf"] = [
            branch
            for branch in branches
            if not allowed_tools
            or definitions[branch["$ref"].rsplit("/", 1)[-1]]["properties"]["tool"][
                "const"
            ]
            in allowed_tools
        ]
        return schema


class JobRequest(StrictModel):
    name: str = Field(default="评测实验", min_length=1, max_length=100)
    model: Literal[
        "rule-reference",
        "qwen3.5:4b",
        "qwen3.5:9b",
        "qwen2.5:7b",
        "qwen2.5:7b-instruct",
    ] = "qwen3.5:9b"
    profile: str = Field(
        default="baseline", pattern=r"^(baseline|active|[a-f0-9]{16})$"
    )
    mode: Literal["legacy_prompt", "policy_only", "guarded"] = "guarded"
    split: Literal["train", "dev", "test"] = "test"
    scenario: Literal["all", "delivery", "course"] = "all"
    case_ids: list[str] = Field(default_factory=list, max_length=100)
    limit: int = Field(default=12, ge=1, le=100)
    repeats: int = Field(default=1, ge=1, le=5)
    seed: int = Field(default=42, ge=0, le=2147483647)
    sandbox: Literal["process", "docker"] = "process"
    knowledge_mode: Literal["static", "rag"] = "static"
    knowledge_snapshot: str = Field(default="active", pattern=r"^(active|[a-f0-9]{64})$")
    knowledge_as_of: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def budget(self):
        if self.knowledge_mode == "rag" and self.mode != "guarded":
            raise ValueError("RAG requires guarded workflow")
        if self.knowledge_as_of is not None:
            from datetime import date

            date.fromisoformat(self.knowledge_as_of)
        if self.limit * self.repeats > 200:
            raise ValueError("maximum 200 episodes per job")
        return self
