import logging
import re
import textwrap
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Coroutine, List, Tuple
from urllib.parse import quote_plus

import cachingutils
import disnake
from aiohttp import ClientResponseError
from disnake.ext import commands

from monty import constants
from monty.bot import Bot
from monty.log import get_logger
from monty.utils.helpers import EXPAND_BUTTON_PREFIX, decode_github_link
from monty.utils.messages import DeleteButton


if TYPE_CHECKING:
    from monty.exts.info.github_info import GithubInfo

log = get_logger(__name__)


GITHUB_RE = re.compile(
    r"https://github\.com/(?P<repo>[a-zA-Z0-9-]+/[\w.-]+)/(?:blob|tree)/"
    r"(?P<path>[^#>]+)(\?[^#>]+)?(#L(?P<start_line>\d+)(([-~:]|(\.\.))L(?P<end_line>\d+))?)"
)

GITHUB_GIST_RE = re.compile(
    r"https://gist\.github\.com/([a-zA-Z0-9-]+)/(?P<gist_id>[a-zA-Z0-9]+)/*"
    r"(?P<revision>[a-zA-Z0-9]*)/*#file-(?P<file_path>[^#>]+?)(\?[^#>]+)?"
    r"(-L(?P<start_line>\d+)([-~:]L(?P<end_line>\d+))?)"
)

GITHUB_HEADERS = {"Accept": "application/vnd.github.v3.raw"}
if GITHUB_TOKEN := constants.Tokens.github:
    GITHUB_HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"

GITLAB_RE = re.compile(
    r"https://gitlab\.com/(?P<repo>[\w.-]+/[\w.-]+)/\-/blob/(?P<path>[^#>]+)"
    r"(\?[^#>]+)?(#L(?P<start_line>\d+)(-(?P<end_line>\d+))?)"
)

BITBUCKET_RE = re.compile(
    r"https://bitbucket\.org/(?P<repo>[a-zA-Z0-9-]+/[\w.-]+)/src/(?P<ref>[0-9a-zA-Z]+)"
    r"/(?P<file_path>[^#>]+)(\?[^#>]+)?(#lines-(?P<start_line>\d+)(:(?P<end_line>\d+))?)"
)

BLACKLIST = []


class CodeSnippets(commands.Cog, slash_command_attrs={"dm_permission": False}):
    """
    commands.Cog that parses and sends code snippets to disnake.

    Matches each message against a regex and prints the contents of all matched snippets.
    """

    def __init__(self, bot: Bot):
        """Initializes the cog's bot."""
        self.bot = bot

        self.pattern_handlers: List[Tuple[re.Pattern, Coroutine]] = [
            (GITHUB_RE, self._fetch_github_snippet),
            (GITHUB_GIST_RE, self._fetch_github_gist_snippet),
            (GITLAB_RE, self._fetch_gitlab_snippet),
            (BITBUCKET_RE, self._fetch_bitbucket_snippet),
        ]

        self.request_cache: cachingutils.MemoryCache[str, Any] = cachingutils.MemoryCache(timeout=timedelta(minutes=6))

    async def _fetch_response(self, url: str, response_format: str, **kwargs) -> Any:
        """Makes http requests using aiohttp."""
        # make the request with the github_info cog if it is loaded
        if url.startswith("https://api.github.com/") and (cog := self.bot.get_cog("GithubInfo")):
            cog: GithubInfo
            return await cog.fetch_data(
                url,
                as_text=True if response_format == "text" else False,
                raise_for_status=True,
                **kwargs,
            )

        key = (url, response_format)
        if cached := self.request_cache.get(key):
            return cached

        async with self.bot.http_session.get(url, raise_for_status=True, **kwargs) as response:
            if response_format == "text":
                body = await response.text()
            elif response_format == "json":
                body = await response.json()
            else:
                return None

        self.request_cache.set(key, body)
        return body

    def _find_ref(self, path: str, refs: tuple) -> tuple:
        """Loops through all branches and tags to find the required ref."""
        # Base case: there is no slash in the branch name
        ref, file_path = path.split("/", 1)
        # In case there are slashes in the branch name, we loop through all branches and tags
        for possible_ref in refs:
            if path.startswith(possible_ref["name"] + "/"):
                ref = possible_ref["name"]
                file_path = path[len(ref) + 1 :]
                break
        return ref, file_path

    async def _fetch_github_snippet(self, repo: str, path: str, start_line: str, end_line: str) -> str:
        """Fetches a snippet from a GitHub repo."""
        # Search the GitHub API for the specified branch
        branches = await self._fetch_response(
            f"https://api.github.com/repos/{repo}/branches",
            "json",
            headers=GITHUB_HEADERS,
        )
        tags = await self._fetch_response(f"https://api.github.com/repos/{repo}/tags", "json", headers=GITHUB_HEADERS)
        refs = branches + tags
        ref, file_path = self._find_ref(path, refs)

        file_contents = await self._fetch_response(
            f"https://api.github.com/repos/{repo}/contents/{file_path}?ref={ref}",
            "text",
            headers=GITHUB_HEADERS,
        )
        return self._snippet_to_codeblock(file_contents, file_path, start_line, end_line)

    async def _fetch_github_gist_snippet(
        self,
        gist_id: str,
        revision: str,
        file_path: str,
        start_line: str,
        end_line: str,
    ) -> str:
        """Fetches a snippet from a GitHub gist."""
        gist_json = await self._fetch_response(
            f'https://api.github.com/gists/{gist_id}{f"/{revision}" if len(revision) > 0 else ""}',
            "json",
            headers=GITHUB_HEADERS,
        )

        # Check each file in the gist for the specified file
        for gist_file in gist_json["files"]:
            if file_path == gist_file.lower().replace(".", "-"):
                file_contents = await self._fetch_response(
                    gist_json["files"][gist_file]["raw_url"],
                    "text",
                )
                return self._snippet_to_codeblock(file_contents, gist_file, start_line, end_line)
        return ""

    async def _fetch_gitlab_snippet(self, repo: str, path: str, start_line: str, end_line: str) -> str:
        """Fetches a snippet from a GitLab repo."""
        enc_repo = quote_plus(repo)

        # Searches the GitLab API for the specified branch
        branches = await self._fetch_response(
            f"https://gitlab.com/api/v4/projects/{enc_repo}/repository/branches", "json"
        )
        tags = await self._fetch_response(f"https://gitlab.com/api/v4/projects/{enc_repo}/repository/tags", "json")
        refs = branches + tags
        ref, file_path = self._find_ref(path, refs)
        enc_ref = quote_plus(ref)
        enc_file_path = quote_plus(file_path)

        file_contents = await self._fetch_response(
            f"https://gitlab.com/api/v4/projects/{enc_repo}/repository/files/{enc_file_path}/raw?ref={enc_ref}",
            "text",
        )
        return self._snippet_to_codeblock(file_contents, file_path, start_line, end_line)

    async def _fetch_bitbucket_snippet(
        self, repo: str, ref: str, file_path: str, start_line: str, end_line: str
    ) -> str:
        """Fetches a snippet from a BitBucket repo."""
        file_contents = await self._fetch_response(
            f"https://bitbucket.org/{quote_plus(repo)}/raw/{quote_plus(ref)}/{quote_plus(file_path)}",
            "text",
        )
        return self._snippet_to_codeblock(file_contents, file_path, start_line, end_line)

    def _snippet_to_codeblock(self, file_contents: str, file_path: str, start_line: str, end_line: str) -> str:
        """
        Given the entire file contents and target lines, creates a code block.

        First, we split the file contents into a list of lines and then keep and join only the required
        ones together.

        We then dedent the lines to look nice, and replace all ` characters with `\u200b to prevent
        markdown injection.

        Finally, we surround the code with ``` characters.
        """
        # Parse start_line and end_line into integers
        if end_line is None:
            start_line = end_line = int(start_line)
        else:
            start_line = int(start_line)
            end_line = int(end_line)

        split_file_contents = file_contents.splitlines()

        # Make sure that the specified lines are in range
        if start_line > end_line:
            start_line, end_line = end_line, start_line
        if start_line > len(split_file_contents) or end_line < 1:
            return ""
        start_line = max(1, start_line)
        end_line = min(len(split_file_contents), end_line)

        # Gets the code lines, dedents them, and inserts zero-width spaces to prevent Markdown injection
        required = "\n".join(split_file_contents[start_line - 1 : end_line])
        required = textwrap.dedent(required).rstrip().replace("`", "`\u200b")

        # Extracts the code language and checks whether it's a "valid" language
        language = file_path.split("/")[-1].split(".")[-1]
        trimmed_language = language.replace("-", "").replace("+", "").replace("_", "")
        is_valid_language = trimmed_language.isalnum()
        if not is_valid_language:
            language = ""

        # Adds a label showing the file path to the snippet
        if start_line == end_line:
            ret = f"`{file_path}` line {start_line}\n"
        else:
            ret = f"`{file_path}` lines {start_line} to {end_line}\n"

        if len(required) != 0:
            return f"{ret}```{language}\n{required}```"
        # Returns an empty codeblock if the snippet is empty
        return f"{ret}``` ```"

    async def _parse_snippets(self, content: str) -> str:
        """Parse message content and return a string with a code block for each URL found."""
        all_snippets = []

        for pattern, handler in self.pattern_handlers:
            for match in pattern.finditer(content):
                try:
                    snippet = await handler(**match.groupdict())
                    all_snippets.append((match.start(), snippet))
                except ClientResponseError as error:
                    error_message = error.message  # noqa: B306
                    log.log(
                        logging.DEBUG if error.status == 404 else logging.ERROR,
                        f"Failed to fetch code snippet from {match[0]!r}: {error.status} "
                        f"{error_message} for GET {error.request_info.real_url.human_repr()}",
                    )

        # Sorts the list of snippets by their match index and joins them into a single message
        return "\n".join(map(lambda x: x[1], sorted(all_snippets)))

    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message) -> None:
        """Checks if the message has a snippet link, removes the embed, then sends the snippet contents."""
        if message.author.bot:
            return

        if not message.guild or message.guild.id in BLACKLIST:
            return

        message_to_send = await self._parse_snippets(message.content)
        destination = message.channel

        if 0 < len(message_to_send) <= 2000 and message_to_send.count("\n") <= 27:

            try:
                if message.channel.permissions_for(message.guild.me).manage_messages:
                    await message.edit(suppress_embeds=True)
            except disnake.NotFound:
                # Don't send snippets if the original message was deleted.
                return
            except disnake.Forbidden:
                # we're missing permissions to edit the message to remove the embed
                # its fine, since this bot is public and shouldn't require that.
                pass

            components = DeleteButton(message.author)
            await destination.send(message_to_send, components=components)

    @commands.Cog.listener("on_button_click")
    async def send_expanded_links(self, inter: disnake.MessageInteraction) -> None:
        """
        Send expanded links.

        Listener to send expanded links for a given issue/pull request.
        """
        if not inter.component.custom_id.startswith(EXPAND_BUTTON_PREFIX):
            return

        custom_id = inter.component.custom_id[len(EXPAND_BUTTON_PREFIX) :]
        link = decode_github_link(custom_id)
        snippet = await self._parse_snippets(link)

        def disable_button() -> disnake.ui.View:
            view = disnake.ui.View.from_message(inter.message)
            for comp in view.children:
                if custom_id in (getattr(comp, "custom_id", None) or ""):
                    comp.disabled = True
                    break
            return view

        view = disable_button()
        await inter.response.edit_message(view=view)

        if len(snippet) > 2000:
            await inter.followup.send(
                content="Sorry, this button shows a section of code that is too long.", ephemeral=True
            )
            return

        components = DeleteButton(inter.user)
        await inter.followup.send(snippet, components=components)


def setup(bot: Bot) -> None:
    """Load the CodeSnippets cog."""
    bot.add_cog(CodeSnippets(bot))
