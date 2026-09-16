"""What is this question asking for, decided in code before anything is read.

Two jobs, and the first one is a refusal.

**Interpretation is refused at the question, before retrieval.** "Is my thyroid
result serious" is not a question about the record; it is a question about what
the record means, and answering it is the single worst thing this project could
produce — a small model reassuring somebody about a number. CLAUDE.md puts the
line here deliberately: "Draw the line at the question, before retrieval, where
it is cheap and legible." Cheap because nothing has been read yet; legible
because the whole rule is one table of phrasings that can be read and argued
with, rather than a judgement made somewhere inside a prompt.

The hard part of that table is not what it catches. It is what it must let
through. "What am I taking for my blood pressure" is a question about the
record, and refusing it because it contains the words "blood pressure" would
make the feature useless at exactly the thing it is for. Every pattern below is
written tight enough that the passing counterexamples beside it in
``tests/test_query_classify.py`` still pass.

**Then: what to retrieve.** Not one shape but a set of facets, because real
questions carry several. "What dose of perindopril did Dr Nguyen start me on
last year" names an entity, a person, a predicate and a date range, and a
classifier that picked one winner would drop three quarters of the question.
Nothing here calls a model.

**Follow-ups inherit.** "What did Dr Nguyen recommend?" then "when was that?" is
how people ask. The second question carries no facets of its own, so it takes
the previous turn's — which is the conversation having a memory, not the model
refining across rounds. The generation count is unchanged: one per question.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Sequence

from ..projection import dates as dates_mod
from .text import content_words, fold, words
from .vocab import Vocabulary

#: How many questions one conversation may carry before it starts again. The
#: current question is the last of them, so at most two earlier turns reach the
#: classifier and the prompt.
MAX_TURNS = 3
MAX_PRIOR_TURNS = MAX_TURNS - 1

#: Longer than any question anyone types, short enough that a pasted document
#: cannot arrive here as a "question". Refused with a sentence, not truncated.
MAX_QUESTION_CHARS = 500

# -- shapes ------------------------------------------------------------------

ENTITY = "entity"
KIND = "kind"
PERSON = "person"
PREDICATE = "predicate"
RECENCY = "recency"
ARTEFACT = "artefact"
CHANGES = "changes"
COUNT = "count"
TERMS = "terms"


@dataclass(frozen=True)
class Window:
    """A date range a question asked for, and how to say it back."""

    start: date
    end: date
    label: str


@dataclass(frozen=True)
class Question:
    """One question, parsed. Everything retrieval needs and nothing it does not."""

    text: str
    shapes: frozenset[str] = frozenset()
    entities: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    predicates: tuple[str, ...] = ()
    tiers: tuple[str, ...] = ()
    window: Window | None = None
    terms: tuple[str, ...] = ()
    counting: bool = False
    changes: bool = False
    #: Set when this question was answered from the previous turn's facets.
    inherited: bool = False
    #: Set when the question asks what something means. Nothing is retrieved.
    refusal: str | None = None

    @property
    def is_refused(self) -> bool:
        return self.refusal is not None

    @property
    def has_facets(self) -> bool:
        return bool(
            self.entities
            or self.kinds
            or self.predicates
            or self.tiers
            or self.window
            or self.terms
            or self.changes
        )


@dataclass(frozen=True)
class Turn:
    """One earlier exchange in this conversation."""

    question: str
    #: The answer as it was shown, already validated and citation-bound. Only
    #: text that survived rendering is ever carried forward.
    answer: str = ""
    facets: Question | None = field(default=None, repr=False)


# -- the refusal -------------------------------------------------------------

#: Why a question was refused, in the words the screen uses. Fixed sentences
#: selected by a code, never assembled from the question.
MEANING = "meaning"
SERIOUSNESS = "seriousness"
ADVICE = "advice"
CAUSE = "cause"
OPINION = "opinion"
GENERAL = "general"

#: Ordered. The first match decides, so the more specific patterns come first.
#: Each entry is ``(pattern, code)`` and each pattern is matched against the
#: *folded* question — lowercase, punctuation stripped, single-spaced — so the
#: patterns never have to carry punctuation classes.
_INTERPRETATION: tuple[tuple[re.Pattern[str], str], ...] = (
    # What something means. "what does my record say" is not this, and the
    # negative lookahead is what keeps it out.
    (re.compile(r"\bwhat (does|do|did) (?!.*\b(say|said|state|mention|record)\b).*\bmean\b"), MEANING),
    (re.compile(r"\bwhat (is|are) the meaning\b"), MEANING),
    (re.compile(r"\bmeaning of\b"), MEANING),
    (re.compile(r"\bexplain (what|why|how)\b"), MEANING),
    (re.compile(r"\bwhat (is|are) (a |an |the )?(normal|healthy|safe) (range|level|reading|dose)"), MEANING),
    # Asking whether an action is safe is asking what to do, so it is matched
    # here rather than by the "is this serious" pattern below, which would
    # otherwise claim it first and answer with the wrong sentence.
    (re.compile(r"\b(is|would) it (safe|ok|okay|alright|better|worse)\b"), ADVICE),
    # Whether something is serious. Deliberately includes the bare "should I be
    # worried", which is the sentence this whole feature is built to not answer.
    (re.compile(r"\bshould i (be )?(worry|worried|concerned)\b"), SERIOUSNESS),
    (re.compile(r"\b(is|are|was|were) (this|that|it|these|those|my|his|her|their|the)\b.*\b(serious|dangerous|bad|normal|abnormal|ok|okay|fine|safe|healthy|concerning|worrying|high|low|too high|too low)\b"), SERIOUSNESS),
    (re.compile(r"\bhow (serious|bad|dangerous|worried|concerning)\b"), SERIOUSNESS),
    (re.compile(r"\b(do|should) i need to (worry|see|call)\b"), SERIOUSNESS),
    (re.compile(r"\bis (it|this|that) (a lot|too much|too many|enough)\b"), SERIOUSNESS),
    (re.compile(r"\b(is|are) \d"), SERIOUSNESS),
    # What to do. "what did the letter say to do" is a record question and the
    # lookbehind on a past-tense source keeps it out of this.
    (re.compile(r"\bwhat should i\b"), ADVICE),
    (re.compile(r"\bshould i (take|stop|start|keep|change|increase|reduce|see|ask)\b"), ADVICE),
    (re.compile(r"\bwhat (can|do) i do about\b"), ADVICE),
    (re.compile(r"\b(can|could|should) i (stop|start|take|double|halve|skip|split)\b"), ADVICE),
    (re.compile(r"\bhow (do|should|can) i (treat|manage|fix|cure|lower|raise)\b"), ADVICE),
    (re.compile(r"\bwhat (happens|would happen) if i\b"), ADVICE),
    (re.compile(r"\bdo i need (to see|a|an|any)\b"), ADVICE),
    (re.compile(r"\b(interact|interaction|interactions) with\b"), ADVICE),
    # Why something is happening. "why did the dose change" is a record
    # question — the record may literally say — so only the bodily ones refuse.
    (re.compile(r"\bwhy (do|does|did|am|is|are) (i|my)\b"), CAUSE),
    (re.compile(r"\bwhat (causes|caused|is causing)\b"), CAUSE),
    (re.compile(r"\bcould (this|that|it|they) be\b"), CAUSE),
    (re.compile(r"\bam i at risk\b"), CAUSE),
    (re.compile(r"\b(diagnose|diagnosing) me\b"), CAUSE),
    # The model's own view of anything.
    (re.compile(r"\b(do|what do) you (think|reckon|recommend|suggest|advise)\b"), OPINION),
    (re.compile(r"\byour (opinion|advice|view)\b"), OPINION),
    (re.compile(r"\bwhat would you\b"), OPINION),
    # General knowledge wearing a record question's clothes.
    (re.compile(r"\bside effects of\b"), GENERAL),
    (re.compile(r"\bwhat (is|are) (a |an |the )?\w+ (used|prescribed|taken) for\b"), GENERAL),
    (re.compile(r"\bwhat (is|are) \w+ for\b"), GENERAL),
)

#: One sentence per code. No interpolation from the question, because a refusal
#: that quotes the question back is a refusal that can be made to say anything.
REFUSALS: dict[str, str] = {
    MEANING: (
        "This asks what something means, and your record cannot answer that. It "
        "holds what your documents say, not what they signify."
    ),
    SERIOUSNESS: (
        "This asks whether something is serious, and that is not a question your "
        "record can answer. It can tell you what your documents say and when "
        "they said it."
    ),
    ADVICE: (
        "This asks what to do, and your record does not advise. It can tell you "
        "what you have been prescribed and what your documents say about it."
    ),
    CAUSE: (
        "This asks why something is happening, and your record cannot answer "
        "that. It holds what has been written down, not the reason behind it."
    ),
    OPINION: (
        "This asks for an opinion, and there is none to give here. Your record "
        "reports what your documents say and nothing else."
    ),
    GENERAL: (
        "This asks about medicines in general rather than about your record. "
        "Nothing here draws on anything outside your own documents."
    ),
}

#: Shown under every refusal, pointing at the thing that does help. The
#: consultation summary is the answer to "I want to ask somebody about this":
#: one sourced page, with the patient's own question printed on it.
REFUSAL_NEXT = (
    "Ask a person. Take a sheet to your appointment — one page, every line "
    "saying which of your documents it came from, ending with the question you "
    "came to ask."
)


def refusal_for(question: str) -> str | None:
    """The code for why this question is refused, or ``None`` to proceed."""
    folded = fold(question)
    if not folded:
        return None
    for pattern, code in _INTERPRETATION:
        if pattern.search(folded):
            return code
    return None


# -- facets ------------------------------------------------------------------

#: Words that name a whole shelf of the record rather than one thing on it.
_KIND_WORDS: tuple[tuple[str, str], ...] = (
    ("medications", "med"), ("medication", "med"), ("medicines", "med"),
    ("medicine", "med"), ("meds", "med"), ("drugs", "med"), ("tablets", "med"),
    ("tablet", "med"), ("pills", "med"), ("pill", "med"),
    ("prescriptions", "med"), ("prescription", "med"), ("scripts", "med"),
    ("script", "med"), ("taking", "med"), ("take", "med"),
    ("allergies", "allergy"), ("allergy", "allergy"), ("allergic", "allergy"),
    ("intolerant", "allergy"), ("intolerance", "allergy"),
    ("problems", "problem"), ("problem", "problem"), ("conditions", "problem"),
    ("condition", "problem"), ("diagnoses", "problem"), ("diagnosis", "problem"),
    ("diagnosed", "problem"), ("treated", "problem"),
    ("doctors", "person"), ("doctor", "person"), ("gp", "person"),
    ("specialist", "person"), ("specialists", "person"),
    ("practitioner", "person"), ("practitioners", "person"),
    ("clinician", "person"), ("consultant", "person"), ("surgeon", "person"),
    # "Who prescribed them", "who told me to stop", "who is my GP". A question
    # beginning "who" is a question about a person, and without this one it
    # retrieved nothing at all — the record holds the practitioner and the
    # letter, and answered "nothing in your record covers that".
    ("who", "person"),
)

#: Words that name a slot rather than a subject.
_PREDICATE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dose", ("dose",)), ("doses", ("dose",)), ("dosage", ("dose",)),
    ("strength", ("dose",)), ("mg", ("dose",)), ("milligrams", ("dose",)),
    ("frequency", ("frequency",)),
    ("reaction", ("reaction",)), ("reactions", ("reaction",)),
    ("route", ("route",)),
    ("started", ("started", "status")), ("start", ("started", "status")),
    ("starting", ("started", "status")), ("commenced", ("started",)),
    ("stopped", ("status",)), ("stop", ("status",)), ("ceased", ("status",)),
    ("onset", ("onset",)), ("since", ("started", "onset")),
    ("role", ("role",)), ("contact", ("contact",)),
)

#: Phrases that name a slot but are two words, so they never survive the
#: single-word scan above.
_PREDICATE_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("how much", ("dose",)),
    ("how often", ("frequency",)),
    ("how many milligrams", ("dose",)),
    ("what dose", ("dose",)),
    ("allergic to", ("reaction", "name")),
)

#: A kind of document, and the evidence tier that identifies it in the log.
#: There is no "kind of artefact" recorded anywhere — what a document *is* is
#: the model's reading of it — so the tier is what a question about "the blood
#: test" actually selects, and it selects it exactly.
_ARTEFACT_WORDS: tuple[tuple[str, str], ...] = (
    ("blood test", "lab-issued"), ("blood tests", "lab-issued"),
    ("bloods", "lab-issued"), ("pathology", "lab-issued"),
    ("lab", "lab-issued"), ("labs", "lab-issued"),
    ("results", "lab-issued"), ("result", "lab-issued"),
    ("scan", "lab-issued"), ("imaging", "lab-issued"), ("xray", "lab-issued"),
    ("letter", "prescriber-issued"), ("letters", "prescriber-issued"),
    ("referral", "prescriber-issued"), ("discharge", "prescriber-issued"),
    ("summary", "prescriber-issued"),
    ("script", "prescriber-issued"), ("scripts", "prescriber-issued"),
    ("prescription", "prescriber-issued"), ("prescriptions", "prescriber-issued"),
    ("voice note", "patient-reported"), ("voice notes", "patient-reported"),
    ("recording", "patient-reported"), ("recordings", "patient-reported"),
    ("note", "patient-reported"), ("notes", "patient-reported"),
    ("said out loud", "patient-reported"),
)

#: "What changed" is its own retrieval: the slots that hold more than one
#: reading, and the readings a correction replaced. "since" is deliberately not
#: here — it is a date word, and it is already read as one.
_CHANGE_WORDS = frozenset(
    {"changed", "change", "changes", "different", "corrected", "correction",
     "updated"}
)

_COUNT_PHRASES = ("how many", "how often", "how much of the time", "number of times")

#: Words about the act of asking, which are never content in a health record.
#:
#: Dropped from the literal terms, and the reason is sharper than "they are
#: common". The timeline writes its own rows — "An audio recording was added to
#: the record" — so a question containing the word "record" matches the *app's*
#: phrasing rather than anything in the folder. "What does my record say about
#: alcohol" came back with three unrelated rows for exactly that reason, which
#: is worse than an empty answer: it looks like a considered one.
_META_WORDS = frozenset(
    """
    record records document documents file files folder added say says said
    saying tell telling told show shows showed mention mentions mentioned
    anything everything something thing things entry entries know contain
    contains read
    """.split()
)

#: A follow-up says nothing about what it is about. These are the words that
#: point back rather than forward.
_ANAPHORA = frozenset(
    {"that", "it", "this", "those", "these", "them", "they", "he", "she", "him",
     "her", "his", "hers", "their", "theirs", "one", "ones", "same", "then"}
)

_CONTINUATION_OPENERS = (
    "and ", "what about", "how about", "and what", "and when", "and who",
    "also ", "but ", "ok and", "okay and",
)


def _kinds(tokens: Sequence[str]) -> tuple[str, ...]:
    found: list[str] = []
    for token in tokens:
        for word, kind in _KIND_WORDS:
            if token == word and kind not in found:
                found.append(kind)
    return tuple(found)


def _predicates(folded: str, tokens: Sequence[str]) -> tuple[str, ...]:
    found: list[str] = []
    for phrase, predicates in _PREDICATE_PHRASES:
        if phrase in folded:
            found.extend(p for p in predicates if p not in found)
    for token in tokens:
        for word, predicates in _PREDICATE_WORDS:
            if token == word:
                found.extend(p for p in predicates if p not in found)
    return tuple(found)


def _tiers(folded: str) -> tuple[str, ...]:
    found: list[str] = []
    for phrase, tier in _ARTEFACT_WORDS:
        if f" {phrase} " in f" {folded} " and tier not in found:
            found.append(tier)
    return tuple(found)


# -- date windows ------------------------------------------------------------

_MONTHS = {name.lower(): number for number, name in enumerate(dates_mod.MONTH_NAMES, 1)}
_MONTHS.update({name.lower()[:3]: number for number, name in enumerate(dates_mod.MONTH_NAMES, 1)})

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "a": 1, "an": 1, "couple": 2, "few": 3,
}

_RELATIVE = re.compile(
    r"\b(?:in |over |during |within )?(?:the )?(?:last|past|previous|recent) "
    r"(\w+) (day|days|week|weeks|month|months|year|years)\b"
)

_SINCE = re.compile(r"\bsince (\w+)(?: (\d{4}))?\b")

_MONTH_YEAR = re.compile(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b(?: (\d{4}))?")

_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _month_window(year: int, month: int) -> Window:
    last = calendar.monthrange(year, month)[1]
    return Window(
        start=date(year, month, 1),
        end=date(year, month, last),
        label=dates_mod.render_month(year, month),
    )


def window_for(question: str, today: date) -> Window | None:
    """The date range a question asked for, computed in code.

    Every branch is arithmetic on ``today``, which the caller supplies from the
    snapshot's ``as_of``. Nothing here reads a clock, so the same question
    against the same snapshot selects the same rows on any machine — the
    property the whole projection is built on, extended to retrieval.
    """
    folded = fold(question)
    if not folded:
        return None

    if " today " in f" {folded} ":
        return Window(today, today, "today")
    if " yesterday " in f" {folded} ":
        day = today - timedelta(days=1)
        return Window(day, day, "yesterday")

    match = _RELATIVE.search(folded)
    if match:
        count_word, unit = match.group(1), match.group(2)
        count = _NUMBER_WORDS.get(count_word)
        if count is None and count_word.isdigit():
            count = int(count_word)
        if count is not None and 0 < count <= 120:
            days = {"day": 1, "week": 7, "month": 30, "year": 365}[unit.rstrip("s")]
            span = count * days
            return Window(today - timedelta(days=span), today, f"the last {count_word} {unit}")

    if re.search(r"\blast month\b", folded):
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        return _month_window(end.year, end.month)
    if re.search(r"\bthis month\b", folded):
        return _month_window(today.year, today.month)
    if re.search(r"\blast week\b", folded):
        return Window(today - timedelta(days=13), today - timedelta(days=7), "last week")
    if re.search(r"\bthis week\b", folded):
        return Window(today - timedelta(days=6), today, "this week")
    if re.search(r"\blast year\b", folded):
        year = today.year - 1
        return Window(date(year, 1, 1), date(year, 12, 31), str(year))
    if re.search(r"\bthis year\b", folded):
        return Window(date(today.year, 1, 1), today, str(today.year))
    if re.search(r"\b(recently|lately|just now|of late)\b", folded):
        return Window(today - timedelta(days=90), today, "the last three months")

    since = _SINCE.search(folded)
    if since:
        month = _MONTHS.get(since.group(1))
        if month is not None:
            year = int(since.group(2)) if since.group(2) else _implied_year(today, month)
            return Window(date(year, month, 1), today, f"since {dates_mod.render_month(year, month)}")
        if since.group(1).isdigit() and len(since.group(1)) == 4:
            year = int(since.group(1))
            return Window(date(year, 1, 1), today, f"since {year}")

    month_match = _MONTH_YEAR.search(folded)
    if month_match and not _is_the_modal_may(folded, month_match):
        month = _MONTHS[month_match.group(1)]
        year = int(month_match.group(2)) if month_match.group(2) else _implied_year(today, month)
        return _month_window(year, month)

    year_match = _YEAR.search(folded)
    if year_match:
        year = int(year_match.group(1))
        return Window(date(year, 1, 1), date(year, 12, 31), str(year))
    return None


def _is_the_modal_may(folded: str, match: re.Match[str]) -> bool:
    """Whether "may" here is the verb rather than the month.

    The only month name that is also an ordinary English word in a question.
    Treated as the month when a year follows it or a temporal preposition
    precedes it, and as the verb otherwise — the failure that matters is
    silently filtering a whole record down to one month of it because somebody
    wrote "may".
    """
    if match.group(1) != "may":
        return False
    if match.group(2):
        return False
    before = folded[: match.start()].split()
    return not (before and before[-1] in {"in", "during", "of", "from", "since", "until", "before", "after"})


def _implied_year(today: date, month: int) -> int:
    """"In June" means the most recent June, which may be last year.

    Never a future one: a record holds what has happened, and reading "June" as
    a June that has not arrived yet would return nothing and look broken.
    """
    return today.year if month <= today.month else today.year - 1


# -- putting it together -----------------------------------------------------


def _terms(question: str, taken: Sequence[str]) -> tuple[str, ...]:
    """Content words the question carries that nothing else claimed.

    Matched literally against the record later. Capped, and stopwords and words
    already consumed by another facet are dropped, so "what did the letter say
    about the headaches" searches for "headaches" rather than for "letter".
    """
    spent = {word for phrase in taken for word in words(phrase)}
    found: list[str] = []
    for word in content_words(question):
        if word in spent or word in found or word in _META_WORDS or len(word) < 3:
            continue
        found.append(word)
    return tuple(found[:8])


def _is_continuation(question: str) -> bool:
    folded = fold(question)
    if not folded:
        return False
    if any(folded.startswith(opener.strip()) and " " in folded for opener in _CONTINUATION_OPENERS):
        return True
    tokens = words(question)
    if not tokens:
        return False
    # Short, and pointing at something already said. Both halves matter: "when
    # was that" inherits, "when did I start metformin" does not.
    return len(tokens) <= 8 and any(token in _ANAPHORA for token in tokens)


def classify(
    question: str,
    vocabulary: Vocabulary,
    today: date,
    history: Sequence[Turn] = (),
) -> Question:
    """Parse one question. No model call, no retrieval, no clock read."""
    text = (question or "").strip()
    if not text:
        return Question(text="", refusal=None)

    code = refusal_for(text)
    if code is not None:
        return Question(text=text, refusal=code)

    folded = fold(text)
    tokens = words(text)

    entities = vocabulary.mentions(text)
    kinds = _kinds(tokens)
    predicates = _predicates(folded, tokens)
    tiers = _tiers(folded)
    window = window_for(text, today)
    changes = any(token in _CHANGE_WORDS for token in tokens)
    counting = any(phrase in folded for phrase in _COUNT_PHRASES)

    claimed = [vocabulary.display.get(subject, "") for subject in entities]
    claimed.extend(word for word, _ in _KIND_WORDS)
    claimed.extend(word for word, _ in _PREDICATE_WORDS)
    claimed.extend(phrase for phrase, _ in _ARTEFACT_WORDS)
    terms = _terms(text, claimed)

    parsed = Question(
        text=text,
        entities=entities,
        kinds=kinds,
        predicates=predicates,
        tiers=tiers,
        window=window,
        terms=terms,
        counting=counting,
        changes=changes,
    )

    if not parsed.has_facets or (_is_continuation(text) and not entities and not kinds):
        parsed = _inherit(parsed, history)

    return replace(parsed, shapes=_shapes(parsed))


def _inherit(parsed: Question, history: Sequence[Turn]) -> Question:
    """Take the previous turn's subject, keeping this question's own slots.

    Only the *subject* facets are inherited — which entities, kinds, people and
    documents were being discussed. The predicate, the date window and the
    literal terms belong to the question that was actually asked: "when was
    that" is asking about a date, and carrying the previous turn's "dose" into
    it would answer the earlier question a second time.
    """
    for turn in reversed(list(history)[-MAX_PRIOR_TURNS:]):
        previous = turn.facets
        if previous is None or not previous.has_facets:
            continue
        return replace(
            parsed,
            entities=parsed.entities or previous.entities,
            kinds=parsed.kinds or previous.kinds,
            tiers=parsed.tiers or previous.tiers,
            terms=parsed.terms or previous.terms,
            inherited=True,
        )
    return parsed


def _shapes(parsed: Question) -> frozenset[str]:
    shapes: set[str] = set()
    if parsed.entities:
        shapes.add(ENTITY)
        if any(subject.startswith("person:") for subject in parsed.entities):
            shapes.add(PERSON)
    if parsed.kinds:
        shapes.add(KIND)
        if "person" in parsed.kinds:
            shapes.add(PERSON)
    if parsed.predicates:
        shapes.add(PREDICATE)
    if parsed.window:
        shapes.add(RECENCY)
    if parsed.tiers:
        shapes.add(ARTEFACT)
    if parsed.changes:
        shapes.add(CHANGES)
    if parsed.counting:
        shapes.add(COUNT)
    if parsed.terms:
        shapes.add(TERMS)
    return frozenset(shapes)


__all__ = [
    "MAX_PRIOR_TURNS",
    "MAX_QUESTION_CHARS",
    "MAX_TURNS",
    "Question",
    "REFUSALS",
    "REFUSAL_NEXT",
    "Turn",
    "Window",
    "classify",
    "refusal_for",
    "window_for",
]
