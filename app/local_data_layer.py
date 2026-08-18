import json
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from chainlit.data.base import BaseDataLayer
from chainlit.types import (
    Feedback,
    PageInfo,
    PaginatedResponse,
    Pagination,
    ThreadDict,
    ThreadFilter,
)
from chainlit.user import PersistedUser, User

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".chat_history")


class LocalDataLayer(BaseDataLayer):
    """A simple file-based data layer that stores chat history as JSON files."""

    def __init__(self):
        os.makedirs(STORAGE_DIR, exist_ok=True)
        self.users_file = os.path.join(STORAGE_DIR, "users.json")
        self.threads_dir = os.path.join(STORAGE_DIR, "threads")
        os.makedirs(self.threads_dir, exist_ok=True)

    # ── Helpers ──

    def _thread_path(self, thread_id: str) -> str:
        return os.path.join(self.threads_dir, f"{thread_id}.json")

    def _load_thread(self, thread_id: str) -> Optional[dict]:
        path = self._thread_path(thread_id)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def _save_thread(self, thread_id: str, data: dict):
        path = self._thread_path(thread_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def _load_users(self) -> dict:
        if os.path.exists(self.users_file):
            with open(self.users_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_users(self, users: dict):
        with open(self.users_file, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2, default=str)

    # ── Users ──

    async def get_user(self, identifier: str) -> Optional[PersistedUser]:
        users = self._load_users()
        if identifier in users:
            u = users[identifier]
            return PersistedUser(
                id=u["id"],
                createdAt=u["createdAt"],
                identifier=identifier,
            )
        return None

    async def create_user(self, user: User) -> Optional[PersistedUser]:
        users = self._load_users()
        now = datetime.now(timezone.utc).isoformat()
        user_id = str(uuid.uuid4())
        users[user.identifier] = {"id": user_id, "createdAt": now}
        self._save_users(users)
        return PersistedUser(
            id=user_id,
            createdAt=now,
            identifier=user.identifier,
        )

    # ── Threads ──

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse[ThreadDict]:
        all_threads = []
        for filename in os.listdir(self.threads_dir):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(self.threads_dir, filename)
            with open(path, "r", encoding="utf-8") as f:
                thread = json.load(f)
                # Apply user filter
                if filters.userId and thread.get("userId") != filters.userId:
                    continue
                # Apply search filter
                if filters.search:
                    name = (thread.get("name") or "").lower()
                    if filters.search.lower() not in name:
                        continue
                all_threads.append(thread)

        # Sort by creation date, newest first
        all_threads.sort(key=lambda t: t.get("createdAt", ""), reverse=True)

        # Pagination
        start = 0
        if pagination.cursor:
            for i, t in enumerate(all_threads):
                if t["id"] == pagination.cursor:
                    start = i + 1
                    break

        page = all_threads[start: start + pagination.first]
        has_next = (start + pagination.first) < len(all_threads)

        return PaginatedResponse(
            pageInfo=PageInfo(
                hasNextPage=has_next,
                startCursor=page[0]["id"] if page else None,
                endCursor=page[-1]["id"] if page else None,
            ),
            data=page,
        )

    async def get_thread(self, thread_id: str) -> Optional[ThreadDict]:
        return self._load_thread(thread_id)

    async def get_thread_author(self, thread_id: str) -> str:
        thread = self._load_thread(thread_id)
        if thread:
            return thread.get("userIdentifier", "")
        return ""

    async def update_thread(
        self,
        thread_id: str,
        name: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
    ):
        thread = self._load_thread(thread_id)
        if not thread:
            thread = {
                "id": thread_id,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "name": name,
                "userId": user_id,
                "userIdentifier": user_id,
                "tags": tags,
                "metadata": metadata or {},
                "steps": [],
                "elements": [],
            }
        else:
            if name is not None:
                thread["name"] = name
            if user_id is not None:
                thread["userId"] = user_id
                thread["userIdentifier"] = user_id
            if metadata is not None:
                thread["metadata"] = metadata
            if tags is not None:
                thread["tags"] = tags

        self._save_thread(thread_id, thread)

    async def delete_thread(self, thread_id: str):
        path = self._thread_path(thread_id)
        if os.path.exists(path):
            os.remove(path)

    # ── Steps (messages) ──

    async def create_step(self, step_dict: dict):
        thread_id = step_dict.get("threadId")
        if not thread_id:
            return
        thread = self._load_thread(thread_id)
        if not thread:
            thread = {
                "id": thread_id,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "name": None,
                "userId": None,
                "userIdentifier": None,
                "tags": None,
                "metadata": {},
                "steps": [],
                "elements": [],
            }
        thread["steps"].append(step_dict)
        self._save_thread(thread_id, thread)

    async def update_step(self, step_dict: dict):
        thread_id = step_dict.get("threadId")
        if not thread_id:
            return
        thread = self._load_thread(thread_id)
        if not thread:
            return
        for i, s in enumerate(thread.get("steps", [])):
            if s.get("id") == step_dict.get("id"):
                thread["steps"][i] = step_dict
                break
        self._save_thread(thread_id, thread)

    async def delete_step(self, step_id: str):
        for filename in os.listdir(self.threads_dir):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(self.threads_dir, filename)
            with open(path, "r", encoding="utf-8") as f:
                thread = json.load(f)
            thread["steps"] = [s for s in thread.get("steps", []) if s.get("id") != step_id]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(thread, f, indent=2, default=str)

    # ── Elements (files/images) ──

    async def create_element(self, element):
        pass

    async def get_element(self, thread_id: str, element_id: str):
        return None

    async def delete_element(self, element_id: str, thread_id=None):
        pass

    # ── Feedback ──

    async def upsert_feedback(self, feedback: Feedback) -> str:
        return feedback.id or str(uuid.uuid4())

    async def delete_feedback(self, feedback_id: str) -> bool:
        return True

    # ── Misc ──

    async def build_debug_url(self) -> str:
        return ""

    async def close(self) -> None:
        pass

    async def get_favorite_steps(self, user_id: str) -> list:
        return []
