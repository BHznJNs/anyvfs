import inspect
from typing import Awaitable, TypeAlias, TypeVar


T = TypeVar("T")
AwaitableOr: TypeAlias = Awaitable[T] | T

async def resolve_awaitable_or(awaitable_or: AwaitableOr[T]) -> T:
    if inspect.isawaitable(awaitable_or):
        return await awaitable_or
    return awaitable_or
