from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class Capabilities(ApiModel):
    typing_indicators: bool = Field(False, alias="typingIndicators")
    cancel_actions: bool = Field(True, alias="cancelActions")


class TimingPolicy(ApiModel):
    words_per_minute: int = Field(180, ge=30, le=1000, alias="wordsPerMinute")
    intercept_ms: int = Field(350, ge=0, le=60_000, alias="interceptMs")
    min_delay_ms: int = Field(300, ge=0, le=60_000, alias="minDelayMs")
    max_delay_ms: int = Field(12_000, ge=0, le=120_000, alias="maxDelayMs")
    stagger_ms: int = Field(800, ge=0, le=60_000, alias="staggerMs")


class ModelPolicy(ApiModel):
    temperature: float = Field(0.7, ge=0, le=2)
    max_output_tokens: int = Field(300, ge=1, le=4096, alias="maxOutputTokens")
    context_messages: int = Field(40, ge=1, le=500, alias="contextMessages")
    max_words: int = Field(24, ge=1, le=500, alias="maxWords")


class AppDefaults(ApiModel):
    system_prompt: str = Field(
        "Participate naturally and answer only as your assigned participant.",
        alias="systemPrompt",
    )
    response_probability: float = Field(1.0, ge=0, le=1, alias="responseProbability")
    max_respondents: int = Field(1, ge=0, le=20, alias="maxRespondents")
    timing: TimingPolicy = Field(default_factory=TimingPolicy)
    model: ModelPolicy = Field(default_factory=ModelPolicy)


class AppRegistration(ApiModel):
    app_id: str = Field(min_length=1, max_length=100, alias="appId")
    display_name: str = Field(min_length=1, max_length=200, alias="displayName")
    callback_url: HttpUrl = Field(alias="callbackUrl")
    bearer_token: str = Field(min_length=24, alias="bearerToken")
    webhook_secret: str = Field(min_length=24, alias="webhookSecret")
    capabilities: Capabilities = Field(default_factory=Capabilities)
    defaults: AppDefaults = Field(default_factory=AppDefaults)


class ParticipantRole(str, Enum):
    human = "human"
    virtual = "virtual"


class ParticipantSlot(ApiModel):
    participant_id: str = Field(min_length=1, max_length=100, alias="participantId")
    role: ParticipantRole
    display: dict[str, Any] = Field(default_factory=dict)
    private_context: dict[str, Any] = Field(
        default_factory=dict, alias="privateContext"
    )
    personality_id: str | None = Field(None, alias="personalityId")


class ChannelContext(ApiModel):
    instructions: str = ""
    include_shared_context: bool = Field(True, alias="includeSharedContext")
    include_private_context: bool = Field(True, alias="includePrivateContext")
    include_personality: bool = Field(True, alias="includePersonality")
    response_probability: float | None = Field(
        None, ge=0, le=1, alias="responseProbability"
    )
    max_respondents: int | None = Field(None, ge=0, le=20, alias="maxRespondents")


class SessionRegistration(ApiModel):
    session_id: str = Field(min_length=1, max_length=150, alias="sessionId")
    group_id: str | None = Field(None, max_length=150, alias="groupId")
    channels: list[str] = Field(min_length=1)
    channel_context: dict[str, ChannelContext] = Field(
        default_factory=dict, alias="channelContext"
    )
    shared_context: dict[str, Any] = Field(default_factory=dict, alias="sharedContext")
    participants: list[ParticipantSlot] = Field(min_length=1)

    @field_validator("channels")
    @classmethod
    def unique_channels(cls, values: list[str]) -> list[str]:
        if not all(values) or len(values) != len(set(values)):
            raise ValueError("channels must be non-empty and unique")
        return values

    @field_validator("participants")
    @classmethod
    def unique_participants(
        cls, values: list[ParticipantSlot]
    ) -> list[ParticipantSlot]:
        ids = [slot.participant_id for slot in values]
        if len(ids) != len(set(ids)):
            raise ValueError("participant IDs must be unique")
        return values


class EventKind(str, Enum):
    session_started = "session.started"
    session_ended = "session.ended"
    channel_opened = "channel.opened"
    channel_closed = "channel.closed"
    message_created = "message.created"


class RuntimeEvent(ApiModel):
    event_id: str = Field(min_length=1, max_length=200, alias="eventId")
    session_id: str = Field(min_length=1, max_length=150, alias="sessionId")
    channel: str
    type: EventKind
    sender_id: str | None = Field(None, alias="senderId")
    text: str | None = Field(None, max_length=50_000)
    occurred_at: datetime = Field(default_factory=utc_now, alias="occurredAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def message_has_text(cls, value: str | None, info: Any) -> str | None:
        if info.data.get("type") == EventKind.message_created and not value:
            raise ValueError("message.created requires text")
        return value


class ActionKind(str, Enum):
    typing_set = "typing.set"
    typing_clear = "typing.clear"
    message_deliver = "message.deliver"
    actions_cancel = "actions.cancel"
    session_end = "session.end"


class CallbackAction(ApiModel):
    action_id: str = Field(alias="actionId")
    app_id: str = Field(alias="appId")
    session_id: str = Field(alias="sessionId")
    channel: str
    type: ActionKind
    participant_id: str | None = Field(None, alias="participantId")
    text: str | None = None
    source_event_id: str | None = Field(None, alias="sourceEventId")
    created_at: datetime = Field(default_factory=utc_now, alias="createdAt")


class PersonalityTemplate(ApiModel):
    personality_id: str = Field(min_length=1, max_length=100, alias="personalityId")
    name: str = Field(min_length=1, max_length=150)
    prompt: str = Field(min_length=1, max_length=20_000)


class GroupPolicy(ApiModel):
    group_id: str = Field(min_length=1, max_length=150, alias="groupId")
    system_prompt: str | None = Field(None, max_length=20_000, alias="systemPrompt")
    response_probability: float | None = Field(
        None, ge=0, le=1, alias="responseProbability"
    )
    max_respondents: int | None = Field(None, ge=0, le=20, alias="maxRespondents")
    personality_assignments: dict[str, str] = Field(
        default_factory=dict, alias="personalityAssignments"
    )
    timing: TimingPolicy | None = None
    model: ModelPolicy | None = None


class EventReceipt(ApiModel):
    event_id: str = Field(alias="eventId")
    accepted: bool
    duplicate: bool = False
    scheduled_actions: int = Field(0, alias="scheduledActions")


class StoredApp(ApiModel):
    app_id: str = Field(alias="appId")
    display_name: str = Field(alias="displayName")
    callback_url: str = Field(alias="callbackUrl")
    capabilities: Capabilities
    defaults: AppDefaults


class Health(ApiModel):
    status: Literal["ok"] = "ok"
    version: str = "0.1.0"
