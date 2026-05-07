"""Base platform adapter."""

import abc
from asyncio import Queue
from dataclasses import dataclass
from enum import Enum

from .message import MessageEvent


class PlatformStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass
class PlatformMeta:
    name: str
    description: str
    id: str = ""


class Platform(abc.ABC):
    def __init__(self, config: dict, event_queue: Queue):
        self.config = config
        self._event_queue = event_queue
        self._status = PlatformStatus.PENDING

    @property
    def status(self) -> PlatformStatus:
        return self._status

    def commit_event(self, event: MessageEvent):
        self._event_queue.put_nowait(event)

    @abc.abstractmethod
    async def run(self):
        raise NotImplementedError

    @abc.abstractmethod
    async def send_message(self, event: MessageEvent, text: str):
        raise NotImplementedError

    @abc.abstractmethod
    async def send_message_chain(self, event: MessageEvent, chain):
        raise NotImplementedError

    async def terminate(self):
        self._status = PlatformStatus.STOPPED

    @abc.abstractmethod
    def meta(self) -> PlatformMeta:
        raise NotImplementedError
