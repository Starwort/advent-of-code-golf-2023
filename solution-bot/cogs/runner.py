import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from sys import version as py_version
from typing import Literal, TypedDict
from urllib.parse import quote

import aoc_helper
import msgpack
import websockets.client as websockets
from aoc_helper.data import DATA_DIR as aoc_data_dir
from bot import Bot
from context import Context
from discord.ext import commands
from jishaku.codeblocks import Codeblock, codeblock_converter
from thefuzz import fuzz, process
from websockets.version import version as ws_version

README_TEMPLATE = """# Advent of Code Golf 2023

![Advent of Code Golf icon](./advent-of-code-golf.png)

This is a community project for Advent of Code Golf 2023 - anyone can submit a
solution to any day, in any language (supported by [Attempt This
Online](https://ato.pxeger.com)), and the shortest one for each language wins.
This file will be maintained by the `solution-bot` and will contain the current
leaderboard.

If you wish to submit solutions, please use [the bot](https://discord.com/api/oauth2/authorize?client_id=1179753478214651915&permissions=0&scope=bot)
(or [on the code.golf server](https://discord.gg/eVCTkYQ))

## Submission rules

- Each solution must be a full program, runnable on [Attempt This
  Online](https://ato.pxeger.com) - this is how the bot will evaluate submissions.
- Each solution must be a valid answer to the challenge - the bot will check this
  against my input/output for the day. If you believe a submission is *wrong*
  (i.e. doesn't solve the challenge on your input), please raise an issue
  indicating the last working solution for that language, and include your
  Discord username so I can DM you to collect your input and answers.
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
    internal_name: str  # custom
    ato_name: str  # custom
    disallowed_regex: str | None  # custom
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

    def __init__(self, bot: Bot):
        self.bot = bot

    async def async_init(self):
        from json import loads

        self.languages: dict[str, LanguageMeta] = loads(languages.read_text())
        for lang, meta in self.languages.items():
            meta["ato_name"] = lang
            meta["internal_name"] = lang
            meta["disallowed_regex"] = None
        self.language_lookup: dict[str, LanguageMeta] = {
            lang.lower(): meta for lang, meta in self.languages.items()
        } | {meta["name"].lower(): meta for meta in self.languages.values()}

        self.load_variants()

    def load_variants(self):
        extra_langs = {}
        for variant, rule in {
            "no-ws": {"name": "No Whitespace", "disallowed_regex": r"\s"},
            "orthoplex": {
                "name": "Orthoplex",
                "disallowed_regex": r"[^a-z()]|eval|exec",
            },
        }.items():
            extra_langs |= {
                f"{lang}-{variant}": {
                    "name": f"{meta['name']} ({rule['name']})",
                    "ato_name": meta["internal_name"],
                    "internal_name": f"{lang}-{variant}",
                    "version": meta["version"],
                    "disallowed_regex": rule["disallowed_regex"],
                }
                for lang, meta in self.languages.items()
            }
        self.languages |= extra_langs
        self.language_lookup |= {
            lang.lower(): meta for lang, meta in extra_langs.items()
        }
        self.language_lookup |= {
            meta["name"].lower(): meta for meta in extra_langs.values()
        }

    def get_language(
        self, language: str, top_n: int = 3
    ) -> tuple[LanguageMeta | None, list[tuple[LanguageMeta, int]]]:
        language = language.lower()
        if language in self.languages:
            return self.languages[language], []
        elif language in self.language_lookup:
            return self.language_lookup[language], []
        else:
            results: list[tuple[str, int]] = process.extract(
                language,
                self.language_lookup.keys(),
                scorer=fuzz.ratio,
                limit=top_n * 2,
            )  # type: ignore
            found = set()
            filtered: list[tuple[LanguageMeta, int]] = []
            for lang, score in results:
                if self.language_lookup[lang]["name"] not in found:
                    filtered.append((self.language_lookup[lang], score))
                    found.add(self.language_lookup[lang]["name"])
            return None, filtered[:top_n]

    @commands.command(name="search-langs", aliases=["langs"])
    async def search_langs(self, ctx: Context, query: str):
        """Search the available languages. Returns the top 10 matches, or states
        an exact match.
        """
        exact, best_10 = self.get_language(query, 10)
        if exact is not None:
            await ctx.reply(
                f"{query!r} is an available language:"
                f" {exact['name']} ({exact['version']})"
            )
        else:
            await ctx.reply(
                f"## Languages matching {query!r}\n"
                + "\n".join(
                    f"- '{lang['name']}' `{lang['internal_name']}` ({score}%)"
                    for lang, score in best_10
                )
            )

    @commands.command()
    async def search(self, ctx: Context, day: int, language: str):
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
                    f"'{lang['name']}' `{lang['internal_name']}` ({score}%)"
                    for lang, score in top_3_meta
                )
            )
            return
        language = ato_lang["name"]
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
        self, ctx: Context, code: str, language: str, input: str
    ) -> list[str]:
        stdout = ""
        stderr = ""
        async with websockets.connect(
            "wss://ato.pxeger.com/api/v1/ws/execute",
            user_agent_header=(  # type: ignore
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
                try:
                    msg: Stdout | Stderr | Done = msgpack.loads(await ws.recv())  # type: ignore
                except Exception:
                    import traceback

                    traceback.print_exc()
                    await ctx.reply("Something went wrong in the ATO bridge, sorry")
                    break
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
                                await ctx.last_message.append_line(
                                    f"Your code was killed by the server: {why}"
                                )
                            case {
                                "status_type": "core_dumped",
                                "status_value": why,
                            }:
                                await ctx.last_message.append_line(
                                    f"Your code caused a core dump: {why}"
                                )
                        break
            if stdout:
                return stdout.split()
            else:
                return stderr.split()

    @commands.command()
    async def submit(
        self,
        ctx: Context,
        day: int,
        language: str,
        *,
        code: Codeblock = commands.parameter(converter=codeblock_converter),
    ):
        """Submit a solution for a day.

        The solution must be a full program that takes input from stdin, and
        gives the answer to both parts of the puzzle on stdout. The solution
        must be runnable on [ATO](https://ato.pxeger.com).
        """
        puzzle_unlock = datetime(2023, 12, day, 5)
        if datetime.utcnow() < puzzle_unlock:
            await ctx.reply(
                f"Day {day} is not yet unlocked. It will be unlocked"
                f" <t:{int(puzzle_unlock.timestamp())}:R>"
            )
            return
        if code.language is not None:
            # skip initial and trailing newlines when codeblock was given with
            # ``` syntax
            code_content = code.content[1:]
            if code_content.endswith("\n") and language != "whitespace":
                code_content = code_content[:-1]
            code = Codeblock(code.language, code_content)
        ato_lang, top_3_matches = self.get_language(language)
        if ato_lang is None:
            top_3_meta = [
                (self.languages[lang], score)
                for lang, score in top_3_matches
                if lang in self.languages
            ]
            await ctx.reply(
                f"Could not find language `{language}`. Did you mean one of these?\n"
                + "\n".join(
                    f"`{lang['name']}` ({score}%)" for lang, score in top_3_meta
                )
            )
            return
        elif ato_lang["disallowed_regex"] is not None:
            if match := re.search(ato_lang["disallowed_regex"], code.content):
                await ctx.reply(
                    "Sorry, your solution is ineligible for this language variant:"
                    f" contains disallowed text `{match[0]}`"
                )
                return
        real_answer_path = aoc_data_dir / "2023" / f"{day}"
        try:
            real_answers = ((real_answer_path / "1.solution").read_text(),)
            if day != 25:
                real_answers += ((real_answer_path / "2.solution").read_text(),)
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
        if (
            real_answers[0] in code.content
            or day != 25
            and real_answers[1] in code.content
        ):
            await ctx.reply("Don't cheat, that's not cool")
            return
        language = ato_lang["name"]
        await ctx.reply(
            f"Running your code ({len(code.content.encode())} bytes) in"
            f" {language} ({ato_lang['version']})..."
        )
        async with ctx.typing():
            answers = await self.execute(
                ctx,
                code.content,
                language=ato_lang["ato_name"],
                input=aoc_helper.fetch(day, year=2023),
            )

            if not await self.grade_solution(ctx, answers, real_answers):
                return

            cases_dir = extra_data_dir / f"{day}"

            if cases_dir.exists():
                for additional_case in cases_dir.iterdir():
                    input = (additional_case / "input").read_text()
                    real_answers = ((additional_case / "1.solution").read_text(),)
                    if day != 25:
                        real_answers += ((additional_case / "2.solution").read_text(),)
                    answers = await self.execute(
                        ctx,
                        code.content,
                        language=ato_lang["ato_name"],
                        input=input,
                    )
                    if not await self.grade_solution(ctx, answers, real_answers):
                        return

        await ctx.last_message.append_line("That's the right answer!")
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
        self, ctx: Context, answers: list[str], real_answers: tuple[str, ...]
    ) -> bool:
        answers = self.parse_answers(answers)
        if len(answers) != len(real_answers):
            await ctx.last_message.append_line(
                "Your solution gave the wrong number of answers; found:"
                f" {len(answers)} ({', '.join(answers)}), expected: {len(real_answers)}"
            )
            return False
        if tuple(answers) == real_answers:
            return True
        elif answers[0] == real_answers[0]:
            await ctx.last_message.append_line(
                f"Solution gave wrong answer for part 2: {answers[1]}! Correct answer:"
                f" {real_answers[1]}"
            )
            return False
        elif len(answers) == 1 or answers[1] == real_answers[1]:
            await ctx.last_message.append_line(
                f"Solution gave wrong answer for part 1: {answers[0]}! Correct answer:"
                f" {real_answers[0]}"
            )
            return False
        else:
            await ctx.last_message.append_line(
                f"Solution gave wrong answers for both parts: {answers[0]},"
                f" {answers[1]}! Correct answers: {real_answers[0]}, {real_answers[1]}"
            )
            return False

    async def update_solutions(self, ctx: Context, day: int, language: str, code: str):
        solution_path = solutions_dir / f"{day}" / language
        solution_path.parent.mkdir(parents=True, exist_ok=True)
        if solution_path.exists():
            current_solution = solution_path.read_bytes()
            code_bytes = code.encode()
            if len(code_bytes) < len(current_solution):
                await ctx.last_message.append_line(
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
                await ctx.last_message.append_line("Done!")
        else:
            await ctx.last_message.append_line(
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
            await ctx.last_message.append_line("Done!")

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

    @commands.command(name="add-test-case")
    @commands.check(
        lambda ctx: (
            ctx.author.id
            in {
                232948417087668235,  # starwort
                417374125015695373,  # tessaract
            }
        )
    )
    async def add_test_case(
        self, ctx: Context, day: int, case_name: str, answer_1: str, answer_2: str
    ):
        attachments = ctx.message.attachments
        if not attachments:
            await ctx.reply("Missing input file")
            return
        elif len(attachments) > 1:
            await ctx.reply("Too many input files")
            return
        file = attachments[0]
        if file.content_type is None:
            await ctx.reply("Sorry, could not determine content type")
            return
        if not file.content_type.startswith("text/"):
            await ctx.reply(
                f"Bad file type: {file.content_type!r} - expected 'text/plain'"
            )
            return
        test_case_path = extra_data_dir / str(day) / case_name
        if test_case_path.exists():
            await ctx.reply(f"Bad case name {case_name!r} - already exists")
            return
        test_case_path.mkdir(parents=True)
        (test_case_path / "input").write_text(
            (await file.read()).decode(file.content_type.split("charset=")[1])
        )
        (test_case_path / "1.solution").write_text(answer_1)
        if day != 25:
            (test_case_path / "2.solution").write_text(answer_2)
        await ctx.reply(f"Added test case {case_name!r} successfully.")


repo_root = Path(__file__).parent.parent.parent
solutions_dir = repo_root / "solutions"
extra_data_dir = repo_root / "extra-data"
solution_authors_file = repo_root / "solution_authors.json"
readme_file = repo_root / "README.md"
languages = repo_root / "attempt-this-online" / "languages.json"


async def setup(bot: Bot):
    cog = Runner(bot)
    await cog.async_init()
    await bot.add_cog(cog)
