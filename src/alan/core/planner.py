"""Intent detection and execution planning.

The planner turns a request into an explicit :class:`Plan` before anything runs,
so the orchestrator (and the user, via ``alan plan``) can see what is going to
happen. Two implementations share one interface:

* :class:`HeuristicPlanner` — keyword/rule based, needs no model, fully
  deterministic. This is the default so Alan works offline and tests are stable.
* :class:`ModelPlanner` — asks a routed model to classify the request, then
  still falls back to the heuristic result if the model is unavailable.

Both return the same :class:`Plan`, so nothing downstream cares which ran.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Sequence

from ..interfaces.model import ModelCategory, ModelProvider
from ..interfaces.types import RiskLevel


@dataclass
class Step:
    """One unit of planned work."""

    action: str            # "answer" | "recall" | "search_web" | "code" | ...
    description: str = ""
    arguments: dict = field(default_factory=dict)
    risk: RiskLevel = RiskLevel.SAFE

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "description": self.description,
            "arguments": self.arguments,
            "risk": str(self.risk),
        }


@dataclass
class Plan:
    """What Alan intends to do, and why."""

    goal: str
    steps: list[Step] = field(default_factory=list)
    category: ModelCategory = ModelCategory.GENERAL
    intent: str = "chat"
    complexity: int = 1
    needs_memory: bool = True
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "intent": self.intent,
            "category": self.category.value,
            "complexity": self.complexity,
            "needs_memory": self.needs_memory,
            "rationale": self.rationale,
            "steps": [s.to_dict() for s in self.steps],
        }

    def actions(self) -> list[str]:
        return [s.action for s in self.steps]


class Planner(ABC):
    """Turns a request into a plan."""

    name: str = "planner"

    @abstractmethod
    def plan(self, request: str, *, context: str = "") -> Plan:
        """Produce a plan. Must not raise for ordinary ambiguity."""


# -- heuristics -----------------------------------------------------------
#: Information-seeking requests that need the outside world. Deliberately narrow:
#: a bare "what is" matches ordinary reasoning questions ("what is 2 + 2"), so
#: misclassifying those as research would send a math question to a web search.
_RESEARCH = re.compile(
    r"\b(search|look up|find out|research|latest|news|docs|documentation|"
    r"price|weather|release notes|who is|who was|when did|when is|"
    r"what(?:'s| is) the (?:latest|current|new|best|first|price of)|"
    r"compare|versus|vs\.?)\b",
    re.I,
)
#: Mentions code, a repository or a test suite at all.
_CODING = re.compile(
    r"\b(fix|bug|debug|refactor|implement|add a|write a|test|compile|"
    r"rename|typo|patch|repo|repository|codebase|function|class)\b",
    re.I,
)
#: Asks for the code to *change*. Without one of these the plan is read-only:
#: a request to "find the test command" must not propose editing a file, however
#: good the model is at guessing what to change.
_CODING_CHANGE = re.compile(
    r"\b(fix|debug|refactor|implement|add|remove|delete|rename|update|"
    r"rewrite|migrate|replace|patch|upgrade|introduce|clean up|tidy|"
    r"write|create|change|edit|correct|port)\b",
    re.I,
)
#: Names Vibecoder directly. Checked before :data:`_CODING` so an explicit "ask
#: vibecoder to ..." is never reworded into something else.
_VIBECODER = re.compile(
    r"\b(vibecoder|vibe ?coder|my coding agent|use the coder)\b", re.I
)
#: "read math.js and tell me what it does" is a request to *read code*, not to
#: chat. It matched nothing above, so it degraded to ``intent=chat`` and the file
#: was never opened -- the user asked to be shown a file and got an opinion about
#: it from a model that could not see it.
#:
#: Deliberately narrow. A verb alone is far too broad ("read the docs" is a
#: web search, "explain this function" may be an ordinary question), so the match
#: requires a filename with a real source extension within a short distance of the
#: verb. That is what makes it safe: the pattern matches *a file*, not a topic.
#:
#: Named extensions only. ``\w+\.\w+`` would match "config.yaml.example" and, far
#: worse, "e.g." and "i.e." -- a request to explain a term would send Alan to a
#: file named after it.
_CODE_READ = re.compile(
    r"\b(read|open|look\s+at|show|explain|describe|walk\s+me\s+through|"
    r"summari[sz]e|review|check)\b[^.?!\n]{0,40}?"
    r"[\w./\\-]+\.(?:py|js|mjs|cjs|ts|tsx|jsx|java|kt|go|rs|rb|php|c|h|cc|cpp|"
    r"hpp|cs|swift|kt|sh|ps1|bat|sql|html|css|scss|vue|svelte|json|yaml|yml|"
    r"toml|ini|cfg|env|md|txt)\b",
    re.I,
)
_MEMORY = re.compile(
    r"\b(remember|recall|forget|my preference|i prefer|note that|"
    r"what do you know|do you remember)\b",
    re.I,
)
#: A request to do something *later*, on a schedule. Checked before the memory
#: intent, because "remember to water the plants every day" is an instruction
#: to schedule -- not a fact about the user to store -- and before research,
#: because "check the news every morning" is one recurring task, not one search.
#:
#: Deliberately explicit about time: every alternative carries a recurrence
#: word (every/daily/weekly) or a clock time. A bare "every" inside prose
#: ("every time I do this") is not a schedule, so it must be followed by a unit.
_SCHEDULE = re.compile(
    r"\b(?:schedule|scheduled|scheduling|reschedule|unschedule|"
    r"set (?:up )?(?:a )?(?:reminder|alarm)|make an? (?:reminder|alarm))\b"
    r"|\bremind\s+(?:me|you)\b[^.?!]{0,80}?\b(?:every|at\s+\d|tomorrow|tonight|"
    r"later|in\s+\d+\s*(?:minutes?|hours?|days?)|this\s+"
    r"(?:morning|afternoon|evening|night)|\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm))\b"
    r"|\beach\s+(?:\d+\s*)?(?:minute|hour|day|week|month)\b"
    r"|\b(?:once|twice)\s+a\s+(?:second|minute|hour|day|week|month)\b"
    r"|\bevery\s+(?:\d+\s*)?(?:seconds?|minutes?|hours?|days?|weeks?|months?|"
    r"mornings?|afternoons?|evenings?|nights?|noon|midnight)\b"
    r"|\b(?:daily|weekly)\s+at\s+\d"
    r"|\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b[^.?!]{0,40}?\bremind\b",
    re.I,
)
#: Explicit cancellation of a scheduled thing. Separate from _SCHEDULE so the
#: verb decides add vs remove rather than trying to infer it later.
_SCHEDULE_CANCEL = re.compile(
    r"\b(?:stop|cancel|remove|delete|drop|disable)\b[^.?!]{0,40}?"
    r"\b(?:reminders?|alarms?|schedule|scheduled task|recurring task|"
    r"the (?:daily|weekly|recurring) task)\b",
    re.I,
)
#: "what's my schedule" / "list my scheduled tasks" -- a request to *see* the
#: schedule rather than add to it. Only a few explicit viewing verbs count, and
#: "add X to my schedule" is excluded by the negative lookahead so the common
#: phrasing of *creating* something is never read as a request for a list.
_SCHEDULE_LIST = re.compile(
    r"\b(?:list|show|what(?:'s| is| are)|print|check|see)\b"
    r"(?:\s+\w+){0,3}\s+(?:my\s+|the\s+)?schedule\b"
    r"|\bmy (?:scheduled tasks|reminders|schedule)\b"
    r"(?!\s+(?:add|to))",
    re.I,
)
#: The timing phrase inside a schedule request, so the remainder can become the
#: task payload. Order matters: anchored forms ("every day at 07:30") must be
#: tried before the unanchored ones ("every day") or the anchor is swallowed by
#: the shorter match and the task drifts. 24-hour times need no am/pm suffix --
#: "at 07:30" is a real reading and refusing it would strand every user who
#: does not say "7:30 in the evening".
_WHEN = re.compile(
    r"(?:"
    r"every\s+\d+\s*(?:minutes?|hours?|days?|weeks?|months?)"
    r"|every\s+(?:day|week)(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?"
    r"|(?:daily|weekly)(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?"
    r"|(?:tomorrow|today)\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
    r"|at\s+(?:\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm))\b"
    r"|\b\d{1,2}:\d{2}\s*(?:am|pm)?\b"
    r"|(?:every|each)\s+(?:second|minute|hour|day|week|month)s?"
    r"|(?:every|each)\s+(?:morning|noon|afternoon|evening|night|midnight)"
    r"|(?:once|twice)\s+a\s+(?:second|minute|hour|day|week|month)"
    r"|in\s+\d+\s*(?:minutes?|hours?|days?|weeks?)"
    r"|\b(?:daily|weekly)\b"
    r")",
    re.I,
)
#: A request to *show* the user something, as opposed to being told about it.
#: Anchored to the start of the request because "open" is also an ordinary verb
#: mid-sentence, and an unanchored match would hijack unrelated asks.
#:
#: ``start`` and ``run`` are included but qualified below: "start the server" and
#: Politeness that may precede the verb. "please" was the only one, so "can you
#: open calc" -- an extremely ordinary way to ask -- was not recognised as a
#: request to open anything. Each form here is unambiguously a request wrapper:
#: none of them can begin a sentence that is *about* opening rather than asking
#: for it.
_OPEN_POLITE = (
    r"(?:please\s+"
    r"|(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"|i\s+(?:need|want)\s+you\s+to\s+"
    r"|i'?d\s+like\s+you\s+to\s+"
    r"|let'?s\s+|lets\s+)?"
)
_OPEN_VERB = re.compile(
    r"^(?P<polite>" + _OPEN_POLITE + r")"
    r"(?:open|launch|start|run|browse\s+to|go\s+to|visit|navigate\s+to|pull\s+up)"
    r"\b\s*(?P<target>.*)$",
    re.I,
)
#: Words that end the application name; what follows is a second request.
_OPEN_STOP = {
    "and", "then", "so", "because", "but", "tell", "show", "explain", "summarise",
    "summarize", "what", "why", "how", "who", "when", "describe", "read", "me",
}
#: Politeness and filler, stripped from either end of the name.
_OPEN_FILLER = {
    "please", "for", "me", "now", "thanks", "thank", "ok", "okay", "the", "a",
    "an", "my", "up", "just", "right", "app", "application", "website", "site",
    "page", "link",
}
#: A leading article is the difference between "open the file explorer" (an
#: application) and "run the tests" / "open the door" (ordinary English). It
#: cannot be resolved from spelling, so for these the target is only accepted as
#: an application when the operating system confirms one by that name.
_OPEN_DETERMINERS = {"the", "a", "an", "my", "our", "this", "that", "some", "your"}


def _is_launchable(target: str) -> bool:
    """Ask the operating system whether ``target`` is a program it can start.

    Only ever consulted for determiner-led requests, where spelling cannot decide
    ("open the file explorer" is an application, "run the tests" is not). The
    trade-off is deliberate and worth stating: a false negative ("open the
    youtube") falls through to a conversational answer, which is merely
    unhelpful, whereas a false positive ("run the tests") hijacks an engineering
    command and tries to launch a program named "tests". Refusing when unsure is
    the cheaper error.

    An *assumed* answer counts as unsure. :func:`alan.tools.opening.resolve`
    falls back to reading a bare word as a website name, so "tests" comes back
    as ``tests.com``. Treating that as evidence would defeat the entire check --
    it would confirm that every English noun is an application, since a name
    suffixed with ``.com`` always parses. Only a real hit counts.

    Anything unexpected -- a non-Windows host, no registry, an import failure --
    answers permissively, so this can only ever add open intents, never remove
    them for a reason other than a definite "not a program".
    """
    if not target:
        return False
    try:
        from ..tools.opening import resolve
    except Exception:  # noqa: BLE001 - never fail planning over this
        return True
    try:
        found = resolve(target)
    except (FileNotFoundError, ValueError):
        return False
    except Exception:  # noqa: BLE001 - platform or permission problem
        return True
    return not (isinstance(found, dict) and found.get("assumed"))


#: Leading connectives that join two requests: "open cmd and then start
#: vibecoder", "open x, then open y". :func:`_open_target` anchors its verb at the
#: start of the string, so these words would otherwise stop the second request
#: from being recognised -- and the whole point of the second attempt is that it
#: *is* a second request.
#:
#: Stripped only when re-parsing a remainder. The original wording is what the
#: ``answer`` step receives, so "open youtube and tell me what it is" still asks
#: its question in the user's own words.
_CONNECTIVE = re.compile(
    r"^(?:and(?:\s+then)?|then|also|plus|after\s+that|next|,\s*)\s+", re.I
)


#: A path ending in a source extension. "open src/core/planner.py" used to be
#: planned as a launch, which is wrong twice over: the OS cannot start it, and the
#: user plainly wants it *read*. Checked against the path's own extension rather
#: than the whole request, so "open notepad and read notes.txt" still opens
#: Notepad -- only a target that is itself a file is diverted.
_SOURCE_FILE = re.compile(
    r"\.("
    r"py|pyi|js|mjs|cjs|ts|tsx|jsx|java|kt|go|rs|rb|php|c|h|cc|cpp|hpp|cs|"
    r"swift|sh|ps1|bat|cmd|sql|html|css|scss|vue|svelte|json|yaml|yml|toml|"
    r"ini|cfg|conf|xml|md|txt|log|env"
    r")$",
    re.I,
)


def _looks_like_source(target: str) -> bool:
    """Whether ``target`` names a file to read rather than a program to start."""
    if not target or " " in target.strip():
        # A multi-word target is a name, not a path. "open visual studio code"
        # contains no dot anyway, but "open my notes.txt" is ambiguous and
        # launching the reader is the safer guess for anything with a space.
        return False
    return bool(_SOURCE_FILE.search(target.strip()))


def _strip_connective(text: str) -> str:
    """Remove joiners that precede a second request. Repeat, to allow two."""
    out = text.strip()
    while True:
        stripped = _CONNECTIVE.sub("", out, count=1)
        if stripped == out:
            return out
        out = stripped


def _open_target(text: str) -> tuple[str | None, str]:
    """Split "open youtube and tell me about it" into ("youtube", "tell me about it").

    Returns ``(None, "")`` when the verb is present but nothing usable follows.
    """
    match = _OPEN_VERB.match(text)
    if not match:
        return None, ""

    words = match.group("target").replace("?", " ").replace("!", " ").split()
    # Whether the request opened with an article has to be recorded *before* the
    # filler loop below discards it, otherwise "run the tests" is judged as the
    # bare noun "tests" and reads like a program name.
    led_with_determiner = bool(
        words and words[0].strip("\"'.,:").lower() in _OPEN_DETERMINERS
    )
    target_words: list[str] = []
    remainder_words: list[str] = []
    for index, word in enumerate(words):
        bare = word.strip("\"'.,:").lower()
        if not bare:
            continue
        if bare in _OPEN_STOP:
            remainder_words = words[index:]
            break
        target_words.append(word.strip("\"'.,:"))

    while target_words and target_words[-1].strip("\"'.,:").lower() in _OPEN_FILLER:
        target_words.pop()
    while target_words and target_words[0].strip("\"'.,:").lower() in _OPEN_FILLER:
        target_words.pop(0)

    target = " ".join(target_words).strip()
    if not target:
        return None, ""
    # A questioned request needs no extra test. The determiner rule below is
    # already the right guard for them, because that is exactly where a question
    # about ability lives: "can you open a socket", "could you open a file
    # descriptor". Demanding a *confirmed installed program* as well was tried and
    # rejected -- it broke "would you open youtube", which is not a program, even
    # though the user plainly wants a website opened. Requiring the OS to confirm
    # an app is the correct bar for a determiner, where spelling cannot decide, and
    # the wrong bar for a bare name.
    if led_with_determiner and not _is_launchable(target):
        return None, ""
    return target, " ".join(remainder_words).strip()


#: Politeness and request wrappers that may precede a scheduling verb, the
#: same family as _OPEN_POLITE: forms that can only begin a sentence *about*
#: asking, never a sentence that happens to mention scheduling.
_SCHEDULE_PREFIX = re.compile(
    r"^\s*(?:please\s+|"
    r"(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
    r"(?:i\s+(?:need|want)\s+you\s+to|i'?d\s+like\s+you\s+to)\s+)?",
    re.I,
)
#: The scheduling verbs themselves, removed from the payload after the timing
#: phrase is gone so "schedule a daily check of the news at 07:30" leaves
#: "a daily check of the news" rather than leaking the schedule words into the
#: task that will be executed every day.
_SCHEDULE_VERBS = re.compile(
    r"\b(?:schedule|reschedule|unschedule|set\s+(?:up\s+)?(?:a\s+)?"
    r"(?:reminder|alarm)(?:\s+for|\s+to)?|make\s+(?:an?\s+)?"
    r"(?:reminder|alarm)(?:\s+for)?)\b",
    re.I,
)


def _schedule_parts(text: str) -> tuple[str, str]:
    """Split a schedule request into ``(task payload, when phrase)``.

    Three shapes exist and each has its own reading:

    ``remind me ... to X``     the payload is what follows "to"
    ``schedule X <when>``      the payload is the request with the timing
                               phrase and the scheduling verbs removed
    ``<when> X`` (cancel etc.) the payload is what remains

    *Every* timing fragment the sentence contains is removed from the payload
    and joined into the when phrase: "schedule a backup at 07:30 daily" has two
    fragments, and dropping either would leave the clock inside the task that
    runs every day -- or, worse, lose the daily recurrence because only the
    time survived the extraction. ``parse_when`` accepts the join in any word
    order, so the join order does not matter.
    """
    matches = list(_WHEN.finditer(text))
    when = " ".join(m.group(0).strip() for m in matches)

    payload = text
    to_match = re.search(r"\bremind\s+(?:me|you)\b[^!?]*?\bto\b\s*(.+)", text, re.I)
    if to_match:
        payload = to_match.group(1)
    for fragment in matches:
        payload = payload.replace(fragment.group(0), " ")
    payload = _SCHEDULE_PREFIX.sub("", payload)
    payload = _SCHEDULE_VERBS.sub(" ", payload)
    payload = re.sub(r"\s{2,}", " ", payload).strip(" ,.-")
    if not payload:
        payload = text.strip()
    return payload, when


class HeuristicPlanner(Planner):
    """Deterministic intent classification. No model required."""

    name = "heuristic"

    def plan(self, request: str, *, context: str = "") -> Plan:
        text = (request or "").strip()
        steps: list[Step] = []

        # Schedule intent first: "remember to ..." is an instruction to do
        # something later (handled below), not a fact to store, and "check the
        # news every morning" is one recurring task rather than one search.
        if _SCHEDULE_CANCEL.search(text):
            return Plan(
                goal=f"Cancel scheduled work: {text[:80]}",
                steps=[Step("schedule_remove", "Stop matching scheduled tasks",
                            {"match": text}, RiskLevel.LOW_RISK)],
                category=ModelCategory.FAST,
                intent="schedule_remove",
                complexity=1,
                needs_memory=False,
                rationale="request cancels a reminder or scheduled task",
            )
        if _SCHEDULE.search(text):
            if _SCHEDULE_LIST.search(text):
                return Plan(
                    goal="Show scheduled tasks",
                    steps=[Step("schedule_list", "List the schedule", {},
                                RiskLevel.SAFE)],
                    category=ModelCategory.FAST,
                    intent="schedule_list",
                    complexity=1,
                    needs_memory=False,
                    rationale="request asks to see the current schedule",
                )
            payload, when = _schedule_parts(text)
            steps = [
                Step("schedule_add", f"Schedule: {payload[:60]}",
                     {"task": payload, "when": when}, RiskLevel.LOW_RISK),
            ]
            return Plan(
                goal=f"Schedule: {payload[:80]}",
                steps=steps,
                category=ModelCategory.FAST,
                intent="schedule",
                complexity=1,
                needs_memory=False,
                rationale="request names a recurrence or clock time",
            )

        # Memory intent: an explicit store/forget is the whole task.
        if re.search(r"\b(remember|note that)\b", text, re.I) or _MEMORY.search(text):
            intent = "memory"
            steps = [
                Step("recall", "Check what is already known", {"query": text},
                     RiskLevel.SAFE),
                Step("remember", "Store the new fact", {"text": text},
                     RiskLevel.LOW_RISK),
            ]
            return Plan(
                goal=f"Update memory: {text[:80]}",
                steps=steps,
                category=ModelCategory.FAST,
                intent=intent,
                complexity=1,
                rationale="request contains an explicit memory verb",
            )

        if re.search(r"\bforget\b|\bdelete (my |the )?memor", text, re.I):
            return Plan(
                goal=f"Forget stored memory: {text[:80]}",
                steps=[Step("forget", "Erase matching memory", {"text": text},
                            RiskLevel.HIGH_RISK)],
                category=ModelCategory.FAST,
                intent="memory",
                complexity=1,
                rationale="request asks to erase memory",
                needs_memory=False,
            )

        target, remainder = _open_target(text)
        if target and _looks_like_source(target):
            # "open src/core/planner.py" is a request to read a file. Launching it
            # would fail, and answering about it from memory would be a guess.
            return Plan(
                goal=f"Read {target}",
                steps=[
                    Step("vibecoder_inspect", f"Read {target} and explain it",
                         {"task": f"Read {target} and explain what it does. "
                                  f"Report what the file actually contains.",
                          "cwd": "", "read_only": True}, RiskLevel.LOW_RISK),
                    Step("vibecoder_report", "Report what was found", {},
                         RiskLevel.SAFE),
                ],
                category=ModelCategory.CODING,
                intent="vibecoder_inspect",
                complexity=2,
                rationale="target is a source file, so it is read and explained "
                          "rather than launched",
            )
        if target:
            steps = [
                Step("open_target", f"Open {target}", {"target": target},
                     RiskLevel.MEDIUM_RISK),
            ]
            # "open youtube and tell me what it is" is two requests. Dropping the
            # second because the first was recognised would quietly lose it.
            if remainder:
                # "open cmd and then start vibecoder" is also two requests, and
                # the second one is not an answer to be written -- it is another
                # thing to open. Routing it to `answer` silently downgraded
                # "start vibecoder" into a paragraph about Vibecoder. Re-run the
                # remainder through the same splitter before falling back to a
                # reply, so the capability is used instead of discarded.
                # Only one extra level: "open x, open y and tell me z" still ends
                # as a reply, which is the honest reading of three requests.
                second, third = _open_target(_strip_connective(remainder))
                if second:
                    steps.append(
                        Step("open_target", f"Open {second}",
                             {"target": second}, RiskLevel.MEDIUM_RISK)
                    )
                    if third:
                        steps.append(
                            Step("answer", f"Also answer: {third}",
                                 {"query": third}, RiskLevel.SAFE)
                        )
                else:
                    steps.append(
                        Step("answer", f"Also answer: {remainder}",
                             {"query": remainder}, RiskLevel.SAFE)
                    )
            return Plan(
                goal=f"Open {target}",
                steps=steps,
                category=ModelCategory.FAST,
                intent="open",
                complexity=1 + len(steps) - 1,
                rationale="request begins with a verb that opens something",
            )

        if _CODING.search(text) or _VIBECODER.search(text) or _CODE_READ.search(text):
            wants_change = bool(_CODING_CHANGE.search(text))
            # Alan does not write code. The local model is a 1.5b one, and a
            # plausible-looking edit from a model that small is worse than no
            # edit at all, because the user cannot tell the difference by
            # reading the diff. Vibecoder runs a 550B model behind a real agent
            # loop with tools, so it does the work and Alan reports on it.
            #
            # The old plan (analyze -> code -> run_tests -> review_diff) is what
            # this replaces. Its `code` step invoked Alan's own model to write a
            # file; `run_tests` and `review_diff` then let Alan's model narrate
            # work it could not actually check.
            delegate_action = ("vibecoder_delegate" if wants_change
                               else "vibecoder_inspect")
            steps = [
                Step("recall", "Load relevant project memory", {"query": text},
                     RiskLevel.SAFE),
                Step(
                    delegate_action,
                    ("Let Vibecoder make the change and run the tests"
                     if wants_change
                     else "Let Vibecoder inspect the code, read-only"),
                    # cwd is deliberately empty rather than ".". The directory is
                    # resolved once, by the tool, from settings -- and refusing to
                    # guess here is what stops a vague request being applied to
                    # whichever project happened to be current.
                    {"task": text, "cwd": "", "read_only": not wants_change},
                    RiskLevel.MEDIUM_RISK if wants_change else RiskLevel.LOW_RISK,
                ),
                # Always last. The report is the deliverable, and it is a
                # separate step so the honesty contract is structural: a plan
                # that delegates has no way to skip saying what was and was not
                # done.
                Step("vibecoder_report", "Report what is verified and what is not",
                     {}, RiskLevel.SAFE),
            ]
            complexity = 4 if len(text.split()) > 25 else 3
            return Plan(
                goal=(f"Modify code: {text[:80]}" if wants_change
                      else f"Inspect code: {text[:80]}"),
                steps=steps,
                category=ModelCategory.CODING,
                intent="vibecoder_delegate" if wants_change else "vibecoder_inspect",
                complexity=complexity,
                rationale=(
                    "request asks for a code change, so it is delegated to Vibecoder "
                    "and reported, not written by Alan"
                    if wants_change
                    else "request is about code but asks for no change; read-only "
                         "delegation"
                ),
            )

        if _RESEARCH.search(text):
            steps = [
                Step("recall", "Load relevant memory", {"query": text},
                     RiskLevel.SAFE),
                Step("search_web", "Search the web", {"query": text},
                     RiskLevel.LOW_RISK),
                Step("summarize", "Summarise with sources", {}, RiskLevel.SAFE),
            ]
            return Plan(
                goal=f"Research: {text[:80]}",
                steps=steps,
                category=ModelCategory.REASONING,
                intent="research",
                complexity=3,
                rationale="request asks for external information",
            )

        # Default: a conversational turn, but still consult memory.
        steps = [Step("answer", "Answer from context and memory", {"query": text},
                      RiskLevel.SAFE)]
        return Plan(
            goal=text[:120] or "Respond to the user",
            steps=steps,
            category=ModelCategory.GENERAL,
            intent="chat",
            complexity=2 if len(text.split()) > 30 else 1,
            rationale="no tool-specific intent detected",
        )


class ModelPlanner(Planner):
    """Model-assisted classification layered over the heuristic.

    Deliberately does not *replace* the heuristic: it only reclassifies the
    intent and may add a step. That keeps behaviour predictable when the model
    is wrong, which is the common case.
    """

    name = "model"

    _PROMPT = (
        "Classify the request. Answer with exactly one word: "
        "chat, memory, research, coding, or open.\n\nRequest: {request}"
    )

    def __init__(self, model: ModelProvider, fallback: Planner | None = None) -> None:
        self.model = model
        self.fallback = fallback or HeuristicPlanner()

    def plan(self, request: str, *, context: str = "") -> Plan:
        base = self.fallback.plan(request, context=context)
        try:
            reply = self.model.chat(
                [{"role": "user", "content": self._PROMPT.format(request=request[:600])}]
            )
        except Exception:
            return base  # model down -> deterministic behaviour

        verdict = (reply or "").strip().lower()
        for candidate in ("memory", "research", "coding", "open", "chat"):
            if verdict.startswith(candidate):
                if candidate != base.intent:
                    # A recurrence request is not up for reclassification: the
                    # model is offered no "schedule" word in its prompt, so a
                    # mismatch here means confusion, not insight -- and
                    # relabelling schedule steps as "chat" would strip them
                    # from the tool catalog the model is shown.
                    if base.intent.startswith("schedule"):
                        return base
                    replanned = self.fallback.plan(request, context=context)
                    replanned.intent = candidate
                    replanned.rationale = (
                        f"{base.rationale}; model classified as {candidate}"
                    )
                    return replanned
                return base
        return base
