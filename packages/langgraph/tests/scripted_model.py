"""Deterministic scripted chat model for adapter tests.

LangGraph's prebuilt agent requires a model that supports bind_tools; the
installed langchain-core GenericFakeChatModel does not, so this subclasses
BaseChatModel directly and replays a fixed script of AIMessages. A script
step may be an exception instance, which is raised exactly once (the model
"going rogue" mid-run); the node re-executes from the checkpoint on resume
and the script has moved on, so the run then completes.
"""
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


class RogueSignal(Exception):
    """Test-only signal: the agent 'went rogue' mid-run."""


class ScriptedModel(BaseChatModel):
    """Replays ``steps`` (AIMessage | Exception) then a fixed final message."""

    steps: list
    final: str = "Recovered: run complete."
    _idx: int = PrivateAttr(default=0)

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs,
    ) -> ChatResult:
        if self._idx < len(self.steps):
            step = self.steps[self._idx]
            self._idx += 1
            if isinstance(step, BaseException):
                raise step
            message = step
        else:
            message = AIMessage(content=self.final)
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "scripted"
