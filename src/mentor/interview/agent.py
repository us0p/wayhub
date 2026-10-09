"""The interview agent as a LangGraph graph (D54, D56).

    START → plan ─┬─→ speak ───┬─→ apply → END
                  └─→ extract ─┘

- plan: structured `TurnPlan` (private reasoning, focus, brief, done) from the profile,
  checklist and recent conversation. If it fails, a generic brief keeps the conversation going.
- speak: streams the user-facing message from the brief and recent turns only, so internal
  notes can't leak into it. Its tokens are what the UI streams.
- extract: structured `ProfilePatch` from the new message (runs alongside speak).
- apply: writes the patch and checklist, then decides completion (plan.done + all items done).

The graph state (the recent conversation, last plan and patch) is checkpointed per interview
thread; the engine resets its messages to our transcript's recent window on every turn, so
the two can't drift (D58). Per-turn resources (DB session, AI services, profile snapshot) are
run-scoped context.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, NotRequired, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.bundle import AI
from mentor.ai.errors import describe
from mentor.ai.ports import AIOutputError, Effort
from mentor.auth.models import User
from mentor.interview import prompts, store
from mentor.interview.checklist import Progress
from mentor.interview.models import ChecklistStatus
from mentor.interview.schemas import ProfilePatch, TurnPlan
from mentor.profile import service as profile_service

log = logging.getLogger(__name__)

SPEAK_TURNS = 6  # the speaker only needs enough context to sound natural

FALLBACK_PLAN = TurnPlan(
    reasoning="O planejador falhou; seguir com uma pergunta genérica.",
    brief="Reconheça brevemente a última resposta e faça uma pergunta para continuar "
    "conhecendo a trajetória profissional da pessoa.",
)


class InterviewState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    plan: NotRequired[dict[str, Any] | None]
    patch: NotRequired[dict[str, Any] | None]
    completed: NotRequired[bool]


@dataclass
class TurnContext:
    """Run-scoped resources for one user turn (not checkpointed)."""

    db: AsyncSession
    ai: AI
    user: User
    snapshot: profile_service.Snapshot
    progress: Progress
    turn_id: uuid.UUID


def _last(messages: list[BaseMessage], kind: str) -> BaseMessage | None:
    return next((m for m in reversed(messages) if m.type == kind), None)


async def plan(state: InterviewState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    ctx = runtime.context
    messages = [
        SystemMessage(prompts.PLAN_SYSTEM.format(goals=prompts.goals_text())),
        HumanMessage(
            prompts.PLAN_USER.format(
                profile=profile_service.render(ctx.snapshot),
                checklist=prompts.checklist_text(ctx.progress),
                transcript=prompts.transcript(state["messages"]),
            )
        ),
    ]
    try:
        result = await ctx.ai.chat.structured(TurnPlan, effort=Effort.LOW).ainvoke(messages)
    except Exception as exc:
        log.warning("interview plan failed: %s (prompt %s)", describe(exc), prompts.VERSION)
        result = FALLBACK_PLAN
    return {"plan": result.model_dump(mode="json")}


async def speak(state: InterviewState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    brief = TurnPlan.model_validate(state.get("plan") or FALLBACK_PLAN.model_dump()).brief
    messages = [
        SystemMessage(prompts.SPEAK_SYSTEM),
        HumanMessage(
            prompts.SPEAK_USER.format(
                transcript=prompts.transcript(state["messages"][-SPEAK_TURNS:]), brief=brief
            )
        ),
    ]
    parts = [
        chunk.text
        async for chunk in runtime.context.ai.chat.chat(effort=Effort.LOW).astream(messages)
    ]
    text = "".join(parts).strip()
    if not text:
        raise AIOutputError("the interviewer reply was empty")
    return {"messages": [AIMessage(content=text, id=str(uuid.uuid4()))]}


async def extract(state: InterviewState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    ctx = runtime.context
    message = _last(state["messages"], "human")
    before = state["messages"][: state["messages"].index(message)] if message else []
    question = _last(before, "ai")
    messages = [
        SystemMessage(prompts.EXTRACTION_SYSTEM.format(goals=prompts.goals_text())),
        HumanMessage(
            prompts.EXTRACTION_USER.format(
                profile=profile_service.render(ctx.snapshot),
                checklist=prompts.checklist_text(ctx.progress),
                question=question.text if question else "",
                message=message.text.replace("</mensagem>", "") if message else "",
            )
        ),
    ]
    try:
        patch = await ctx.ai.chat.structured(ProfilePatch).ainvoke(messages)
    except Exception as exc:  # a failed extraction loses this turn's facts, never the chat
        log.warning("interview extraction failed: %s (prompt %s)", describe(exc), prompts.VERSION)
        return {"patch": None}
    return {"patch": patch.model_dump(mode="json")}


async def apply(state: InterviewState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
    ctx = runtime.context
    if raw := state.get("patch"):
        patch = ProfilePatch.model_validate(raw)
        try:
            async with ctx.db.begin_nested():  # all or nothing; the session stays usable
                await profile_service.apply_patch(
                    ctx.db, ctx.ai.embeddings, ctx.snapshot, patch, ctx.turn_id
                )
                await store.update_checklist(ctx.db, ctx.user, patch)
        except Exception as exc:
            log.warning(
                "applying the extraction failed: %s (prompt %s)", describe(exc), prompts.VERSION
            )
    progress = await store.checklist_progress(ctx.db, ctx.user)
    planned = TurnPlan.model_validate(state.get("plan") or FALLBACK_PLAN.model_dump())
    if planned.done and not any(i.status is ChecklistStatus.MISSING for i in progress.items):
        # The interviewer said goodbye: partial leftovers don't keep the interview open.
        progress = await store.settle_checklist(ctx.db, ctx.user)
    return {"completed": planned.done and progress.all_done}


def build_graph(
    checkpointer: BaseCheckpointSaver[str] | None,
) -> CompiledStateGraph[InterviewState, TurnContext, InterviewState, InterviewState]:
    graph = StateGraph(InterviewState, context_schema=TurnContext)
    graph.add_node("plan", plan)
    graph.add_node("speak", speak)
    graph.add_node("extract", extract)
    graph.add_node("apply", apply)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "speak")
    graph.add_edge("plan", "extract")
    graph.add_edge(["speak", "extract"], "apply")
    graph.add_edge("apply", END)
    return graph.compile(checkpointer=checkpointer, name="interview")
