import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from sys import version as py_version
from typing import TYPE_CHECKING, Literal, TypedDict
from urllib.parse import quote

import aoc_helper
import discord
import msgpack
import websockets.client as websockets
from aoc_helper.data import DATA_DIR as aoc_data_dir
from discord.ext import commands
from jishaku.codeblocks import Codeblock, codeblock_converter
from thefuzz import fuzz, process
from websockets.version import version as ws_version

README_TEMPLATE = """# Advent of Code Golf 2023

![Advent of Code Golf icon](./advent-of-code-golf.png)

This is a community project for Advent of Code Golf 2023 - anyone can submit a
solution to any day, in any language (supported by [TIO.run](https://tio.run)),
and the shortest one for each language wins. This file will be maintained by the
`solution-bot` and will contain the current leaderboard.

## Submission rules

- Each solution must be a full program, runnable on [TIO.run](https://tio.run) -
  this is how the bot will evaluate submissions.
- Each solution must be a valid answer to the challenge - the bot will check this
  against my input/output for the day. If you believe a submission is *wrong*
  (i.e. doesn't solve the challenge on your input), please raise an issue
  including the submission, your input, and the expected answer.
- Puzzles involving grid lettering do not need to OCR the letters themselves -
  outputting the grid is sufficient.
- Input will be provided as stdin, and output should be to stdout.
- Input length is measured in *bytes*, not characters.

## Leaderboard

{}
"""


class LanguageMeta(TypedDict):
    name: str
    version: str
    # ignoring the rest of the fields for now


class Stdout(TypedDict):
    Stdout: bytes


class Stderr(TypedDict):
    Stderr: bytes


class DoneData(TypedDict):
    status_type: Literal["exited", "killed", "core_dumped", "unknown"]
    status_value: int
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    # ignoring the rest of the fields for now


class Done(TypedDict):
    Done: DoneData


type SolutionDay = str
type SolutionLanguage = str

type SolutionAuthors = dict[SolutionDay, dict[SolutionLanguage, str]]


class Runner(commands.Cog):
    """The description for Runner goes here."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def async_init(self):
        from json import loads

        self.languages: dict[str, LanguageMeta] = loads(languages.read_text())
        self.language_lookup: dict[str, str] = {
            lang.lower(): lang for lang in self.languages
        } | {meta["name"].lower(): lang for lang, meta in self.languages.items()}

    def get_language(self, language: str) -> tuple[str | None, list[tuple[str, int]]]:
        language = language.lower()
        if language in self.languages:
            return language, []
        else:
            results: list[tuple[str, int]] = process.extract(
                language,
                self.language_lookup.keys(),
                scorer=fuzz.ratio,
                limit=6,
            )  # type: ignore
            found = set()
            filtered: list[tuple[str, int]] = []
            for lang, score in results:
                if self.language_lookup[lang] not in found:
                    filtered.append((self.language_lookup[lang], score))
                    found.add(self.language_lookup[lang])
            return None, filtered[:3]

    @commands.command()
    async def search(self, ctx: commands.Context, day: int, language: str):
        """Search for a solution in a language."""
        ato_lang, top_3_matches = self.get_language(language.lower())
        if ato_lang is None:
            top_3_meta = [
                (self.languages[lang], score)
                for lang, score in top_3_matches
                if lang in self.languages
            ]
            await ctx.send(
                f"Could not find language `{language}`. Did you mean one of these?\n"
                + "\n".join(
                    f"`{lang['name']}` ({score}%)" for lang, score in top_3_meta
                )
            )
            return
        language = self.languages[ato_lang]["name"]
        solution_authors: SolutionAuthors = json.loads(
            solution_authors_file.read_text()
        )
        if str(day) not in solution_authors:
            await ctx.reply("No solutions for this day yet.")
            return
        if language not in solution_authors[str(day)]:
            await ctx.reply("No solutions for this language yet.")
            return
        author = solution_authors[str(day)][language]
        solution = (solutions_dir / f"{day}" / language).read_text()
        solution_len = len(solution.encode())
        await ctx.reply(
            f"[Solution for day {day} in {language} by"
            f" {author} ({solution_len})](https://github.com/Starwort/advent-of-code-golf-2023/blob/master/solutions/{day}/{quote(language)}):\n```\n"
            + solution
            + "\n```"
        )

    async def execute(
        self, ctx: commands.Context, code: str, language: str, input: str
    ) -> list[str]:
        stdout = ""
        stderr = ""
        async with websockets.connect(
            "wss://ato.pxeger.com/api/v1/ws/execute",
            user_agent_header=(
                f"Advent of Code Golf bot / websockets=={ws_version};"
                f" Python=={py_version}"
            ),
        ) as ws:
            await ws.send(
                msgpack.dumps(
                    {
                        "language": language,
                        "code": code,
                        "input": input,
                        "options": [],
                        "arguments": [],
                    }
                )
            )
            while True:
                msg: Stdout | Stderr | Done = msgpack.loads(await ws.recv())  # type: ignore
                match msg:
                    case {"Stdout": data}:
                        stdout += data.decode()
                    case {"Stderr": data}:
                        stderr += data.decode()
                    case {"Done": data}:
                        match data:
                            case {"timed_out": True}:
                                await ctx.reply("Your code timed out after 60 seconds.")
                            case {"status_type": "killed", "status_value": why}:
                                await ctx.reply(
                                    f"Your code was killed by the server: {why}"
                                )
                            case {"status_type": "core_dumped", "status_value": why}:
                                await ctx.reply(f"Your code caused a core dump: {why}")
                        break
            if stdout:
                return stdout.split()
            else:
                return stderr.split()

    @commands.command()
    async def submit(self, ctx: commands.Context, day: int, language: str, *, code: codeblock_converter):  # type: ignore
        """Submit a solution for a day.

        The solution must be a full program that takes input from stdin, and
        gives the answer to both parts of the puzzle on stdout. The solution
        must be runnable on [TIO.run](https://tio.run).
        """
        puzzle_unlock = datetime(2023, 12, day, 5)
        if datetime.utcnow() < puzzle_unlock:
            await ctx.reply(
                f"Day {day} is not yet unlocked. It will be unlocked"
                f" <t:{int(puzzle_unlock.timestamp())}:R>"
            )
            return
        if TYPE_CHECKING:
            code: Codeblock = code  # type: ignore
        ato_lang, top_3_matches = self.get_language(language.lower())
        if ato_lang is None:
            top_3_meta = [
                (self.languages[lang], score)
                for lang, score in top_3_matches
                if lang in self.languages
            ]
            await ctx.send(
                f"Could not find language `{language}`. Did you mean one of these?\n"
                + "\n".join(
                    f"`{lang['name']}` ({score}%)" for lang, score in top_3_meta
                )
            )
            return
        language = self.languages[ato_lang]["name"]
        await ctx.send(
            "Running your code in"
            f" {language} ({self.languages[ato_lang]['version']})..."
        )
        answers = await self.execute(
            ctx,
            code.content,
            language=ato_lang,
            input=aoc_helper.fetch(day, year=2023),
        )
        real_answer_path = aoc_data_dir / "2023" / f"{day}"
        try:
            real_answers = (
                (real_answer_path / "1.solution").read_text(),
                (real_answer_path / "2.solution").read_text(),
            )
        except FileNotFoundError:
            now = datetime.utcnow()
            soon = now.replace(
                second=0, microsecond=0, minute=now.minute - (now.minute % 15)
            ) + timedelta(minutes=15)
            timestamp = int(soon.timestamp())
            await ctx.reply(
                "Sorry, submissions for this day are not yet open. Please try again"
                f" <t:{timestamp}:R>"
            )
            return

        if not await self.grade_solution(ctx, answers, real_answers):
            return

        for additional_case in (extra_data_dir / f"{day}").iterdir():
            input = (additional_case / "input").read_text()
            real_answers = (
                (additional_case / "1.solution").read_text(),
                (additional_case / "2.solution").read_text(),
            )
            answers = await self.execute(
                ctx,
                code.content,
                language=ato_lang,
                input=input,
            )
            if not await self.grade_solution(ctx, answers, real_answers):
                return

        await self.update_solutions(ctx, day, language, code.content)

    def row_to_bools(self, row: str) -> list[bool]:
        return [c == "#" for c in row]

    def parse_answers(self, answers: list[str]) -> list[str]:
        looks_like_grid = ["#" in answer for answer in answers]
        out_answers = []
        current_grid = []
        for answer, is_grid in zip(answers, looks_like_grid):
            if is_grid:
                current_grid.append(self.row_to_bools(answer))
                if len(current_grid) == 6:
                    out_answers.append(aoc_helper.decode_text(current_grid))
                    current_grid = []
            else:
                out_answers.append(answer)
        return out_answers

    async def grade_solution(
        self, ctx: commands.Context, answers: list[str], real_answers: tuple[str, str]
    ) -> bool:
        answers = self.parse_answers(answers)
        if len(answers) != 2:
            await ctx.reply(
                "Your solution gave the wrong number of answers; found:"
                f" {len(answers)} ({', '.join(answers)}), expected: 2"
            )
            return False
        if (answers[0], answers[1]) == real_answers:
            await ctx.reply("That's the right answer!")
            return True
        elif answers[0] == real_answers[0]:
            await ctx.reply(
                f"Solution gave wrong answer for part 2: {answers[1]}! Correct answer:"
                f" {real_answers[1]}"
            )
            return False
        elif answers[1] == real_answers[1]:
            await ctx.reply(
                f"Solution gave wrong answer for part 1: {answers[0]}! Correct answer:"
                f" {real_answers[0]}"
            )
            return False
        else:
            await ctx.reply(
                f"Solution gave wrong answers for both parts: {answers[0]},"
                f" {answers[1]}! Correct answers: {real_answers[0]}, {real_answers[1]}"
            )
            return False

    async def update_solutions(
        self, ctx: commands.Context, day: int, language: str, code: str
    ):
        solution_path = solutions_dir / f"{day}" / language
        solution_path.parent.mkdir(parents=True, exist_ok=True)
        if solution_path.exists():
            current_solution = solution_path.read_bytes()
            code_bytes = code.encode()
            if len(code_bytes) < len(current_solution):
                reply = await ctx.reply(
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
                subprocess = await asyncio.create_subprocess_shell(
                    f'git add .. && git commit -m "({ctx.author.name}) Day'
                    f' {day} {language} {len(current_solution)} -> {len(code_bytes)}"'
                    " && git push"
                )
                await subprocess.wait()
                await reply.edit(
                    content=(
                        "Your solution is shorter than the current one, updating..."
                        " Done!"
                    )
                )
        else:
            reply = await ctx.reply(
                "Your solution is the first for this language, adding..."
            )
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
            subprocess = await asyncio.create_subprocess_shell(
                f'git add .. && git commit -m "({ctx.author.name})'
                f' Day {day} {language} -> {len(code.encode())}" && git push'
            )
            await subprocess.wait()
            await reply.edit(
                content="Your solution is the first for this language, adding... Done!"
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
                    return (
                        f"[{solution_len} - {author}](./solutions/{day}/{quote(lang)})"
                    )
                else:
                    return "-"

            leaderboard += f"\n{day} | " + " | ".join(get_entry(lang) for lang in langs)
        readme_file.write_text(README_TEMPLATE.format(leaderboard))


repo_root = Path(__file__).parent.parent.parent
solutions_dir = repo_root / "solutions"
extra_data_dir = repo_root / "extra-data"
solution_authors_file = repo_root / "solution_authors.json"
readme_file = repo_root / "README.md"
languages = repo_root / "attempt-this-online" / "languages.json"


async def setup(bot: commands.Bot):
    cog = Runner(bot)
    await cog.async_init()
    await bot.add_cog(cog)
