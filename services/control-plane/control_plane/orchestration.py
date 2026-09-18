import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone

from .inference import ResponsesClient
from .models import (
    ActionKind,
    AppDefaults,
    CallbackAction,
    EventKind,
    RuntimeEvent,
    SessionRegistration,
)
from .repository import Repository


class Orchestrator:
    def __init__(self, repo: Repository, inference: ResponsesClient):
        self.repo = repo
        self.inference = inference

    # Input: An app ID, registered session, and unique message event.
    # Output: Number of callback actions persisted for replay-safe delivery.
    async def handle_message(
        self, app_id: str, session: SessionRegistration, event: RuntimeEvent
    ) -> int:
        if event.type != EventKind.message_created or not event.text:
            return 0
        self.repo.add_message(
            app_id,
            session.session_id,
            event.channel,
            event.sender_id,
            "user",
            event.text,
            event.event_id,
        )
        app = self.repo.get_app(app_id)
        if not app:
            return 0
        group_policy = self.repo.group_policy(app_id, session.group_id)
        updates = (
            group_policy.model_dump(
                exclude={"group_id", "personality_assignments"},
                exclude_none=True,
            )
            if group_policy
            else {}
        )
        defaults = AppDefaults.model_validate({
            **app.defaults.model_dump(),
            **updates,
        })
        channel_context = session.channel_context.get(event.channel)
        if channel_context:
            channel_updates = {
                "response_probability": channel_context.response_probability,
                "max_respondents": channel_context.max_respondents,
            }
            defaults = defaults.model_copy(update={
                key: value
                for key, value in channel_updates.items()
                if value is not None
            })
        slots = self._select_respondents(session, event, defaults)
        count = 0
        for index, slot in enumerate(slots):
            transcript = self._transcript(
                app_id,
                session,
                event.channel,
                defaults.model.context_messages,
                slot.participant_id,
            )
            personality_id = (
                group_policy.personality_assignments.get(slot.participant_id)
                if group_policy
                else None
            ) or slot.personality_id
            prompt = self._prompt(
                app_id,
                session,
                slot,
                defaults.system_prompt,
                personality_id,
                event.channel,
            )
            response = await self.inference.respond(prompt, transcript, defaults.model)
            aliases = [
                value
                for participant in session.participants
                for value in (
                    participant.participant_id,
                    str(participant.display.get("name", "")),
                )
                if value
            ]
            text = self._normalize(response, defaults.model.max_words, aliases)
            self.repo.add_message(
                app_id,
                session.session_id,
                event.channel,
                slot.participant_id,
                "assistant",
                text,
            )
            count += self._schedule_actions(
                app_id, event, slot.participant_id, text, index, app, defaults.timing
            )
            self.repo.audit(
                app_id,
                session.session_id,
                "response.generated",
                {
                    "sourceEventId": event.event_id,
                    "participantId": slot.participant_id,
                    "characters": len(text),
                },
            )
        return count

    # Input: Session roster, source event, and app response defaults.
    # Output: Mention-first respondents ranked by relevant private context.
    def _select_respondents(self, session, event, defaults):
        candidates = [
            slot
            for slot in session.participants
            if slot.role.value == "virtual" and slot.participant_id != event.sender_id
        ]
        mentioned = set(event.metadata.get("mentionedParticipantIds", []))
        direct = [slot for slot in candidates if slot.participant_id in mentioned]
        if direct:
            return direct[: defaults.max_respondents]
        query = set(re.findall(r"[a-z0-9]+", (event.text or "").lower()))
        ranked = sorted(
            candidates,
            key=lambda slot: (
                -len(
                    query
                    & set(
                        re.findall(
                            r"[a-z0-9]+",
                            json.dumps(slot.private_context).lower(),
                        )
                    )
                ),
                hashlib.sha256(
                    f"{event.event_id}:{slot.participant_id}".encode()
                ).digest(),
            ),
        )
        selected = []
        for slot in ranked:
            score = (
                int.from_bytes(
                    hashlib.sha256(
                        f"select:{event.event_id}:{slot.participant_id}".encode()
                    ).digest()[:8],
                    "big",
                )
                / 2**64
            )
            if score < defaults.response_probability:
                selected.append(slot)
        return selected[: defaults.max_respondents]

    # Input: Raw model text and the configured word limit.
    # Output: One short plain-text message safe for chat delivery.
    def _normalize(self, value: str, max_words: int, aliases: list[str]) -> str:
        prose = re.sub(r"\s+", " ", re.sub(r"^[\s#*-]+", "", value)).strip()
        names = "|".join(re.escape(alias) for alias in aliases if alias)
        if names:
            prose = re.sub(
                rf"^(?:(?:{names})\s*:\s*)+",
                "",
                prose,
                flags=re.IGNORECASE,
            )
        sentence = re.split(r"(?<=[.!?])\s+", prose, maxsplit=1)[0]
        return " ".join(sentence.split()[:max_words])

    # Input: Strict app/session/channel boundary and context limit.
    # Output: Display-name transcript from one participant's point of view.
    def _transcript(
        self,
        app_id: str,
        session: SessionRegistration,
        channel: str,
        limit: int,
        participant_id: str,
    ) -> list[dict[str, str]]:
        rows = self.repo.transcript(app_id, session.session_id, channel, limit)
        names = {
            slot.participant_id: str(
                slot.display.get("name", slot.participant_id)
            )
            for slot in session.participants
        }
        return [
            {
                "role": (
                    "assistant"
                    if row["sender_id"] == participant_id
                    else "user"
                ),
                "content": f"{names.get(row['sender_id'], 'Participant')}: {row['text']}",
            }
            for row in rows
        ]

    # Input: Session context, one virtual slot, and the app's base prompt.
    # Output: Private participant instructions not exposed to callbacks.
    def _prompt(
        self,
        app_id,
        session,
        slot,
        base_prompt: str,
        personality_id: str | None,
        channel: str,
    ) -> str:
        personality = self.repo.personality_prompt(app_id, personality_id)
        channel_context = session.channel_context.get(channel)
        parts = [
            base_prompt,
            f"Your participant ID is {slot.participant_id}.",
            f"Current channel: {channel}.",
            "Return only the message text. Do not copy or add any sender label, name, ID, or colon prefix.",
        ]
        if channel_context and channel_context.instructions:
            parts.append(f"Channel instructions: {channel_context.instructions}")
        if not channel_context or channel_context.include_shared_context:
            parts.append(
                f"Shared context: {json.dumps(session.shared_context, sort_keys=True)}"
            )
        if not channel_context or channel_context.include_private_context:
            parts.append(
                f"Your private context: {json.dumps(slot.private_context, sort_keys=True)}"
            )
        if personality and (not channel_context or channel_context.include_personality):
            parts.append(f"Personality: {personality}")
        return "\n".join(parts)

    # Input: Generated text, respondent position, and timing policy.
    # Output: Typing/message callback actions with KLM and stagger due times.
    def _schedule_actions(
        self, app_id, event, participant_id: str, text: str, index: int, app, policy
    ) -> int:
        word_ms = math.ceil(len(text.split()) / policy.words_per_minute * 60_000)
        delay_ms = min(
            policy.max_delay_ms,
            max(policy.min_delay_ms, policy.intercept_ms + word_ms),
        )
        now = datetime.now(timezone.utc)
        due = now + timedelta(milliseconds=delay_ms + index * policy.stagger_ms)
        actions = []
        if app.capabilities.typing_indicators:
            actions.append(
                (
                    self._action(
                        app_id,
                        event,
                        participant_id,
                        ActionKind.typing_set,
                    ),
                    now + timedelta(milliseconds=index * policy.stagger_ms),
                )
            )
        actions.append(
            (
                self._action(
                    app_id,
                    event,
                    participant_id,
                    ActionKind.message_deliver,
                    text,
                ),
                due,
            )
        )
        if app.capabilities.typing_indicators:
            actions.append(
                (
                    self._action(
                        app_id,
                        event,
                        participant_id,
                        ActionKind.typing_clear,
                    ),
                    due,
                )
            )
        for action, action_due in actions:
            self.repo.schedule(action, action_due)
        return len(actions)

    def _action(
        self,
        app_id: str,
        event: RuntimeEvent,
        participant_id: str,
        kind: ActionKind,
        text: str | None = None,
    ) -> CallbackAction:
        stable = f"{event.event_id}:{participant_id}:{kind.value}"
        action_id = str(uuid.uuid5(uuid.NAMESPACE_URL, stable))
        return CallbackAction(
            actionId=action_id,
            appId=app_id,
            sessionId=event.session_id,
            channel=event.channel,
            type=kind,
            participantId=participant_id,
            text=text,
            sourceEventId=event.event_id,
        )
