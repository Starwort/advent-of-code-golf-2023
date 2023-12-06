import discord


def custom_create_message(
    cls,
    *,
    state,
    channel,
    data,
) -> "Message":
    msg = object.__new__(Message)
    msg.__init__(state=state, channel=channel, data=data)
    return msg


discord.Message.__new__ = custom_create_message  # type: ignore


class Message(discord.Message):
    __slots__ = ()

    async def edit(self, content: str | None = None, **kwargs):
        msg = await super().edit(content=content, **kwargs)
        for itm in discord.Message.__slots__:
            try:
                setattr(self, itm, getattr(msg, itm))  # update in-place
            except AttributeError:
                pass  # doesn't exist, don't copy
        return self

    async def append_line(self, line: str):
        await self.edit(self.content + "\n" + line)

    def __copy__(self):
        msg = object.__new__(Message)
        for itm in discord.Message.__slots__:
            try:
                setattr(msg, itm, getattr(self, itm))
            except AttributeError:
                pass  # doesn't exist, don't copy
        return msg
