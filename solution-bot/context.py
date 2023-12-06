from typing import cast

from discord.ext import commands

from message import Message


class Context(commands.Context):
    last_message: Message
    message: Message

    async def send(self, *args, **kw) -> Message:
        self.last_message = cast(Message, await super().send(*args, **kw))
        return self.last_message

    async def reply(self, *args, **kw) -> Message:
        self.last_message = cast(Message, await super().reply(*args, **kw))
        return self.last_message
