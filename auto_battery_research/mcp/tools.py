"""AutoBatteryResearch 高层专家 Tool；内部科研工具不在此公开。"""

import logging
from typing import Annotated

from mcp.types import CallToolResult, TextContent
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import Field, ValidationError

from .schemas import AutoBatteryResearchExperimentMCPOutput

L = logging.getLogger("AutoBatteryResearch.MCP")


async def battery_consult(
    question: str, context: str | None = None, *, service
) -> CallToolResult:
    try:
        answer = await service.consult(question=question, context=context)
        if not isinstance(answer, str) or not answer.strip():
            return CallToolResult(
                content=[TextContent(type="text", text="AutoBatteryResearch 调用失败：LLM 未生成可用回答")],
                isError=True,
            )
    except ValidationError:
        text = "AutoBatteryResearch 调用失败：输入无效"
    except (TimeoutError, APITimeoutError):
        L.exception("AutoBatteryResearch consultation timed out")
        text = "AutoBatteryResearch 调用失败：模型请求超时"
    except (ConnectionError, APIConnectionError, APIStatusError):
        L.exception("AutoBatteryResearch consultation provider failed")
        text = "AutoBatteryResearch 调用失败：模型服务不可用"
    except Exception:
        L.exception("AutoBatteryResearch consultation failed")
        text = "AutoBatteryResearch 调用失败：模型调用失败"
    else:
        return CallToolResult(content=[TextContent(type="text", text=answer)], isError=False)
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        isError=True,
    )


async def design_battery_experiment(
    goal: str, context: str | None = None, constraints: dict | None = None,
    available_resources: dict | None = None, *, service,
) -> Annotated[CallToolResult, AutoBatteryResearchExperimentMCPOutput]:
    result = await service.design_battery_experiment(
        goal=goal, context=context, constraints=constraints,
        available_resources=available_resources,
    )
    output = AutoBatteryResearchExperimentMCPOutput.model_validate(result)
    if output.status == "error":
        output.report_markdown = ""
        output.report_path = None
        if output.error is None:
            output.error = {"type": "agent_execution_error", "message": "AutoBatteryResearch execution failed"}
        text = f"AutoBatteryResearch 实验方案生成失败：{output.error.message}"
    elif output.status == "partial":
        message = output.error.message if output.error else "AutoBatteryResearch 工作流未完成"
        text = f"> ⚠️ AutoBatteryResearch 实验方案未完成：{message}\n\n{output.report_markdown}".rstrip()
    else:
        text = output.report_markdown
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=output.model_dump(mode="json"),
        isError=output.status == "error",
    )


def register_tools(server, service):
    @server.tool(
        name="battery_consult",
        description=(
            "Ask AutoBatteryResearch a lightweight lithium-battery question. "
            "Returns an LLM-generated conversational answer without retrieval, evidence verification, or a research report. "
            "Use design_battery_experiment when a full experimental plan is needed. "
            "No scientific tools or research workflow are run."
        ),
    )
    async def consult(
        question: Annotated[str, Field(description="需要 AutoBatteryResearch 回答的锂电池问题")],
        context: Annotated[str | None, Field(description="可选的对话背景或前文摘要；服务端不存储会话历史")] = None,
    ) -> CallToolResult:
        return await battery_consult(question, context, service=service)

    @server.tool(
        name="design_battery_experiment",
        description=(
            "Design a complete lithium-battery experimental plan using AutoBatteryResearch. "
            "Use when the user needs a battery research scheme or experimental plan, including "
            "battery-system selection, scientific rationale, procedure, characterization, testing "
            "and validation. AutoBatteryResearch performs internal scientific reasoning and tool "
            "use and returns its complete generated Markdown report. Do not use for simple factual questions."
        ),
    )
    async def design_experiment(
        goal: Annotated[str, Field(description="需要生成完整电池实验方案的科研目标")],
        context: Annotated[str | None, Field(description="已有科研背景和实验结果")] = None,
        constraints: Annotated[dict | None, Field(description="科研与实验约束")] = None,
        available_resources: Annotated[dict | None, Field(description="可用材料、设备及实验条件")] = None,
    ) -> Annotated[CallToolResult, AutoBatteryResearchExperimentMCPOutput]:
        return await design_battery_experiment(
            goal, context, constraints, available_resources, service=service,
        )
