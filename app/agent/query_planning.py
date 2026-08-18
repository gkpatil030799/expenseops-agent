from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Literal

from app.agent.contracts import AgentPageContext, AgentSurface
from app.services.temporal_range_service import (
    ResolvedDateRange,
    TemporalPreset,
    configured_zone,
    resolve_temporal_range,
)

MAX_AGENT_TOP_N = 10
DEFAULT_AGENT_TOP_N = 5
MAX_LIFESTYLE_TOP_N = 8


class QueryObjective(StrEnum):
    TOTAL_SPEND = "total_spend"
    COMPARE_SPENDING = "compare_spending"
    TOP_CATEGORIES = "top_categories"
    TOP_MERCHANTS = "top_merchants"
    TRANSACTION_LIST = "transaction_list"
    AVERAGE_CHECK = "average_check"
    CHANGE_EXPLANATION = "change_explanation"
    LIFESTYLE_TOTAL = "lifestyle_total"
    LIFESTYLE_FREQUENCY = "lifestyle_frequency"
    RECENT_LEARNING = "recent_learning"
    LEARNING_SUMMARY = "learning_summary"
    UNCERTAIN_CLASSIFICATIONS = "uncertain_classifications"
    REPLENISHMENT_DUE = "replenishment_due"
    RECEIPT_STATUS = "receipt_status"
    ATTENTION_SUMMARY = "attention_summary"


class QueryDomain(StrEnum):
    SPENDING = "spending"
    LIFESTYLE = "lifestyle"
    TRANSACTIONS = "transactions"
    CLASSIFICATION = "classification"
    REPLENISHMENT = "replenishment"
    RECEIPTS = "receipts"
    ATTENTION = "attention"


@dataclass(frozen=True, slots=True)
class AgentQueryPlan:
    objective: QueryObjective
    domain: QueryDomain
    tool_name: str
    date_range: ResolvedDateRange | None = None
    comparison_date_range: ResolvedDateRange | None = None
    top_n: int | None = None
    activity_type: Literal["all", "coffee", "restaurants", "delivery", "nightlife"] | None = None
    classification_view: Literal["summary", "staple_candidates", "uncertain"] | None = None
    comparison_mode: Literal["same_weekdays_last_week"] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.objective, QueryObjective) or not isinstance(
            self.domain, QueryDomain
        ):
            raise ValueError("query plan objective and domain must use closed enums")
        expected_tool = {
            QueryDomain.SPENDING: "get_spending_insights",
            QueryDomain.LIFESTYLE: "get_lifestyle_dining_insights",
            QueryDomain.TRANSACTIONS: "search_transactions",
            QueryDomain.CLASSIFICATION: "get_classification_activity",
            QueryDomain.REPLENISHMENT: "get_household_replenishment",
            QueryDomain.RECEIPTS: "get_receipts",
        }.get(self.domain)
        if expected_tool is None or self.tool_name != expected_tool:
            raise ValueError("query plan contains an unsupported tool")
        allowed_domains = {
            QueryObjective.TOTAL_SPEND: {QueryDomain.SPENDING},
            QueryObjective.COMPARE_SPENDING: {QueryDomain.SPENDING},
            QueryObjective.TOP_CATEGORIES: {QueryDomain.SPENDING},
            QueryObjective.TOP_MERCHANTS: {QueryDomain.SPENDING, QueryDomain.LIFESTYLE},
            QueryObjective.TRANSACTION_LIST: {QueryDomain.TRANSACTIONS},
            QueryObjective.AVERAGE_CHECK: {QueryDomain.LIFESTYLE},
            QueryObjective.CHANGE_EXPLANATION: {QueryDomain.SPENDING, QueryDomain.LIFESTYLE},
            QueryObjective.LIFESTYLE_TOTAL: {QueryDomain.LIFESTYLE},
            QueryObjective.LIFESTYLE_FREQUENCY: {QueryDomain.LIFESTYLE},
            QueryObjective.RECENT_LEARNING: {QueryDomain.CLASSIFICATION},
            QueryObjective.LEARNING_SUMMARY: {QueryDomain.CLASSIFICATION},
            QueryObjective.UNCERTAIN_CLASSIFICATIONS: {QueryDomain.CLASSIFICATION},
            QueryObjective.REPLENISHMENT_DUE: {QueryDomain.REPLENISHMENT},
            QueryObjective.RECEIPT_STATUS: {QueryDomain.RECEIPTS},
        }.get(self.objective)
        if allowed_domains is None or self.domain not in allowed_domains:
            raise ValueError("query plan objective and domain do not agree")
        if self.top_n is not None and not 1 <= self.top_n <= MAX_AGENT_TOP_N:
            raise ValueError("top_n is outside the supported range")
        if (
            self.comparison_mode is not None
            and self.objective is not QueryObjective.COMPARE_SPENDING
        ):
            raise ValueError("comparison_mode requires compare_spending")
        if self.comparison_mode is not None and self.comparison_date_range is not None:
            raise ValueError("comparison mode and explicit comparison range are mutually exclusive")
        expected_view = {
            QueryObjective.LEARNING_SUMMARY: "summary",
            QueryObjective.RECENT_LEARNING: "staple_candidates",
            QueryObjective.UNCERTAIN_CLASSIFICATIONS: "uncertain",
        }.get(self.objective)
        if self.classification_view != expected_view:
            raise ValueError("classification view does not match the query objective")

    @property
    def exposed_tools(self) -> frozenset[str]:
        return frozenset({self.tool_name})

    @property
    def requires_explicit_comparison(self) -> bool:
        """Whether execution must preserve a caller-selected second period."""

        return self.comparison_date_range is not None

    def tool_arguments(self) -> dict[str, object]:
        arguments: dict[str, object] = {}
        if self.tool_name == "get_classification_activity" and self.date_range is not None:
            arguments["timezone"] = self.date_range.timezone
            if self.date_range.start_date == self.date_range.end_date:
                arguments["activity_date"] = self.date_range.start_date.isoformat()
            else:
                # Day 17's range-aware classification contract consumes these.
                arguments.update(self.date_range.tool_arguments())
        elif self.tool_name == "search_transactions" and self.date_range is not None:
            if self.activity_type is not None:
                arguments.update(self.date_range.tool_arguments())
            else:
                ranges = [self.date_range]
                if self.comparison_date_range is not None:
                    ranges.append(self.comparison_date_range)
                arguments.update(
                    {
                        "start_date": min(item.start_date for item in ranges).isoformat(),
                        "end_date": max(item.end_date for item in ranges).isoformat(),
                    }
                )
            if self.activity_type is not None and self.comparison_date_range is not None:
                arguments.update(
                    {
                        "comparison_start_date": (
                            self.comparison_date_range.start_date.isoformat()
                        ),
                        "comparison_end_date": self.comparison_date_range.end_date.isoformat(),
                    }
                )
        elif self.date_range is not None and self.tool_name in {
            "get_spending_insights",
            "get_lifestyle_dining_insights",
        }:
            arguments.update(self.date_range.tool_arguments())
            if self.comparison_date_range is not None:
                arguments.update(
                    {
                        "comparison_start_date": (
                            self.comparison_date_range.start_date.isoformat()
                        ),
                        "comparison_end_date": self.comparison_date_range.end_date.isoformat(),
                    }
                )
        if self.activity_type is not None and self.tool_name == "get_lifestyle_dining_insights":
            arguments["activity_type"] = self.activity_type
        if (
            self.tool_name == "get_lifestyle_dining_insights"
            and self.objective is QueryObjective.TOP_MERCHANTS
        ):
            arguments["merchant_limit"] = min(
                self.top_n or DEFAULT_AGENT_TOP_N,
                MAX_LIFESTYLE_TOP_N,
            )
        if self.comparison_mode is not None:
            arguments["comparison_mode"] = self.comparison_mode
        if self.classification_view is not None:
            arguments["view"] = self.classification_view
            arguments["limit"] = self.top_n or DEFAULT_AGENT_TOP_N
        if self.tool_name == "search_transactions":
            arguments.update({"include_pending": False, "limit": self.top_n or 20})
            if self.activity_type is not None:
                arguments["lifestyle_activity_type"] = self.activity_type
        return arguments


_TYPO_TOKENS = {
    "catagory": "category",
    "cofee": "coffee",
    "frm": "from",
    "mnth": "month",
    "reciept": "receipt",
    "restrant": "restaurant",
    "spendings": "spending",
    "spendng": "spending",
}
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_ENTITY_OUTER_PERIOD = (
    r"(?:today|yesterday|(?:this|current) week|(?:last|previous|prior) week|"
    r"(?:last|past|previous) 7 days?|(?:this|current) month|month to date|mtd|"
    r"(?:last|previous|prior) month|(?:last|past|previous) 30 days?|"
    r"(?:this|current) quarter|qtd|(?:last|previous|prior) quarter|"
    r"(?:last|past|previous) 90 days?|year to date|ytd|(?:this|current) year|"
    r"(?:last|previous|prior) year|recently|lately|recent)"
)
_BOUNDED_ENTITY_QUERY = re.compile(
    r"^(?P<prefix>how much did i spend (?:at|with merchant|on product|on item|"
    r"in (?:the )?category)) "
    r"(?P<entity>[a-z0-9'][a-z0-9' ]{0,254}?) "
    rf"(?P<period>{_ENTITY_OUTER_PERIOD})$"
)


def normalize_agent_query(value: str) -> str:
    """Apply only a small, code-reviewed vocabulary for proven product typos."""

    tokens = re.findall(r"[a-z0-9']+", value.casefold().replace("’", "'"))
    return " ".join(_TYPO_TOKENS.get(token, token) for token in tokens)


def _query_text_with_inert_entity(value: str) -> tuple[str, str | None]:
    """Mask one explicitly delimited entity slot while retaining its outer query."""

    match = _BOUNDED_ENTITY_QUERY.fullmatch(value)
    if match is None:
        return value, None
    return (
        f"{match.group('prefix')} selected entity {match.group('period')}",
        match.group("entity"),
    )


def temporal_preset_from_text(value: str) -> TemporalPreset | None:
    text = normalize_agent_query(value)
    patterns: tuple[tuple[TemporalPreset, str], ...] = (
        (TemporalPreset.LAST_30_DAYS, r"\b(?:last|past|previous) 30 days?\b"),
        (TemporalPreset.LAST_90_DAYS, r"\b(?:last|past|previous) 90 days?\b"),
        (TemporalPreset.LAST_7_DAYS, r"\b(?:last|past|previous) 7 days?\b"),
        (TemporalPreset.LAST_QUARTER, r"\b(?:last|previous|prior) quarter\b"),
        (TemporalPreset.THIS_QUARTER, r"\b(?:this|current) quarter\b|\bqtd\b"),
        (TemporalPreset.LAST_MONTH, r"\b(?:last|previous|prior) month\b"),
        (TemporalPreset.THIS_MONTH, r"\b(?:this|current) month\b|\bmonth to date\b|\bmtd\b"),
        (TemporalPreset.LAST_WEEK, r"\b(?:last|previous|prior) week\b"),
        (TemporalPreset.THIS_WEEK, r"\b(?:this|current) week\b|\bweek to date\b|\bwtd\b"),
        (TemporalPreset.LAST_YEAR, r"\b(?:last|previous|prior) year\b"),
        (TemporalPreset.YEAR_TO_DATE, r"\b(?:year to date|ytd)\b"),
        (TemporalPreset.THIS_YEAR, r"\b(?:this|current) year\b"),
        (TemporalPreset.YESTERDAY, r"\byesterday\b"),
        (TemporalPreset.TODAY, r"\btoday\b"),
        (TemporalPreset.RECENTLY, r"\b(?:recently|lately|recent)\b"),
    )
    return next((preset for preset, pattern in patterns if re.search(pattern, text)), None)


_MONTH_NUMBER = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _explicit_date_range_from_text(
    value: str,
    *,
    now: datetime,
    timezone_name: str | None,
) -> tuple[ResolvedDateRange | None, bool]:
    """Resolve two explicit dates and report malformed explicit attempts."""

    timezone, zone = configured_zone(timezone_name)
    iso_tokens = re.findall(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", value)
    if iso_tokens:
        if len(iso_tokens) != 2:
            return None, True
        try:
            start, end = (date.fromisoformat(token) for token in iso_tokens)
        except ValueError:
            return None, True
        return _validated_explicit_range(start, end, timezone=timezone), True

    normalized = normalize_agent_query(value)
    month_names = "|".join(_MONTH_NUMBER)
    match = re.search(
        rf"\b({month_names})\s+(\d{{1,2}})(?:\s+(\d{{4}}))?\s+"
        rf"(?:through|to|until)\s+(?:({month_names})\s+)?"
        r"(\d{1,2})(?:\s+(\d{4}))?\b",
        normalized,
    )
    local_current = (
        (now if now.tzinfo is not None else now.replace(tzinfo=UTC)).astimezone(zone).date()
    )
    if match is not None:
        start_month_name, start_day, start_year, end_month_name, end_day, end_year = match.groups()
        resolved_start_year = int(start_year or end_year or local_current.year)
        resolved_end_year = int(end_year or start_year or local_current.year)
        start_month = _MONTH_NUMBER[start_month_name]
        end_month = _MONTH_NUMBER[end_month_name or start_month_name]
        if not end_year and end_month < start_month:
            resolved_end_year += 1
        try:
            start = date(resolved_start_year, start_month, int(start_day))
            end = date(resolved_end_year, end_month, int(end_day))
        except ValueError:
            return None, True
        return _validated_explicit_range(start, end, timezone=timezone), True

    if re.search(rf"\b(?:{month_names})\s+\d{{1,2}}\b", normalized):
        return None, True
    month_mentions = list(re.finditer(rf"\b({month_names})(?:\s+(\d{{4}}))?\b", normalized))
    if len(month_mentions) != 1:
        return None, False
    month_name, stated_year = month_mentions[0].groups()
    if (
        month_name == "may"
        and not stated_year
        and re.search(
            r"\bmay\s+(?:i|we|you|it|this|that|have|be|need|cause|explain|"
            r"affect|increase|decrease)\b",
            normalized,
        )
    ):
        return None, False
    month = _MONTH_NUMBER[month_name]
    year = int(stated_year) if stated_year else local_current.year
    if not stated_year and month > local_current.month:
        year -= 1
    try:
        start = date(year, month, 1)
        if year == local_current.year and month == local_current.month:
            end = local_current
        else:
            next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            end = next_month - timedelta(days=1)
    except ValueError:
        return None, True
    resolved = _validated_explicit_range(start, end, timezone=timezone)
    if resolved is None:
        return None, True
    return (
        ResolvedDateRange(
            preset=TemporalPreset.EXPLICIT_RANGE,
            start_date=resolved.start_date,
            end_date=resolved.end_date,
            timezone=resolved.timezone,
            label=f"in {month_name.title()} {year}",
        ),
        True,
    )


def _validated_explicit_range(
    start: date,
    end: date,
    *,
    timezone: str,
) -> ResolvedDateRange | None:
    if start > end or (end - start).days > 730:
        return None
    return ResolvedDateRange(
        preset=TemporalPreset.EXPLICIT_RANGE,
        start_date=start,
        end_date=end,
        timezone=timezone,
        label=f"from {start.isoformat()} to {end.isoformat()}",
    )


def _range_from_previous_texts(
    values: tuple[str, ...],
    *,
    now: datetime,
    timezone_name: str | None,
) -> ResolvedDateRange | None:
    for value in reversed(values[-3:]):
        explicit, attempted = _explicit_date_range_from_text(
            value,
            now=now,
            timezone_name=timezone_name,
        )
        if explicit is not None:
            return explicit
        if attempted:
            continue
        preset = temporal_preset_from_text(value)
        if preset is not None:
            return resolve_temporal_range(
                preset,
                now=now,
                timezone_name=timezone_name,
            )
    return None


def _immediately_preceding_range(value: ResolvedDateRange) -> ResolvedDateRange:
    duration = (value.end_date - value.start_date).days
    end = value.start_date - timedelta(days=1)
    start = end - timedelta(days=duration)
    return ResolvedDateRange(
        preset=TemporalPreset.PREVIOUS_PERIOD,
        start_date=start,
        end_date=end,
        timezone=value.timezone,
        label="the previous period",
    )


def plan_agent_query(
    user_text: str,
    *,
    now: datetime,
    timezone_name: str | None,
    page_context: AgentPageContext | None = None,
    previous_plans: tuple[AgentQueryPlan, ...] = (),
    previous_user_texts: tuple[str, ...] = (),
) -> AgentQueryPlan | None:
    """Return a closed, high-confidence read plan or ``None`` for model fallback.

    Explicit wording outranks typed conversational carry, and both outrank page
    defaults. ``previous_user_texts`` remains a bounded compatibility fallback
    while runtimes migrate to persisting ``previous_plans``.
    """

    normalized_text = normalize_agent_query(user_text)
    text, inert_entity = _query_text_with_inert_entity(normalized_text)
    control_text = text if inert_entity is not None else user_text
    if not text or _looks_like_tool_control(control_text, normalized=text):
        return None
    explicit_date_range, explicit_date_attempt = _explicit_date_range_from_text(
        text if inert_entity is not None else user_text,
        now=now,
        timezone_name=timezone_name,
    )
    if explicit_date_attempt and explicit_date_range is None:
        return None
    prior_text = " ".join(normalize_agent_query(item) for item in previous_user_texts[-3:])
    followup = _is_followup(text)
    carry = previous_plans[-1] if followup and previous_plans else None
    prior_date_range = (
        _range_from_previous_texts(
            previous_user_texts,
            now=now,
            timezone_name=timezone_name,
        )
        if followup
        else None
    )
    semantic_text = f"{text} {prior_text}".strip() if followup else text
    page_filters = page_context.filters if page_context is not None else None

    objective = _objective(text, semantic_text, page_context)
    if objective is None and carry is not None:
        objective = carry.objective
    if objective is None and followup and prior_text:
        objective = _objective(prior_text, prior_text, page_context)
    if objective is None:
        return None
    if objective is QueryObjective.TRANSACTION_LIST and not followup:
        # Arbitrary merchant/category selectors must remain provider arguments.
        # A forced planner call replaces provider arguments in the current executor.
        return None
    explicit_activity = _activity_type(text)
    carried_activity = carry.activity_type if carry is not None else None
    activity_hint = explicit_activity or carried_activity
    if activity_hint is None and followup and prior_text:
        activity_hint = _activity_type(prior_text)
    domain, tool_name = _domain_and_tool(
        objective,
        activity_hint=activity_hint,
        page_context=page_context,
        carried_domain=carry.domain if carry is not None else None,
    )
    activity_type = (
        activity_hint
        if domain is QueryDomain.LIFESTYLE or objective is QueryObjective.TRANSACTION_LIST
        else None
    )
    top_n = _result_limit(text, objective)

    explicit_preset = temporal_preset_from_text(text)
    preset = explicit_date_range.preset if explicit_date_range is not None else explicit_preset
    date_range: ResolvedDateRange | None = explicit_date_range
    comparison_date_range: ResolvedDateRange | None = None
    comparison_mode = None
    page_dates_available = bool(
        page_filters
        and page_filters.start_date
        and page_filters.end_date
        and _page_range_applies(domain, page_context)
    )
    direct_comparison_pair = (
        _direct_month_comparison_pair(
            text,
            now=now,
            timezone_name=timezone_name,
        )
        if objective is QueryObjective.COMPARE_SPENDING and explicit_date_range is None
        else None
    )
    if direct_comparison_pair is not None:
        date_range, comparison_date_range = direct_comparison_pair
        preset = date_range.preset
    elif objective is QueryObjective.COMPARE_SPENDING and _is_week_comparison(text):
        # A fair week-to-date comparison remains the authoritative Day 7.5 behavior.
        if explicit_date_range is None:
            preset = TemporalPreset.THIS_WEEK
            date_range = resolve_temporal_range(
                preset,
                now=now,
                timezone_name=timezone_name,
            )
            comparison_mode = "same_weekdays_last_week"
    else:
        if explicit_date_range is not None:
            date_range = explicit_date_range
        elif preset is not None:
            date_range = resolve_temporal_range(
                preset,
                now=now,
                timezone_name=timezone_name,
            )
        elif (
            previous_period_source := prior_date_range
            or (carry.date_range if carry is not None else None)
        ) is not None and re.search(r"\bprevious period\b", text):
            # "Previous period" is an explicit relative selector, not a request
            # to repeat the carried period. Resolve it before generic typed carry
            # so runtime-reconstructed plans and the raw-history fallback agree.
            date_range = _immediately_preceding_range(previous_period_source)
            preset = date_range.preset
        elif carry is not None and carry.date_range is not None:
            preset = carry.date_range.preset
            date_range = carry.date_range
            comparison_date_range = carry.comparison_date_range
        elif prior_date_range is not None:
            date_range = prior_date_range
            preset = date_range.preset
        elif followup and prior_text and (prior_preset := temporal_preset_from_text(prior_text)):
            preset = prior_preset
            date_range = resolve_temporal_range(
                preset,
                now=now,
                timezone_name=timezone_name,
            )
        elif page_dates_available:
            preset = TemporalPreset.PAGE_CONTEXT
            date_range = resolve_temporal_range(
                preset,
                now=now,
                timezone_name=timezone_name,
                page_start_date=page_filters.start_date if page_filters else None,
                page_end_date=page_filters.end_date if page_filters else None,
            )
        else:
            preset = _default_temporal_preset(objective, domain)
            if preset is not None:
                date_range = resolve_temporal_range(
                    preset,
                    now=now,
                    timezone_name=timezone_name,
                )

    if (
        objective is QueryObjective.CHANGE_EXPLANATION
        and followup
        and explicit_date_range is None
        and explicit_preset is None
    ):
        comparison_pair = _comparison_pair(previous_plans, domain=domain, activity=activity_type)
        if comparison_pair is not None:
            date_range, comparison_date_range = comparison_pair

    if (
        objective is QueryObjective.TRANSACTION_LIST
        and carry is not None
        and explicit_date_range is None
        and explicit_preset is None
    ):
        comparison_date_range = carry.comparison_date_range

    classification_view = {
        QueryObjective.LEARNING_SUMMARY: "summary",
        QueryObjective.RECENT_LEARNING: "staple_candidates",
        QueryObjective.UNCERTAIN_CLASSIFICATIONS: "uncertain",
    }.get(objective)
    return AgentQueryPlan(
        objective=objective,
        domain=domain,
        tool_name=tool_name,
        date_range=date_range,
        comparison_date_range=comparison_date_range,
        top_n=top_n,
        activity_type=activity_type,
        classification_view=classification_view,
        comparison_mode=comparison_mode,
    )


def _objective(
    text: str,
    semantic_text: str,
    page_context: AgentPageContext | None,
) -> QueryObjective | None:
    if re.search(
        r"\bwhat (?:did|has|have) (?:expenseops|expense ops|you) learn(?:ed)?\b",
        text,
    ):
        return QueryObjective.LEARNING_SUMMARY
    if re.search(r"\b(?:unsure|uncertain|not sure|low confidence|ambiguous)\b", text):
        return QueryObjective.UNCERTAIN_CLASSIFICATIONS
    if re.search(
        r"\b(?:could become|potential(?:ly)?|newly learned|newly discovered|become)\b"
        r".*\b(?:staples?|household items?)\b",
        text,
    ) or re.search(
        r"\b(?:what did i buy|which purchases?|what was|show|find)\b"
        r".*\b(?:recent|recently)\b"
        r".*\b(?:staples?|replenishable|household items?)\b",
        text,
    ):
        return QueryObjective.RECENT_LEARNING
    if re.search(
        r"\b(?:typical|average|usual)\b.*\b(?:check|bill|purchase|transaction)\b",
        text,
    ) and _activity_type(semantic_text):
        return QueryObjective.AVERAGE_CHECK
    if re.search(
        r"\b(?:why|how come|what drove|what caused|which .{0,64} caused|contributors?)\b",
        text,
    ) and (
        re.search(
            r"\b(?:increase|increased|decrease|decreased|change|changed|difference|higher|lower)\b",
            text,
        )
        or _is_insights_referential_change(text, page_context)
    ):
        return QueryObjective.CHANGE_EXPLANATION
    if re.search(r"\b(?:show|list|which|find|actual)\b.*\btransactions?\b", text):
        return QueryObjective.TRANSACTION_LIST
    if _ranking_request(text, subject="merchant"):
        return QueryObjective.TOP_MERCHANTS
    if _ranking_request(text, subject="categor"):
        return QueryObjective.TOP_CATEGORIES
    if re.search(
        r"\b(?:compare|compared|versus|vs|more|less|higher|lower|up|down|"
        r"increase|increased|decrease|decreased)\b",
        text,
    ) and re.search(r"\b(?:spend|spending|spent|expenses?|purchases?)\b", text):
        return QueryObjective.COMPARE_SPENDING
    if re.search(r"\b(?:often|frequency|frequent|visits?|orders?)\b", text) and _activity_type(
        semantic_text
    ):
        return QueryObjective.LIFESTYLE_FREQUENCY
    if _activity_type(semantic_text) and re.search(
        r"\b(?:how much|money|total|show|spent|spend|spending|went)\b", text
    ):
        return QueryObjective.LIFESTYLE_TOTAL
    if re.search(r"\b(?:how much|what|total|show)\b.*\b(?:spend|spending|spent)\b", text):
        return QueryObjective.TOTAL_SPEND
    if re.search(r"\b(?:due|need to buy|running low)\b", text):
        return QueryObjective.REPLENISHMENT_DUE
    if re.search(r"\b(?:receipt status|receipts? needing review)\b", text):
        return QueryObjective.RECEIPT_STATUS
    return None


def _domain_and_tool(
    objective: QueryObjective,
    *,
    activity_hint: Literal["all", "coffee", "restaurants", "delivery", "nightlife"] | None,
    page_context: AgentPageContext | None,
    carried_domain: QueryDomain | None,
) -> tuple[QueryDomain, str]:
    if objective in {
        QueryObjective.RECENT_LEARNING,
        QueryObjective.LEARNING_SUMMARY,
        QueryObjective.UNCERTAIN_CLASSIFICATIONS,
    }:
        return QueryDomain.CLASSIFICATION, "get_classification_activity"
    if objective is QueryObjective.TRANSACTION_LIST:
        return QueryDomain.TRANSACTIONS, "search_transactions"
    if objective in {
        QueryObjective.AVERAGE_CHECK,
        QueryObjective.LIFESTYLE_TOTAL,
        QueryObjective.LIFESTYLE_FREQUENCY,
    }:
        return QueryDomain.LIFESTYLE, "get_lifestyle_dining_insights"
    if objective in {QueryObjective.TOP_MERCHANTS, QueryObjective.CHANGE_EXPLANATION}:
        if activity_hint is not None or carried_domain is QueryDomain.LIFESTYLE:
            return QueryDomain.LIFESTYLE, "get_lifestyle_dining_insights"
    if objective is QueryObjective.REPLENISHMENT_DUE:
        return QueryDomain.REPLENISHMENT, "get_household_replenishment"
    if objective is QueryObjective.RECEIPT_STATUS:
        return QueryDomain.RECEIPTS, "get_receipts"
    # A referential Insights question intentionally remains scoped to the selected
    # canonical spending category; Food & Dining is broader than restaurant behavior.
    if page_context and page_context.surface is AgentSurface.EXPENSE_INSIGHTS:
        return QueryDomain.SPENDING, "get_spending_insights"
    return QueryDomain.SPENDING, "get_spending_insights"


def _activity_type(
    text: str,
) -> Literal["all", "coffee", "restaurants", "delivery", "nightlife"] | None:
    if re.search(r"\b(?:coffee|cafes?|espresso)\b", text):
        return "coffee"
    if re.search(
        r"\b(?:restaurants?|eat(?:ing)? out|dining out|restaurant checks?|typical checks?)\b",
        text,
    ):
        return "restaurants"
    if re.search(r"\b(?:food delivery|meal delivery|delivery orders?)\b", text):
        return "delivery"
    if re.search(r"\b(?:nightlife|bars?|pubs?)\b", text):
        return "nightlife"
    if re.search(r"\b(?:dining|food and dining|food dining)\b", text):
        return "all"
    return None


def _result_limit(text: str, objective: QueryObjective) -> int | None:
    default: int | None
    if objective in {QueryObjective.TOP_CATEGORIES, QueryObjective.TOP_MERCHANTS}:
        default = DEFAULT_AGENT_TOP_N
    elif objective in {
        QueryObjective.LEARNING_SUMMARY,
        QueryObjective.RECENT_LEARNING,
        QueryObjective.UNCERTAIN_CLASSIFICATIONS,
    }:
        default = DEFAULT_AGENT_TOP_N
    else:
        default = None

    number = r"\d{1,9}|one|two|three|four|five|six|seven|eight|nine|ten"
    patterns = (
        rf"\b(?:top|first)\s+({number})\b",
        rf"\b({number})\s+(?:top|largest|biggest|highest)\b",
        rf"\b(?:show|list|give|find)\s+(?:me\s+)?(?:the\s+)?({number})\b",
    )
    if objective in {QueryObjective.TOP_CATEGORIES, QueryObjective.TOP_MERCHANTS}:
        patterns += (rf"\b({number})\s+(?:merchants?|categor(?:y|ies))\b",)
    match = next((value for pattern in patterns if (value := re.search(pattern, text))), None)
    if match is not None:
        token = match.group(1)
        value = int(token) if token.isdigit() else _NUMBER_WORDS[token]
        return max(1, min(value, MAX_AGENT_TOP_N))
    if (
        objective in {QueryObjective.TOP_CATEGORIES, QueryObjective.TOP_MERCHANTS}
        and re.search(r"\b(?:category|merchant)\b", text)
        and not re.search(r"\b(?:categories|merchants)\b", text)
    ):
        return 1
    return default


def _is_followup(text: str) -> bool:
    return bool(
        re.match(r"^(?:and )?(?:what|how) about\b", text)
        or re.match(r"^same (?:thing )?for\b", text)
        or re.search(
            r"\b(?:caused|drove|explains?) (?:the |that )?(?:difference|change)\b|"
            r"\b(?:actual|underlying|those) transactions\b",
            text,
        )
    )


def _looks_like_tool_control(raw_text: str, *, normalized: str | None = None) -> bool:
    raw = raw_text.casefold()
    if re.search(
        r"\b(?:get_spending_insights|get_lifestyle_dining_insights|"
        r"get_classification_activity|search_transactions|"
        r"get_household_replenishment|get_receipts)\b",
        raw,
    ):
        return True
    text = normalized if normalized is not None else normalize_agent_query(raw_text)
    if re.search(r"\b(?:use|call|invoke|select|expose)\b.{0,40}\btools?\b", text):
        return True
    if re.search(
        r"\b(?:merchant|category|spending|lifestyle|classification|transaction)\s+tools?\b",
        text,
    ):
        return True
    return bool(
        re.match(r"^(?:system|developer|assistant)\b", text)
        and re.search(r"\b(?:tools?|prompt|instructions?|policy)\b", text)
    )


def _ranking_request(text: str, *, subject: Literal["merchant", "categor"]) -> bool:
    noun = r"merchants?" if subject == "merchant" else r"categor(?:y|ies)"
    rank = r"(?:top|largest|biggest|highest|most)"
    request = r"(?:what|which|who|show|list|rank|give|find|tell)"
    return bool(
        re.search(rf"\b{request}\b.{{0,96}}\b{rank}\b.{{0,64}}\b{noun}\b", text)
        or re.search(rf"\b{request}\b.{{0,96}}\b{noun}\b.{{0,96}}\b{rank}\b", text)
        or re.search(
            rf"\b{request}\b.{{0,64}}\b\d{{1,9}}\s+{noun}\b.{{0,64}}"
            r"\b(?:by|for|in)\b.{0,32}\b(?:spend|spending|spent)\b",
            text,
        )
        or re.match(rf"^(?:the\s+)?{rank}\b.{{0,64}}\b{noun}\b", text)
        or re.match(rf"^rank\b.{{0,64}}\b{noun}\b", text)
    )


def _is_week_comparison(text: str) -> bool:
    return bool(
        re.search(r"\b(?:this|last|previous|prior) week\b", text)
        or re.search(r"\bweek (?:than|then|versus|vs) last\b", text)
        or re.search(r"\bweek over week\b", text)
    )


def _direct_month_comparison_pair(
    text: str,
    *,
    now: datetime,
    timezone_name: str | None,
) -> tuple[ResolvedDateRange, ResolvedDateRange] | None:
    if not (
        re.search(r"\b(?:this|current) month\b", text)
        and re.search(r"\b(?:last|previous|prior) month\b", text)
    ):
        return None
    return (
        resolve_temporal_range(
            TemporalPreset.THIS_MONTH,
            now=now,
            timezone_name=timezone_name,
        ),
        resolve_temporal_range(
            TemporalPreset.LAST_MONTH,
            now=now,
            timezone_name=timezone_name,
        ),
    )


def _default_temporal_preset(
    objective: QueryObjective,
    domain: QueryDomain,
) -> TemporalPreset | None:
    if objective is QueryObjective.LEARNING_SUMMARY:
        return TemporalPreset.TODAY
    if objective in {
        QueryObjective.RECENT_LEARNING,
        QueryObjective.UNCERTAIN_CLASSIFICATIONS,
    }:
        return TemporalPreset.RECENTLY
    if domain in {QueryDomain.SPENDING, QueryDomain.LIFESTYLE}:
        return TemporalPreset.RECENTLY
    return None


def _page_range_applies(
    domain: QueryDomain,
    page_context: AgentPageContext | None,
) -> bool:
    return bool(
        page_context
        and page_context.surface
        in {
            AgentSurface.EXPENSE_REVIEW,
            AgentSurface.EXPENSE_INSIGHTS,
            AgentSurface.EXPENSE_ACTIVITY,
        }
        and domain in {QueryDomain.SPENDING, QueryDomain.LIFESTYLE, QueryDomain.TRANSACTIONS}
    )


def _comparison_pair(
    previous_plans: tuple[AgentQueryPlan, ...],
    *,
    domain: QueryDomain,
    activity: Literal["all", "coffee", "restaurants", "delivery", "nightlife"] | None,
) -> tuple[ResolvedDateRange, ResolvedDateRange] | None:
    compatible: list[AgentQueryPlan] = []
    for plan in previous_plans:
        if plan.date_range is None or plan.domain is not domain:
            continue
        if activity is not None and plan.activity_type not in {None, activity}:
            continue
        compatible.append(plan)
    if compatible and compatible[-1].comparison_date_range is not None:
        return compatible[-1].date_range, compatible[-1].comparison_date_range
    if len(compatible) < 2:
        return None
    current = compatible[-2].date_range
    comparison = compatible[-1].date_range
    if (current.start_date, current.end_date) == (
        comparison.start_date,
        comparison.end_date,
    ):
        return None
    return current, comparison


def _is_insights_referential_change(
    text: str,
    page_context: AgentPageContext | None,
) -> bool:
    return bool(
        page_context
        and page_context.surface is AgentSurface.EXPENSE_INSIGHTS
        and re.fullmatch(r"(?:why|how come) did this (?:increase|decrease|change)", text)
    )
