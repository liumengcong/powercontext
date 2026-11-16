"""Deterministic, rule-based StudyFlow logic.

Nothing here calls a model: PowerContext 1.5.1's builtin runtime is used for persistence and
retrieval only, and every derived suggestion states the rule that produced it. ``ENGINE`` is echoed
to the UI so a reader can never mistake the output for generative AI.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.store import MemoryRecord

TASK_STATUS_SCHEMA = "studyflow.task-status.v1"
TASK_STATUS_KIND = "task_status"
ENGINE = "rule-based-v1"

DEFAULT_AVAILABLE_MINUTES = 25
DEFAULT_MAX_TASKS = 2
ABSOLUTE_MAX_TASKS = 4

KIND_ORDER = ("preference", "note", "weakness", "progress", "task", TASK_STATUS_KIND)
KIND_LABELS = {
    "preference": "学习偏好",
    "note": "笔记",
    "weakness": "薄弱点",
    "progress": "进度",
    "task": "待办",
    TASK_STATUS_KIND: "状态事件",
    "other": "其它",
}

_CJK_RUN = re.compile(r"[㐀-䶿一-鿿豈-﫿]{2,}")


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def cjk_bigrams(text: str) -> set[str]:
    """Cheap, explainable Chinese overlap signal: adjacent character pairs inside CJK runs."""
    grams: set[str] = set()
    for run in _CJK_RUN.findall(text or ""):
        grams.update(run[i : i + 2] for i in range(len(run) - 1))
    return grams


def overlap(first: str, second: str) -> int:
    return len(cjk_bigrams(first) & cjk_bigrams(second))


# --------------------------------------------------------------------- task status events


def encode_status_event(task_artifact_id: str, status: str, seq: int, summary: str) -> dict[str, str]:
    """JSON-wrapped text payload with an explicit schema version (see README "状态模型").

    The Chinese sentence is duplicated into ``summary`` so the FTS projection still sees CJK
    tokens instead of only ASCII field names.
    """
    payload = {
        "schema": TASK_STATUS_SCHEMA,
        "task_artifact_id": task_artifact_id,
        "status": status,
        "seq": seq,
        "recorded_at": _now_iso(),
        "summary": summary,
        "note": "应用层追加事件，不修改任务正文 revision",
    }
    return {
        "kind": TASK_STATUS_KIND,
        "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "description": summary[:200],
    }


def decode_status_event(record: MemoryRecord) -> dict[str, Any] | None:
    if record.kind != TASK_STATUS_KIND:
        return None
    try:
        payload = json.loads(record.text)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("schema") != TASK_STATUS_SCHEMA:
        return None
    if payload.get("status") not in {"todo", "done"}:
        return None
    try:
        payload["seq"] = int(payload.get("seq", 0))
    except (TypeError, ValueError):
        return None
    return payload


@dataclass
class TaskState:
    """A task memory plus the newest status event found for it in PowerContext."""

    memory: MemoryRecord
    status: str = "todo"
    event: MemoryRecord | None = None
    payload: dict[str, Any] | None = None

    @property
    def is_open(self) -> bool:
        return self.status == "todo"

    def to_api(self) -> dict[str, Any]:
        return {
            "memory": self.memory.to_api(),
            "status": self.status,
            "status_event": None if self.event is None else self.event.to_api(),
            "event_payload": self.payload,
            "title": _task_title(self.memory),
        }


def _task_title(memory: MemoryRecord) -> str:
    text = memory.text.strip().replace("\n", " ")
    return text[:80] + "…" if len(text) > 80 else text


def latest_events(status_records: list[MemoryRecord]) -> dict[str, MemoryRecord]:
    """Newest event per task: highest ``seq`` wins, then ``recorded_at``, then artifact id."""
    best: dict[str, tuple[tuple[int, str, str], MemoryRecord]] = {}
    for record in status_records:
        payload = decode_status_event(record)
        if payload is None:
            continue
        task_id = str(payload.get("task_artifact_id") or "")
        if not task_id:
            continue
        key = (
            int(payload.get("seq") or 0),
            str(payload.get("recorded_at") or ""),
            record.artifact_id,
        )
        current = best.get(task_id)
        if current is None or key > current[0]:
            best[task_id] = (key, record)
    return {task_id: record for task_id, (_, record) in best.items()}


def build_task_states(tasks: list[MemoryRecord], status_records: list[MemoryRecord]) -> list[TaskState]:
    events = latest_events(status_records)
    states: list[TaskState] = []
    for task in tasks:
        event = events.get(task.artifact_id)
        payload = decode_status_event(event) if event is not None else None
        states.append(
            TaskState(
                memory=task,
                status=str((payload or {}).get("status", "todo")),
                event=event,
                payload=payload,
            )
        )
    states.sort(key=lambda state: state.memory.artifact_id)
    return states


# --------------------------------------------------------------------- preferences


@dataclass
class Preferences:
    available_minutes: int = DEFAULT_AVAILABLE_MINUTES
    max_tasks: int = DEFAULT_MAX_TASKS
    prefer_weakness: bool = True
    source: MemoryRecord | None = None
    rules: list[str] = field(default_factory=list)

    def to_api(self) -> dict[str, Any]:
        return {
            "available_minutes": self.available_minutes,
            "max_tasks": self.max_tasks,
            "prefer_weakness": self.prefer_weakness,
            "source": None if self.source is None else self.source.citation(),
            "rules": list(self.rules),
            "engine": ENGINE,
        }


MINUTES_RE = re.compile(r"(\d{1,3})\s*分钟")
MAX_TASKS_RE = re.compile(r"最多安排\s*(\d{1,2})\s*个任务")


def task_budget(minutes: int) -> int:
    """Documented rule: ≤15 min → 1 task, ≤45 min → 2, ≤90 min → 3, above → 4 (hard cap 4)."""
    if minutes <= 15:
        return 1
    if minutes <= 45:
        return 2
    if minutes <= 90:
        return 3
    return ABSOLUTE_MAX_TASKS


def parse_preferences(preference_records: list[MemoryRecord]) -> Preferences:
    prefs = Preferences(rules=["没有可用的偏好记忆时使用默认值：单次 25 分钟、最多 2 个任务"])
    if not preference_records:
        return prefs
    source = preference_records[-1]
    text = source.text
    prefs.source = source
    prefs.rules = []
    minutes = MINUTES_RE.search(text)
    if minutes:
        prefs.available_minutes = max(1, min(480, int(minutes.group(1))))
        prefs.rules.append(f"从偏好原文读取“{minutes.group(1)} 分钟”，规则：单次可用时长 = {prefs.available_minutes} 分钟")
    else:
        prefs.rules.append("偏好原文没有出现时长，沿用默认 25 分钟")
    explicit = MAX_TASKS_RE.search(text)
    if explicit:
        prefs.max_tasks = max(1, min(ABSOLUTE_MAX_TASKS, int(explicit.group(1))))
        prefs.rules.append(f"偏好原文写明“最多安排 {explicit.group(1)} 个任务”，直接采用该上限")
    else:
        prefs.max_tasks = task_budget(prefs.available_minutes)
        prefs.rules.append(
            f"偏好原文未写任务上限，按时长分档规则（≤15→1，≤45→2，≤90→3，>90→4）得到 {prefs.max_tasks} 项"
        )
    if "薄弱" in text:
        prefs.prefer_weakness = True
        prefs.rules.append("偏好原文提到“薄弱”，薄弱点相关的待办排在前面")
    else:
        prefs.prefer_weakness = False
        prefs.rules.append("偏好原文未提到薄弱点，待办按记录顺序排列")
    return prefs


# --------------------------------------------------------------------- continuation


def build_continuation(
    *,
    workspace: str,
    scope_id: str,
    prefs: Preferences,
    tasks: list[TaskState],
    weaknesses: list[MemoryRecord],
    notes: list[MemoryRecord],
    progress: list[MemoryRecord],
) -> dict[str, Any]:
    """Assemble 已完成 / 当前阻塞 / 下一步 / 参考记忆 strictly from what PowerContext returned."""
    open_tasks = [state for state in tasks if state.is_open]
    done_tasks = [state for state in tasks if not state.is_open]

    weakness_grams: set[str] = set()
    for weakness in weaknesses:
        weakness_grams |= cjk_bigrams(weakness.text)

    def relevance(state: TaskState) -> int:
        return len(cjk_bigrams(state.memory.text) & weakness_grams)

    ranked = sorted(open_tasks, key=lambda state: (-(relevance(state) if prefs.prefer_weakness else 0), state.memory.artifact_id))
    selected = ranked[: prefs.max_tasks]

    def step_reason(state: TaskState) -> str:
        score = relevance(state)
        if prefs.prefer_weakness and score:
            return f"与薄弱点共享 {score} 个中文二元组，且仍为待办"
        if prefs.prefer_weakness:
            return "仍为待办；与薄弱点无字面重叠，按记录顺序补齐"
        return "仍为待办，按记录顺序排列"

    blocked = [
        {
            "text": weakness.text,
            "citation": weakness.citation(),
            "rule": "kind=weakness 且状态为 active 的记忆全部列为待解决薄弱点",
        }
        for weakness in weaknesses
    ]

    completed = [
        {
            "text": _task_title(state.memory),
            "task_artifact_id": state.memory.artifact_id,
            "status": "done",
            "citation": state.memory.citation(),
            "evidence": None if state.event is None else state.event.citation(),
            "rule": "该待办的最新 task_status 事件为 done",
        }
        for state in done_tasks
    ]
    recorded_progress = [
        {
            "text": item.text,
            "citation": item.citation(),
            "rule": "kind=progress 的原文照抄，不做改写或补全",
        }
        for item in progress
    ]

    references = [item.citation() for item in [prefs.source] if item is not None]
    references += [state.memory.citation() for state in selected]
    references += [state.event.citation() for state in done_tasks if state.event is not None]
    references += [item.citation() for item in weaknesses]
    references += [item.citation() for item in progress]

    empty = not (completed or blocked or selected or recorded_progress)
    return {
        "workspace": workspace,
        "scope_id": scope_id,
        "generated_at": _now_iso(),
        "engine": ENGINE,
        "source_of_truth": "powercontext",
        "preference": prefs.to_api(),
        "summary": {
            "open_tasks": len(open_tasks),
            "done_tasks": len(done_tasks),
            "weaknesses": len(weaknesses),
            "notes": len(notes),
            "suggested": len(selected),
        },
        "completed": completed,
        "recorded_progress": recorded_progress,
        "blocked": blocked,
        "next_steps": [
            {
                "text": _task_title(state.memory),
                "task_artifact_id": state.memory.artifact_id,
                "reason": step_reason(state),
                "citation": state.memory.citation(),
                "status_event": None if state.event is None else state.event.citation(),
            }
            for state in selected
        ],
        "references": _dedupe(references, key=lambda item: (item["artifact_id"], item["revision"])),
        "empty": empty,
        "explanation": [
            f"下一步最多 {prefs.max_tasks} 项来自偏好与分档规则，不是模型推断。",
            "每项建议都指向一条真实 PowerContext atomic-memory artifact（含 artifact_id 与 revision）。",
            "没有记录的内容（用时、论文数量、提效比例）不会出现在这里。",
        ],
    }


def _dedupe(items: list[dict[str, Any]], *, key) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        marker = key(item)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(item)
    return out


# --------------------------------------------------------------------- review export


def build_review_markdown(payload: dict[str, Any]) -> str:
    """Markdown draft built only from the continuation payload PowerContext produced."""
    prefs = payload["preference"]
    lines: list[str] = []
    lines.append("# 学习复盘初稿")
    lines.append("")
    lines.append(f"- 工作区：{payload['workspace']}")
    lines.append(f"- PowerContext scope：`{payload['scope_id']}`")
    lines.append(f"- 生成时间：{payload['generated_at']}")
    lines.append(f"- 生成方式：{payload['engine']}（规则整理，未调用任何生成式模型）")
    lines.append("")
    lines.append("> 本初稿只汇总 PowerContext 中 state=active 的记忆与待办状态事件；已遗忘的记忆不参与推导。")
    lines.append("> 需要历史（含已遗忘）记录请另行导出验证记录，本文件不混入两种口径。")
    lines.append("> 请本人核对每条引用是否与自己的实际经历一致后再转发或存档；没有记录的内容不会被补写。")
    lines.append("")
    lines.append("## 本次可用时长与任务上限")
    lines.append("")
    lines.append(f"- 单次可用时长：{prefs['available_minutes']} 分钟")
    lines.append(f"- 建议任务上限：{prefs['max_tasks']} 项")
    lines.append(f"- 是否优先薄弱点：{'是' if prefs['prefer_weakness'] else '否'}")
    for rule in prefs["rules"]:
        lines.append(f"- 规则：{rule}")
    if prefs.get("source"):
        lines.append(f"- 偏好出处：`{prefs['source']['artifact_id']}` revision {prefs['source']['revision']}")
    lines.append("")

    lines.append("## 已完成")
    lines.append("")
    if not payload["completed"] and not payload["recorded_progress"]:
        lines.append("- 本次没有读到任何“已完成”记录。")
    for item in payload["completed"]:
        citation = item["citation"]
        evidence = item["evidence"]
        suffix = (
            f"（任务记忆 `{citation['artifact_id']}` rev {citation['revision']}；"
            f"状态事件 `{evidence['artifact_id']}` rev {evidence['revision']}）"
            if evidence
            else f"（任务记忆 `{citation['artifact_id']}` rev {citation['revision']}）"
        )
        lines.append(f"- {item['text']}{suffix}")
    for item in payload["recorded_progress"]:
        citation = item["citation"]
        lines.append(f"- 进度记录原文：“{citation['quote']}”（`{citation['artifact_id']}` rev {citation['revision']}）")
    lines.append("")

    lines.append("## 仍未解决")
    lines.append("")
    if not payload["blocked"]:
        lines.append("- 没有读到状态为 active 的薄弱点记忆。")
    for item in payload["blocked"]:
        citation = item["citation"]
        lines.append(f"- {item['text']}（`{citation['artifact_id']}` rev {citation['revision']}）")
    lines.append("")

    lines.append("## 下一步")
    lines.append("")
    if not payload["next_steps"]:
        lines.append("- 当前没有待办记忆，因此不给建议。")
    for index, item in enumerate(payload["next_steps"], start=1):
        citation = item["citation"]
        lines.append(f"{index}. {item['text']}")
        lines.append(f"   - 依据：{item['reason']}")
        lines.append(f"   - 出处：`{citation['artifact_id']}` rev {citation['revision']}，“{citation['quote']}”")
    lines.append("")

    lines.append("## 记忆引用清单")
    lines.append("")
    if not payload["references"]:
        lines.append("- 本次没有引用。")
    for citation in payload["references"]:
        lines.append(
            f"- `{citation['artifact_id']}` rev {citation['revision']}（{KIND_LABELS.get(citation['kind'], citation['kind'])}）"
            f"：“{citation['quote']}”"
        )
    lines.append("")

    lines.append("## 需要本人核对")
    lines.append("")
    lines.append("- [ ] 完成状态是否与实际一致")
    lines.append("- [ ] 薄弱点描述是否仍然成立")
    lines.append("- [ ] 下一步是否值得继续投入")
    lines.append("- 本工具未记录、也无法推断：真实使用时长、论文/作业数量、提效百分比。")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------- sample import


@dataclass
class ImportPlan:
    """Sample-data import dedupe map. Non-authoritative UI bookkeeping, allowed by the brief."""

    path: Path
    mapping: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "ImportPlan":
        plan = cls(path=path)
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = {}
            scopes = payload.get("scopes") if isinstance(payload, dict) else None
            if isinstance(scopes, dict):
                plan.mapping = {
                    str(scope): {str(k): str(v) for k, v in entries.items()}
                    for scope, entries in scopes.items()
                    if isinstance(entries, dict)
                }
        return plan

    def mapped(self, scope_id: str) -> dict[str, str]:
        return dict(self.mapping.get(scope_id, {}))

    def record(self, scope_id: str, entries: dict[str, str]) -> None:
        merged = self.mapping.get(scope_id, {})
        merged.update(entries)
        self.mapping[scope_id] = merged

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"schema": "studyflow.import-map.v1", "scopes": self.mapping},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def forget_scope(self, scope_id: str) -> None:
        self.mapping.pop(scope_id, None)


def sample_entries(memories: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Translate sample-data.json memories into atomic-memory inputs.

    ``kind`` and ``text`` carry the real content; the sample's own ``key``/``source`` labels are
    preserved inside ``description`` so a reader can trace the demo record back to the file.
    """
    out: list[dict[str, str]] = []
    for item in memories:
        kind = str(item.get("kind") or "note")
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        description = f"{item.get('source') or 'sample'} · key={item.get('key') or '-'}"
        out.append({"kind": kind, "text": text, "description": description, "key": str(item.get("key") or "")})
    return out


__all__ = [
    "ABSOLUTE_MAX_TASKS",
    "ENGINE",
    "ImportPlan",
    "KIND_LABELS",
    "KIND_ORDER",
    "Preferences",
    "TASK_STATUS_KIND",
    "TASK_STATUS_SCHEMA",
    "TaskState",
    "build_continuation",
    "build_review_markdown",
    "build_task_states",
    "cjk_bigrams",
    "decode_status_event",
    "encode_status_event",
    "latest_events",
    "overlap",
    "parse_preferences",
    "sample_entries",
    "task_budget",
]
