"""AutoBatteryResearch 专家接口的输入输出契约。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AutoBatteryResearchConsultInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(description="需要 AutoBatteryResearch 分析的锂电池科研问题")
    context: str | None = Field(default=None, description="用户提供的研究条件、已有证据或限制")

    @field_validator("question")
    @classmethod
    def non_empty_question(cls, value):
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class AutoBatteryResearchExperimentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(description="需要生成完整电池实验方案的科研目标")
    context: str | None = Field(default=None, description="已有科研背景和实验结果")
    constraints: dict[str, Any] | None = Field(default=None, description="科研与实验约束")
    available_resources: dict[str, Any] | None = Field(default=None, description="可用材料、设备及实验条件")

    @field_validator("goal")
    @classmethod
    def non_empty_goal(cls, value):
        if not value.strip():
            raise ValueError("goal must not be blank")
        return value


class AutoBatteryResearchError(BaseModel):
    type: Literal[
        "invalid_input", "agent_execution_error", "external_tool_error", "timeout",
        "insufficient_evidence", "missing_report", "missing_report_file", "report_mismatch",
    ]
    message: str


class AutoBatteryResearchExperimentMCPOutput(BaseModel):
    report_markdown: str
    report_path: str | None
    status: Literal["complete", "partial", "error"]
    error: AutoBatteryResearchError | None = None
