"""Writing a reminder into the user's own Google Tasks list.

The list the Tasks side panel shows is the default one, so that is where a
task goes unless another is named.

A due date in Google Tasks is a **date**, not a moment: the API takes RFC 3339
but ignores the time of day, and sending a local midnight can land the task on
the previous day for anyone east of UTC.  Midday UTC is used instead, which is
the same calendar day everywhere this is likely to run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from googleapiclient.discovery import build
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.logging import get_logger

log = get_logger(__name__)

TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"

tasks_retry = retry(
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)


@dataclass(slots=True)
class Task:
    id: str
    title: str
    due: str | None = None
    list_id: str | None = None


def due_stamp(day: date) -> str:
    """RFC 3339 for a due date that stays on the day it was meant for."""
    return datetime.combine(day, time(12, 0), tzinfo=UTC).isoformat().replace("+00:00", "Z")


class TasksClient:
    def __init__(self, credentials):
        self._service = build("tasks", "v1", credentials=credentials, cache_discovery=False)

    @tasks_retry
    def lists(self) -> list[tuple[str, str]]:
        """``(id, title)`` for each task list, the default one first."""
        response = self._service.tasklists().list(maxResults=100).execute()
        return [(item["id"], item.get("title", "")) for item in response.get("items", [])]

    def resolve_list(self, name: str = "") -> str:
        """The id of the named list, or of the default one.

        "@default" is accepted by the API directly, but resolving it means the
        caller can be told which list a task actually went into.
        """
        available = self.lists()
        if not available:
            return "@default"
        if name:
            for list_id, title in available:
                if title.casefold() == name.casefold():
                    return list_id
            raise ValueError(
                f"No task list named {name!r}. Available: "
                + ", ".join(title for _id, title in available)
            )
        return available[0][0]

    @tasks_retry
    def create(
        self,
        title: str,
        notes: str = "",
        due: date | None = None,
        list_id: str = "",
    ) -> Task:
        body: dict = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            body["due"] = due_stamp(due)

        target = list_id or "@default"
        created = self._service.tasks().insert(tasklist=target, body=body).execute()
        log.info("tasks.created", title=title, due=due.isoformat() if due else None)
        return Task(
            id=created["id"],
            title=created.get("title", title),
            due=created.get("due"),
            list_id=target,
        )
