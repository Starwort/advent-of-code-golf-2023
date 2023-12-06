#!/usr/bin/env python3

import config
import discord
from context import Context
from discord.ext import commands
from message import Message


class Bot(commands.Bot):
    def __init__(self, intents: discord.Intents, **kwargs):
        super().__init__(
            command_prefix=commands.when_mentioned_or("aoc!", "aoc ", "aoc|"),
            intents=intents,
            **kwargs,
        )

    async def get_context(self, message: Message, *, cls=Context):
        return await super().get_context(message, cls=cls)

    async def setup_hook(self):
        for cog in config.cogs:
            try:
                await self.load_extension(cog)
            except Exception as exc:
                print(
                    f"Could not load extension {cog} due to {exc.__class__.__name__}:"
                    f" {exc}"
                )

    async def on_ready(self):
        assert self.user is not None
        print(f"Logged on as {self.user} (ID: {self.user.id})")


intents = discord.Intents.default()
intents.message_content = True
bot = Bot(intents=intents)

# write general commands here

if __name__ == "__main__":
    bot.run(config.token)
