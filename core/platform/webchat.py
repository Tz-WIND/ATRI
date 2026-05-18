"""WebChat platform adapter.

Messages created from the dashboard WebUI go through this adapter. Each chat
request gets a response Future so the HTTP handler can await the pipeline result.
"""

import asyncio
import uuid
from asyncio import Queue

from core import logger

from .base import Platform, PlatformMeta, PlatformStatus
from .message import Image, MessageChain, MessageEvent, MessageType, Plain, Sender


class WebChatAdapter(Platform):
    """Platform adapter for the dashboard WebUI chat."""

    def __init__(self, event_queue: Queue):
        super().__init__({}, event_queue)
        # session_id -> asyncio.Future for pending responses
        self._pending: dict[str, asyncio.Future] = {}
        self._status = PlatformStatus.RUNNING

    def create_event(
        self,
        message: str,
        session_id: str,
        *,
        images: list[dict] | None = None,
    ) -> tuple[MessageEvent, asyncio.Future]:
        """Create a MessageEvent from a WebUI chat message.

        Returns (event, future) -- await the future to get the response text.
        """
        chain: MessageChain = []
        if message:
            chain.append(Plain(text=message))
        for item in images or []:
            chain.append(
                Image(
                    url=str(item.get("url") or ""),
                    file=str(item.get("file") or ""),
                    mime_type=str(item.get("mime_type") or ""),
                    size=int(item.get("size") or 0),
                )
            )
        message_outline = message or (
            f"[{len(images or [])} image attachment(s)]" if images else ""
        )
        event = MessageEvent(
            message_str=message_outline,
            message_chain=chain,
            message_type=MessageType.FRIEND_MESSAGE,
            sender=Sender(user_id="webui_user", nickname="WebUI"),
            session_id=session_id,
            self_id="atri",
            platform_name="webchat",
        )

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        req_id = uuid.uuid4().hex
        event._extras["_webchat_req_id"] = req_id
        self._pending[req_id] = future

        self.commit_event(event)
        return event, future

    async def send_message(self, event: MessageEvent, text: str):
        """Resolve the pending future so the dashboard HTTP handler gets the response."""
        req_id = event._extras.get("_webchat_req_id")
        logger.info(
            f"WebChat.send_message: req_id={req_id}, "
            f"pending={list(self._pending.keys())}, text={len(text)}chars"
        )
        if req_id and req_id in self._pending:
            fut = self._pending.pop(req_id)
            if not fut.done():
                fut.set_result({"text": text, "chain": None})
                logger.info("WebChat.send_message: future resolved")
            else:
                logger.info("WebChat.send_message: future already done")
        else:
            logger.info("WebChat.send_message: req_id not found in pending!")

    async def send_message_chain(self, event: MessageEvent, chain: MessageChain):
        req_id = event._extras.get("_webchat_req_id")
        logger.info(
            f"WebChat.send_message_chain: req_id={req_id}, pending={list(self._pending.keys())}"
        )
        if req_id and req_id in self._pending:
            fut = self._pending.pop(req_id)
            if not fut.done():
                texts = [c.text for c in chain if isinstance(c, Plain)]
                fut.set_result({"text": "\n".join(texts), "chain": chain})
                logger.info("WebChat.send_message_chain: future resolved")
            else:
                logger.info("WebChat.send_message_chain: future already done")
        else:
            logger.info("WebChat.send_message_chain: req_id not found in pending!")

    async def run(self):
        # WebChat doesn't need a persistent connection loop -- it's driven by
        # HTTP requests from the dashboard. Just idle until shutdown.
        logger.info("WebChat adapter ready (driven by dashboard HTTP).")
        self._shutdown = asyncio.Event()
        await self._shutdown.wait()

    async def terminate(self):
        if hasattr(self, "_shutdown"):
            self._shutdown.set()
        # Cancel any pending futures
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        await super().terminate()

    def meta(self) -> PlatformMeta:
        return PlatformMeta(
            name="webchat",
            description="Dashboard WebUI chat adapter",
            id="webchat",
        )


def _chain_to_wire(chain: MessageChain) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for comp in chain:
        if isinstance(comp, Plain):
            items.append({"type": "plain", "text": comp.text})
        elif isinstance(comp, Image):
            file_value = "" if comp.file.startswith("base64://") else comp.file
            items.append(
                {
                    "type": "image",
                    "url": comp.url,
                    "file": file_value,
                    "mime_type": comp.mime_type,
                    "size": comp.size,
                }
            )
    return items
