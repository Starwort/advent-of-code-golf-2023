import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, assert_never

import aoc_helper
import discord
from aoc_helper.data import DATA_DIR as aoc_data_dir
from async_tio import Language, Tio
from discord.ext import commands
from jishaku.codeblocks import Codeblock, codeblock_converter
from thefuzz import process

README_TEMPLATE = """# Advent of Golf 2023

This is a community project for Advent of Golf 2023 - anyone can submit a solution to any day, in any language (supported by [TIO.run](https://tio.run)), and the shortest one for each language wins. This file will be maintained by the `solution-bot` and will contain the current leaderboard.

## Submission rules

- Each solution must be a full program, runnable on [TIO.run](https://tio.run) - this is how the bot will evaluate submissions.
- Each solution must be a valid answer to the challenge - the bot will check this against my input/output for the day. If you believe a submission is *wrong* (i.e. doesn't solve the challenge on your input), please raise an issue including the submission, your input, and the expected answer.
- Puzzles involving grid lettering do not need to OCR the letters themselves - outputting the grid is sufficient.
- Input will be provided as stdin, and output should be to stdout.
- Input length is measured in *bytes*, not characters.

## Leaderboard

{}
"""

type SolutionDay = str
type SolutionLanguage = str

type SolutionAuthors = dict[SolutionDay, dict[SolutionLanguage, str]]


class Runner(commands.Cog):
    """The description for Runner goes here."""

    languages: dict[str, Language]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tio = Tio()

    async def async_init(self):
        languages = await self.tio.get_languages()

        self.languages = (
            {lang.name: lang for lang in languages}
            | {lang.tio_name: lang for lang in languages}
            | {lang.alias: lang for lang in languages}
        )

    def get_language(
        self, language: str
    ) -> tuple[Language | None, list[tuple[Language, int]]]:
        if language in self.languages:
            return self.languages[language], []
        else:
            results: list[tuple[str, int]] = process.extract(language, self.languages.keys(), limit=3)  # type: ignore
            return None, [(self.languages[lang], score) for lang, score in results]

    @commands.command()
    async def submit(self, ctx: commands.Context, day: int, language: str, code: codeblock_converter):  # type: ignore
        if TYPE_CHECKING:
            code: Codeblock = code  # type: ignore
        tio_lang, top_3_matches = self.get_language(language)
        if tio_lang is None:
            await ctx.send(
                f"Could not find language `{language}`. Did you mean one of these?\n"
                + "\n".join(
                    f"`{lang.name}` ({score}%)" for lang, score in top_3_matches
                )
            )
            return
        language = tio_lang.name
        await ctx.send(f"Running your code in {tio_lang.name}...")
        result = await self.tio.execute(
            code.content,
            language=tio_lang.tio_name,
            inputs=aoc_helper.fetch(day, year=2023),
        )
        answers = result.output.split()
        real_answer_path = aoc_data_dir / "2023" / f"{day}"
        real_answers = (
            (real_answer_path / "1.solution").read_text(),
            (real_answer_path / "2.solution").read_text(),
        )
        if real_answers[0] in code.content or real_answers[1] in code.content:
            await ctx.reply("Your solution contains the answer. Please don't cheat.")
            return
        if await self.grade_solution(ctx, answers, real_answers):
            await self.update_solutions(ctx, day, language, code.content)

    def row_to_bools(self, row: str) -> list[bool]:
        return [c == "#" for c in row]

    async def grade_solution(
        self, ctx: commands.Context, answers: list[str], real_answers: tuple[str, str]
    ) -> bool:
        if (answers[0], answers[1]) == real_answers:
            await ctx.reply("That's the right answer!")
            return True
        elif answers[0] == real_answers[0]:
            text = aoc_helper.decode_text(
                [self.row_to_bools(row) for row in answers[1:]]
            )
            if text == real_answers[1]:
                await ctx.reply("That's the right answer!")
                return True
            elif "?" not in text:
                await ctx.reply(
                    f"Solution gave wrong answer for part 2: {text}! Correct answer:"
                    f" {real_answers[1]}"
                )
                return False
            else:
                await ctx.reply(
                    f"Failed to decode text; incompletely decoded as {text}\nOriginal"
                    " text was:\n```\n"
                    + "\n".join(answers[1:])
                    + "\n```"
                )
                return False
        elif answers[-1] == real_answers[1]:
            text = aoc_helper.decode_text(
                [self.row_to_bools(row) for row in answers[:-1]]
            )
            if text == real_answers[0]:
                await ctx.reply("That's the right answer!")
                return True
            elif "?" not in text:
                await ctx.reply(
                    f"Solution gave wrong answer for part 1: {text}! Correct answer:"
                    f" {real_answers[0]}"
                )
                return False
            else:
                await ctx.reply(
                    f"Failed to decode text; incompletely decoded as {text}\nOriginal"
                    " text was:\n```\n"
                    + "\n".join(answers[:-1])
                    + "\n```"
                )
                return False
        else:
            text_left = aoc_helper.decode_text(
                [self.row_to_bools(row) for row in answers[: len(answers) // 2]]
            )
            text_right = aoc_helper.decode_text(
                [self.row_to_bools(row) for row in answers[len(answers) // 2 :]]
            )
            failed_msg = ""
            if "?" in text_left:
                failed_msg += (
                    "Failed to decode text for part 1; incompletely decoded as"
                    f" {text_left}\nOriginal text was:\n```\n"
                    + "\n".join(answers[: len(answers) // 2])
                    + "\n```\n"
                )
            if "?" in text_right:
                failed_msg += (
                    "Failed to decode text for part 2; incompletely decoded as"
                    f" {text_right}\nOriginal text was:\n```\n"
                    + "\n".join(answers[len(answers) // 2 :])
                    + "\n```"
                )
            if failed_msg:
                await ctx.reply(failed_msg)
                return False
            match (text_left == real_answers[0], text_right == real_answers[1]):
                case (True, True):
                    await ctx.reply("That's the right answer!")
                    return True
                case (False, True):
                    await ctx.reply(
                        f"Solution gave wrong answer for part 1: {text_left}! Correct"
                        f" answer: {real_answers[0]}"
                    )
                    return False
                case (True, False):
                    await ctx.reply(
                        f"Solution gave wrong answer for part 2: {text_right}! Correct"
                        f" answer: {real_answers[1]}"
                    )
                    return False
                case (False, False):
                    await ctx.reply(
                        f"Solution gave wrong answer for both parts: {text_left},"
                        f" {text_right}! Correct answer: {real_answers[0]},"
                        f" {real_answers[1]}"
                    )
                    return False
                case _:
                    assert False, "unreachable"

    async def update_solutions(
        self, ctx: commands.Context, day: int, language: str, code: str
    ):
        solution_path = solutions_dir / f"{day}" / language
        solution_path.parent.mkdir(parents=True, exist_ok=True)
        if solution_path.exists():
            current_solution = solution_path.read_bytes()
            code_bytes = code.encode()
            if len(code_bytes) < len(current_solution):
                await ctx.reply(
                    "Your solution is shorter than the current one, updating..."
                )
                solution_path.write_bytes(code_bytes)
                solution_authors: SolutionAuthors = json.loads(
                    solution_authors_file.read_text()
                )
                if str(day) in solution_authors:
                    solution_authors[str(day)][language] = ctx.author.name
                else:
                    solution_authors[str(day)] = {language: ctx.author.name}
                solution_authors_file.write_text(json.dumps(solution_authors))
                self.update_leaderboard()
                await asyncio.create_subprocess_shell(
                    f'git add . && git commit -m "({ctx.author.name}) Day'
                    f' {day} {language} {len(current_solution)} -> {len(code_bytes)}"'
                    " && git push"
                )
        else:
            await ctx.reply("Your solution is the first for this language, adding...")
            code_bytes = code.encode()
            solution_path.write_bytes(code_bytes)
            solution_authors: SolutionAuthors = json.loads(
                solution_authors_file.read_text()
            )
            if str(day) in solution_authors:
                solution_authors[str(day)][language] = ctx.author.name
            else:
                solution_authors[str(day)] = {language: ctx.author.name}
            solution_authors_file.write_text(json.dumps(solution_authors))
            self.update_leaderboard()
            await asyncio.create_subprocess_shell(
                f'git add . && git commit -m "({ctx.author.name})'
                f' Day {day} {language} -> {len(code.encode())}" && git push'
            )

    def update_leaderboard(self):
        solution_authors: SolutionAuthors = json.loads(
            solution_authors_file.read_text()
        )
        max_day = max(int(day) for day in solution_authors)
        langs = sorted({lang for day in solution_authors.values() for lang in day})
        leaderboard = (
            "Day | "
            + " | ".join(langs)
            + "\n"
            + "--: | "
            + " | ".join("---" for _ in langs)
        )
        for day in range(1, max_day + 1):
            day_solutions = solution_authors.get(str(day), {})

            def get_entry(lang: str) -> str:
                if author := day_solutions.get(lang):
                    solution_len = len((solutions_dir / f"{day}" / lang).read_bytes())
                    return f"[{solution_len} - {author}](./solutions/{day}/{lang})"
                else:
                    return "-"

            leaderboard += f"\n{day} | " + " | ".join(get_entry(lang) for lang in langs)
        readme_file.write_text(README_TEMPLATE.format(leaderboard))


repo_root = Path(__file__).parent.parent.parent
solutions_dir = repo_root / "solutions"
solution_authors_file = repo_root / "solution_authors.json"
readme_file = repo_root / "README.md"


async def setup(bot: commands.Bot):
    cog = Runner(bot)
    await cog.async_init()
    await bot.add_cog(cog)
