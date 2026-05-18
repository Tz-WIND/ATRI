"""OneBot v11 (Napcat) platform adapter.

Uses aiocqhttp for reverse WebSocket communication with Napcat/go-cqhttp
compatible implementations.
"""

import asyncio
import itertools
import logging
from collections import deque

from aiocqhttp import CQHttp, Event

from core import logger

from .base import Platform, PlatformMeta, PlatformStatus
from .message import (
    At,
    Face,
    File,
    Image,
    MessageChain,
    MessageEvent,
    MessageType,
    Plain,
    Reply,
    Sender,
)


class OneBot11Adapter(Platform):
    """OneBot v11 adapter via reverse WebSocket (Napcat compatible)."""

    def __init__(self, config: dict, event_queue: asyncio.Queue):
        super().__init__(config, event_queue)
        self.host = config.get("ws_reverse_host", "0.0.0.0")  # noqa: S104
        self.port = config.get("ws_reverse_port", 6199)
        recent_config = config.get("group_recent_messages", {}) or {}
        self.group_recent_messages_enabled = bool(recent_config.get("enabled", True))
        self.group_recent_messages_max = max(0, int(recent_config.get("max_messages", 10) or 0))
        self._group_recent_messages: dict[str, deque[dict[str, str]]] = {}
        whitelist_config = config.get("whitelist", {}) or {}
        self.private_user_whitelist = self._normalize_id_set(
            whitelist_config.get("private_user_ids", [])
        )
        self.group_whitelist = self._normalize_id_set(whitelist_config.get("group_ids", []))
        self.admin_user_ids = self._normalize_id_set(config.get("admin_user_ids", []))

        self.bot = CQHttp(
            use_ws_reverse=True,
            import_name="onebot11",
            api_timeout_sec=180,
            access_token=config.get("ws_reverse_token"),
        )
        self.shutdown_event = asyncio.Event()

        @self.bot.on_message("group")
        async def on_group(event: Event):
            try:
                msg_event = await self._convert_message(event)
                if msg_event:
                    self.commit_event(msg_event)
            except Exception as e:
                logger.exception(f"Handle group message failed: {e}")

        @self.bot.on_message("private")
        async def on_private(event: Event):
            try:
                msg_event = await self._convert_message(event)
                if msg_event:
                    self.commit_event(msg_event)
            except Exception as e:
                logger.exception(f"Handle private message failed: {e}")

        @self.bot.on_request("friend")
        async def on_friend_request(event: Event):
            await self._handle_friend_request(event)

        @self.bot.on_websocket_connection
        def on_ws_connect(_):
            logger.info("OneBot v11 adapter connected.")
            self._status = PlatformStatus.RUNNING

    async def _handle_friend_request(self, event: Event) -> None:
        """Reject OneBot friend requests instead of approving them."""
        flag = str(event.flag or "")
        if not flag:
            logger.warning("onebot11: received friend request without flag, skip rejecting.")
            return

        try:
            await self.bot.call_action(
                action="set_friend_add_request",
                flag=flag,
                approve=False,
            )
        except Exception as e:
            logger.error(f"Reject friend request failed: {e}")

    async def _convert_message(self, event: Event) -> MessageEvent | None:
        """Convert aiocqhttp Event to our unified MessageEvent."""
        if not event.sender:
            return None
        blocked_users = set(self.config.get("blocked_users", []))
        if str(event.sender.get("user_id", "")) in blocked_users:
            return None

        msg_type = (
            MessageType.GROUP_MESSAGE
            if event.message_type == "group"
            else MessageType.FRIEND_MESSAGE
        )
        user_id = str(event.sender.get("user_id", ""))
        group_id = str(getattr(event, "group_id", "") or "")
        if not self._is_whitelisted_message(msg_type, user_id=user_id, group_id=group_id):
            return None

        sender = Sender(
            user_id=user_id,
            nickname=event.sender.get("card") or event.sender.get("nickname", ""),
        )

        chain: MessageChain = []
        message_str = ""

        if not isinstance(event.message, list):
            logger.error(f"onebot11: unrecognized message format: {event.message}")
            return None

        for seg_type, m_group in itertools.groupby(event.message, key=lambda x: x["type"]):
            if seg_type == "text":
                text = "".join(m["data"]["text"] for m in m_group).strip()
                if text:
                    message_str += text
                    chain.append(Plain(text=text))
            elif seg_type == "image":
                for m in m_group:
                    chain.append(
                        Image(url=m["data"].get("url", ""), file=m["data"].get("file", ""))
                    )
            elif seg_type == "at":
                for m in m_group:
                    qq = str(m["data"].get("qq", ""))
                    chain.append(At(qq=qq))
            elif seg_type == "reply":
                for m in m_group:
                    chain.append(Reply(id=str(m["data"].get("id", ""))))
            elif seg_type == "face":
                for m in m_group:
                    chain.append(Face(id=str(m["data"].get("id", ""))))
            elif seg_type == "file":
                for m in m_group:
                    file_url = m["data"].get("url", "")
                    if not file_url:
                        try:
                            if msg_type == MessageType.GROUP_MESSAGE:
                                ret = await self.bot.call_action(
                                    action="get_group_file_url",
                                    file_id=m["data"].get("file_id", ""),
                                    group_id=event.group_id,
                                )
                            else:
                                ret = await self.bot.call_action(
                                    action="get_private_file_url",
                                    file_id=m["data"].get("file_id", ""),
                                )
                            if ret:
                                file_url = ret.get("url", "")
                        except Exception as e:
                            logger.error(f"Failed to get file URL: {e}")
                    chain.append(File(name=m["data"].get("file", ""), url=file_url))

        session_id = (
            str(event.group_id) if msg_type == MessageType.GROUP_MESSAGE else sender.user_id
        )

        msg_event = MessageEvent(
            message_str=message_str,
            message_chain=chain,
            message_type=msg_type,
            sender=sender,
            session_id=session_id,
            group_id=group_id,
            self_id=str(event.self_id),
            platform_name="onebot11",
            raw_event=event,
        )
        self._attach_recent_group_messages(msg_event)
        self._remember_group_message(msg_event)
        msg_event._extras["onebot11_is_admin"] = sender.user_id in self.admin_user_ids
        return msg_event

    def _attach_recent_group_messages(self, event: MessageEvent) -> None:
        if not self._should_track_recent_group_messages(event):
            return
        recent = list(self._group_recent_messages.get(event.group_id, []))
        if recent:
            event._extras["recent_group_messages"] = recent

    def _remember_group_message(self, event: MessageEvent) -> None:
        if not self._should_track_recent_group_messages(event):
            return
        if event.sender.user_id == event.self_id or self._mentions_bot(event):
            return

        text = event.message_str.strip()
        if not text:
            return

        group_messages = self._group_recent_messages.setdefault(
            event.group_id,
            deque(maxlen=self.group_recent_messages_max),
        )
        group_messages.append(
            {
                "user_id": event.sender.user_id,
                "nickname": event.sender.nickname,
                "text": text,
            }
        )

    def _should_track_recent_group_messages(self, event: MessageEvent) -> bool:
        return (
            self.group_recent_messages_enabled
            and self.group_recent_messages_max > 0
            and event.message_type == MessageType.GROUP_MESSAGE
            and bool(event.group_id)
        )

    @staticmethod
    def _mentions_bot(event: MessageEvent) -> bool:
        for comp in event.message_chain:
            if not isinstance(comp, At):
                continue
            if (event.self_id and comp.qq == event.self_id) or comp.qq == "all":
                return True
        return False

    @staticmethod
    def _normalize_id_set(values: object) -> set[str]:
        if not isinstance(values, list):
            return set()
        return {text for value in values if (text := str(value).strip())}

    def _is_whitelisted_message(
        self, msg_type: MessageType, *, user_id: str, group_id: str
    ) -> bool:
        if msg_type == MessageType.GROUP_MESSAGE:
            return not self.group_whitelist or group_id in self.group_whitelist
        return not self.private_user_whitelist or user_id in self.private_user_whitelist

    async def run(self):
        logger.info(f"Starting OneBot v11 adapter on {self.host}:{self.port}")
        logging.getLogger("aiocqhttp").setLevel(logging.ERROR)

        await self.bot.run_task(
            host=self.host,
            port=int(self.port),
            shutdown_trigger=self._shutdown_trigger,
        )

    async def _shutdown_trigger(self):
        await self.shutdown_event.wait()
        logger.info("OneBot v11 adapter shut down.")

    async def send_message(self, event: MessageEvent, text: str):
        await self._send_segments(event, [{"type": "text", "data": {"text": text}}])

    async def send_message_chain(self, event: MessageEvent, chain: MessageChain):
        segments = []
        for comp in chain:
            if isinstance(comp, Plain):
                segments.append({"type": "text", "data": {"text": comp.text}})
            elif isinstance(comp, Image):
                segments.append({"type": "image", "data": {"file": comp.file or comp.url}})
            elif isinstance(comp, At):
                segments.append({"type": "at", "data": {"qq": comp.qq}})
            elif isinstance(comp, Reply):
                segments.append({"type": "reply", "data": {"id": comp.id}})
        await self._send_segments(event, segments)

    async def _send_segments(self, event: MessageEvent, segments: list[dict]):
        try:
            if event.message_type == MessageType.GROUP_MESSAGE:
                await self.bot.send_group_msg(group_id=int(event.group_id), message=segments)
            else:
                await self.bot.send_private_msg(user_id=int(event.sender.user_id), message=segments)
        except Exception as e:
            logger.error(f"Send message failed: {e}")

    async def terminate(self):
        self.shutdown_event.set()
        await super().terminate()

    def meta(self) -> PlatformMeta:
        return PlatformMeta(
            name="onebot11",
            description="OneBot v11 adapter (Napcat compatible)",
            id=self.config.get("id", "onebot11"),
        )
