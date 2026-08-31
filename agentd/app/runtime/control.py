from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import HumanMessage

QueueKind = Literal["steer", "follow-up"]


@dataclass(frozen=True)
class QueuedMessage:
    kind: QueueKind
    message: HumanMessage


class _MessageQueue:
    """单进程事件循环中的最小 FIFO。

    FastAPI 路由和 Agent Worker 运行在同一个 asyncio 事件循环中；enqueue 和
    drain 之间没有 await，因此这里不需要线程锁。若未来改成多进程，必须把队列
    换成共享存储，不能误以为这个内存对象还能保证一致性。
    """

    def __init__(self) -> None:
        self._items: deque[QueuedMessage] = deque()

    def enqueue(self, item: QueuedMessage) -> None:
        self._items.append(item)

    def drain(self) -> list[QueuedMessage]:
        items = list(self._items)
        self._items.clear()
        return items

    def clear(self) -> None:
        self._items.clear()

    def __bool__(self) -> bool:
        return bool(self._items)


class AgentControl:
    """运行中控制面：steer 改方向，follow-up 在自然结束后追加。

    cancel 不伪装成队列消息；M3 由 TaskStore 直接取消运行中的 asyncio.Task，
    这样 Provider、工具和 Runner finally 都能观察到真正的取消语义。
    """

    def __init__(self) -> None:
        self._steering = _MessageQueue()
        self._follow_ups = _MessageQueue()

    def steer(self, content: str) -> None:
        self._steering.enqueue(
            QueuedMessage("steer", HumanMessage(content=content))
        )

    def follow_up(self, content: str) -> None:
        self._follow_ups.enqueue(
            QueuedMessage("follow-up", HumanMessage(content=content))
        )

    def drain_steering(self) -> list[QueuedMessage]:
        return self._steering.drain()

    def drain_follow_ups(self) -> list[QueuedMessage]:
        return self._follow_ups.drain()

    def clear(self) -> None:
        self._steering.clear()
        self._follow_ups.clear()
