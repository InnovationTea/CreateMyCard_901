"""第一层静态数据判定：包含静态数据的请求不路由到模板路线。"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from models.generation import CandidateDataBinding
from services.template_generation.engine import pipeline as pipeline_module
from services.template_generation.engine.advanced.scope_planner import (
    TemplateRouteNotApplicable,
)
from services.template_generation.engine.cardplan.calendar_no_action_policy import (
    plan_calendar_no_action_fallback,
)
from services.template_generation.engine.cardplan.registry import CardPlanRegistry
from services.template_generation.engine.cardplan.template_retrieval import (
    TemplateSearchIntent,
    build_template_retrieval_prompt,
)
from services.template_generation.engine.pipeline import generate_template_a2ui
from services.template_generation.tests.test_calendar_full_extensions import (
    CalendarInputs,
    _inputs,
)
from services.template_generation.tests.test_template_generation import (
    _weather_card_spec,
    _weather_task_spec,
)

_WEATHER_FIELDS = ("/current/temperatureText", "/current/condition")
_WEATHER_BODY = 'Template("SingleFocusLayout@1",{},Template("WeatherOverviewFull@1",{}));'


def _weather_binding() -> CandidateDataBinding:
    return CandidateDataBinding(
        capabilityId="ViewWeather",
        writeResultTo="/data/weather",
        candidateOutputFields=["/current/temperatureText", "/current/condition"],
    )


def _calendar_case() -> CalendarInputs:
    task, bindings, card, intent = _inputs(
        ("/events/0/senderName", "/events/0/remindTime/0"),
        view_candidate=True,
    )
    task = task.model_copy(update={"size": "2x2", "userQuery": "展示下个日程的信息"})
    card["suggestSize"] = "2x2"
    return CalendarInputs(task, bindings, card, intent)


class _FirstLayerModel:
    """第一层按构造参数返回意图，并记录第二层是否被调用。"""

    def __init__(self, intent_extra: dict[str, Any]) -> None:
        self.intent_extra = intent_extra
        self.generate_json_calls = 0
        self.body_called = False

    async def generate_json(
        self,
        _prompt: list[dict[str, str]],
        *,
        phase: str,
    ) -> dict[str, Any]:
        self.generate_json_calls += 1
        assert phase == "template-retrieval-query"
        return self.first_layer_payload()

    def first_layer_payload(self) -> dict[str, Any]:
        raise NotImplementedError

    async def generate(
        self,
        _prompt: list[dict[str, str]],
        *_args: Any,
        **_kwargs: Any,
    ) -> str:
        self.body_called = True
        return _WEATHER_BODY


class _WeatherModel(_FirstLayerModel):
    def first_layer_payload(self) -> dict[str, Any]:
        return {
            "requiredOutputFieldsByCapability": {"ViewWeather": list(_WEATHER_FIELDS)},
            "action": [],
            **self.intent_extra,
        }


class _CalendarModel(_FirstLayerModel):
    def first_layer_payload(self) -> dict[str, Any]:
        intent = _calendar_case().intent
        return {
            **intent.model_dump(mode="json", by_alias=True),
            **self.intent_extra,
        }


def test_contains_static_data_field_parsing() -> None:
    base = {"requiredOutputFieldsByCapability": {"ViewWeather": list(_WEATHER_FIELDS)}}
    intent = TemplateSearchIntent.model_validate(base)
    assert intent.contains_static_data is False
    intent = TemplateSearchIntent.model_validate({**base, "containsStaticData": True})
    assert intent.contains_static_data is True
    with pytest.raises(ValidationError):
        TemplateSearchIntent.model_validate({**base, "containsStaticData": "true"})


def test_first_layer_prompt_exposes_static_data_rule() -> None:
    prompt = build_template_retrieval_prompt(
        _weather_task_spec(),
        CardPlanRegistry(),
        (_weather_binding(),),
    )
    system = prompt[0]["content"]
    assert "containsStaticData 判断用户明确要求展示的内容是否包含静态数据" in system
    assert "本轮卡片静态内容（用作展示的外部事实）" in system
    assert "能力调用参数" in system
    assert '"containsStaticData"' in system


@pytest.mark.asyncio
async def test_first_layer_static_data_rejects_template_route() -> None:
    model = _WeatherModel({"containsStaticData": True})
    with pytest.raises(TemplateRouteNotApplicable):
        await generate_template_a2ui(
            _weather_task_spec(),
            _weather_card_spec(),
            (_weather_binding(),),
            model,
        )
    assert model.generate_json_calls == 1
    assert model.body_called is False


@pytest.mark.asyncio
async def test_static_calendar_request_never_enters_calendar_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _calendar_case()
    fallback_calls: list[Any] = []

    def record_fallback(*args: Any, **kwargs: Any) -> Any:
        fallback_calls.append(args[0])
        return plan_calendar_no_action_fallback(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "plan_calendar_no_action_fallback", record_fallback)
    model = _CalendarModel({"containsStaticData": True})
    with pytest.raises(TemplateRouteNotApplicable):
        await generate_template_a2ui(inputs.task, inputs.card, inputs.bindings, model)
    assert model.generate_json_calls == 1
    assert model.body_called is False
    assert fallback_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("intent_extra", [{}, {"containsStaticData": False}])
async def test_static_data_absent_keeps_template_route(intent_extra: dict[str, Any]) -> None:
    model = _WeatherModel(intent_extra)
    output = await generate_template_a2ui(
        _weather_task_spec(),
        _weather_card_spec(),
        (_weather_binding(),),
        model,
    )
    assert "WeatherOverviewFull@1" in output.template_ids
    assert model.body_called is True
