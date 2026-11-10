"""S-center 通用单数值、标题角色与基础 A2UI 输出回归。"""

import json
from typing import Any

import pytest

from services.card_validation import CompactDslValidationError, validate_compact_dsl
from services.card_validation.api import validate_card
from services.compact_component_runtime import component_visual_recipe
from services.compact_dsl_a2ui_converter import (
    CompactDslConversionError,
    convert_compact_dsl_to_a2ui,
)
from services.compact_plan import (
    CompactPlanValidationError,
    compact_plan_required_route,
    parse_compact_plan_call,
)
from services.compact_region_layout import reference_region_boxes
from services.generation_pipeline import DesignCompactProcessor, DslProcessingContext


def _source(props: dict[str, Any]) -> str:
    rows = [
        ["root", "Layout", {"layout": "S-center"}, ["display"]],
        ["display", "DataDisplay", {"label": "耳机电量", "value": 85, **props}],
        ["/data/subtitle", "左耳"],
        ["/data/value", 85],
    ]
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


def _components(props: dict[str, Any]) -> dict[str, Any]:
    converted = convert_compact_dsl_to_a2ui(_source(props), size="2x2")
    messages = [json.loads(line) for line in converted.splitlines()]
    assert len(messages) == 3
    update = messages[1].get("updateComponents")
    assert isinstance(update, dict)
    nodes = update.get("components")
    assert isinstance(nodes, list)
    result = {}
    for node in nodes:
        identifier = node.get("id")
        assert isinstance(identifier, str)
        result[identifier] = node
    return result


def _node(nodes: dict[str, Any], identifier: str) -> dict[str, Any]:
    node = nodes.get(identifier)
    assert isinstance(node, dict)
    return node


def _styles(nodes: dict[str, Any], identifier: str) -> dict[str, Any]:
    styles = _node(nodes, identifier).get("styles")
    assert isinstance(styles, dict)
    return styles


def _task() -> dict[str, Any]:
    return {
        "size": "2x2", "appVersion": "1.0.0", "eventCandidates": [],
        "userQuery": "耳机电量 左耳 85 %",
        "dataModelSchema": {"data": {
            "subtitle": {"type": "string", "sampleValue": "左耳"},
            "value": {"type": "integer", "sampleValue": 85},
        }},
    }


def _plan() -> dict[str, Any]:
    facts = []
    for display_role, text in (("title", "耳机电量"), ("subtitle", "左耳"),
                               ("value", "85"), ("unit", "%")):
        facts.append({"requirement": text, "text": text, "priority": "must",
                      "componentHints": ["DataDisplay"], "dataDisplayRole": display_role})
    core = facts[2]
    core.pop("text")
    core["dataId"] = "/data/value"
    return {"info_required": facts, "layoutHints": ["S-center"]}


@pytest.mark.parametrize("subtitle", [None, "", "  ", "左耳"])
def test_numeric_focus_title_geometry(subtitle: str | None) -> None:
    nodes = _components({"secondaryInfo": subtitle, "unit": "%"})
    assert _styles(nodes, "root").get("padding") == 12
    assert _styles(nodes, "display_title_area").get("height") == 44
    assert _styles(nodes, "display").get("height") == "matchParent"
    assert _styles(nodes, "display").get("justifyContent") == "start"
    assert _styles(nodes, "display_value_row").get("height") == 74
    assert _styles(nodes, "display_value").get("height") == 70
    area = _styles(nodes, "display_numeric_area")
    assert "height" not in area
    assert area.get("layoutWeight") == 1
    assert area.get("justifyContent") == "center"
    assert area.get("constraintSize") == {"minHeight": 74}
    assert _styles(nodes, "display_title_area").get("justifyContent") == "center"
    assert _styles(nodes, "display_label").get("height") == 21
    assert _styles(nodes, "display_label").get("fontSize") == 16
    assert _styles(nodes, "display_value").get("fontSize") == 56
    assert _styles(nodes, "display_unit").get("fontSize") == 16
    assert _styles(nodes, "display_value_row").get("alignItems") == "bottom"
    assert _styles(nodes, "display_value_row").get("justifyContent") == "center"
    assert _styles(nodes, "display_unit").get("padding", {}).get("bottom", 0) > 0
    expected = ["display_label"]
    if subtitle == "左耳":
        expected.append("display_subtitle_group")
        assert _styles(nodes, "display_subtitle_group").get("padding") == {"top": 2}
        assert _styles(nodes, "display_secondary").get("height") == 21
        assert _styles(nodes, "display_secondary").get("maxLines") == 1
    assert _node(nodes, "display_title_area").get("children") == expected
    assert all(node.get("component") != "DataDisplay" for node in nodes.values())


@pytest.mark.parametrize("height", [150, 200, 260])
@pytest.mark.parametrize("subtitle", [None, "左耳"])
def test_numeric_area_receives_remaining_height(height: int, subtitle: str | None) -> None:
    nodes = _components({"secondaryInfo": subtitle, "unit": "%"})
    boxes = reference_region_boxes(
        list(nodes.values()), size="2x2",
        profile={"sizes": {"2x2": {"width": 150, "height": height}}},
    )
    display = boxes.get("display")
    area = boxes.get("display_numeric_area")
    row = boxes.get("display_value_row")
    assert display is not None and area is not None and row is not None
    assert display.height == height - 24
    assert area.height == height - 24 - 44 - 8
    assert row.height == 74


@pytest.mark.parametrize("height,passes", [(150, True), (200, True), (140, False)])
def test_numeric_focus_height_budget(height: int, passes: bool) -> None:
    context = {
        "task_spec": _task(), "card_spec": {"suggestSize": "2x2"},
        "layout_scope": "S-center",
        "protocol_profile": {"sizes": {"2x2": {"width": 150, "height": height}}},
    }
    if passes:
        validate_compact_dsl(_source({"unit": "%"}), **context)
    else:
        with pytest.raises(CompactDslValidationError, match="vertical layout"):
            validate_compact_dsl(_source({"unit": "%"}), **context)


def test_numeric_focus_without_unit_centers_value() -> None:
    nodes = _components({})
    assert _node(nodes, "display_value_row").get("children") == ["display_value"]
    assert "display_unit" not in nodes


def test_dynamic_subtitle_keeps_binding_and_conditional_group() -> None:
    nodes = _components({"secondaryInfo": {"path": "/data/subtitle"}, "unit": "%"})
    assert _node(nodes, "display_secondary").get("content") == "{{ ${/data/subtitle} }}"
    visibility = _styles(nodes, "display_subtitle_group").get("visibility")
    assert isinstance(visibility, str)
    assert "${/data/subtitle}" in visibility
    assert "'visible' : 'none'" in visibility
    assert _node(nodes, "display_title_area").get("children") == [
        "display_label", "display_subtitle_group",
    ]


def test_legacy_numeric_supporting_text_is_not_guessed_as_unit() -> None:
    nodes = _components({"supportingText": "10:30 更新"})
    assert _node(nodes, "display_supporting").get("content") == "10:30 更新"
    assert _styles(nodes, "display_value").get("fontSize") == 48
    assert "display_unit" not in nodes


@pytest.mark.parametrize("props", [
    {"secondaryInfo": 7}, {"unit": False},
    {"supportingText": "天", "unit": "天"},
    {"value": "优", "unit": "分"},
])
def test_invalid_numeric_focus_props_are_rejected(props: dict[str, Any]) -> None:
    with pytest.raises(CompactDslConversionError):
        _components(props)


def test_public_double_line_title_recipe_is_unchanged() -> None:
    recipe = component_visual_recipe("DoubleLineTitle")
    parts = recipe.get("parts")
    assert isinstance(parts, dict)
    title = parts.get("title")
    assert isinstance(title, dict)
    assert title.get("styles", {}).get("fontSize") == 12
    root = parts.get("root")
    assert isinstance(root, dict)
    assert root.get("styles", {}).get("itemMargin") == 4


def test_plan_preserves_roles_and_locks_one_core() -> None:
    raw = json.dumps({"name": "submit_card_plan", "arguments": _plan()}, ensure_ascii=False)
    result = parse_compact_plan_call(raw, _task())
    assert compact_plan_required_route(result.plan, _task()) == {
        "layout": "S-center", "component": "DataDisplay",
    }
    facts = result.plan.get("info_required")
    assert isinstance(facts, list)
    assert facts[1].get("dataDisplayRole") == "subtitle"


@pytest.mark.parametrize("invalid_role", ["body", [], 7])
def test_plan_rejects_invalid_roles(invalid_role: Any) -> None:
    plan = _plan()
    facts = plan.get("info_required")
    assert isinstance(facts, list)
    facts[0]["dataDisplayRole"] = invalid_role
    raw = json.dumps({"name": "submit_card_plan", "arguments": plan}, ensure_ascii=False)
    with pytest.raises(CompactPlanValidationError):
        parse_compact_plan_call(raw, _task())


def test_role_must_land_in_accepted_property() -> None:
    source = _source({"label": "耳机电量 左耳", "unit": "%"})
    with pytest.raises(CompactDslValidationError, match="DataDisplay.secondaryInfo"):
        validate_compact_dsl(source, task_spec=_task(), card_spec={"suggestSize": "2x2"},
                             compact_plan=_plan(), layout_scope="S-center")


@pytest.mark.parametrize("props", [
    {"secondaryInfo": "这是超过小卡单行容量的很长副标题", "unit": "%"},
    {"value": 123456789, "unit": "步"},
])
def test_overflow_is_rejected_without_shrinking(props: dict[str, Any]) -> None:
    with pytest.raises(CompactDslValidationError):
        validate_compact_dsl(_source(props), task_spec=_task(),
                             card_spec={"suggestSize": "2x2"}, layout_scope="S-center")


def test_full_processor_accepts_generic_numeric_focus() -> None:
    result = DesignCompactProcessor().process(
        _source({"value": {"path": "/data/value"}, "secondaryInfo": "左耳", "unit": "%"}),
        DslProcessingContext(size="2x2", task_spec=_task(),
                             card_spec={"suggestSize": "2x2"},
                             protocol_profile={"version": "v0.9"},
                             design_profile_id="design-compact-dsl",
                             compact_plan=_plan(), layout_scope="S-center"),
    )
    assert not result.errors, result.errors
    assert '"fontSize":56' in result.standard_dsl.replace(" ", "")


@pytest.mark.parametrize("unit,offset", [("天", 14), ("%", 18)])
def test_selected_unit_offsets(unit: str, offset: float) -> None:
    nodes = _components({"unit": unit})
    styles = _styles(nodes, "display_unit")
    if unit == "天":
        assert styles.get("height") == 21
        assert styles.get("margin", {}).get("bottom") == offset
        assert styles.get("padding", {}).get("bottom", 0) == 0
    else:
        assert styles.get("padding", {}).get("bottom") == offset
        assert styles.get("height") == 16 + offset


def test_static_number_can_cover_accepted_value_role() -> None:
    plan = _plan()
    facts = plan.get("info_required")
    assert isinstance(facts, list)
    core = facts[2]
    core.pop("dataId")
    core["text"] = "85"
    validate_compact_dsl(
        _source({"secondaryInfo": "左耳", "unit": "%"}),
        task_spec=_task(), card_spec={"suggestSize": "2x2"}, compact_plan=plan,
        layout_scope="S-center",
    )


@pytest.mark.parametrize("field", ["label", "secondaryInfo"])
def test_numeric_title_rejects_explicit_newline(field: str) -> None:
    with pytest.raises(CompactDslValidationError, match="must be one line"):
        validate_compact_dsl(
            _source({field: "主行\n次行", "unit": "%"}),
            task_spec=_task(), card_spec={"suggestSize": "2x2"}, layout_scope="S-center",
        )


def test_plan_rejects_a_second_core_role() -> None:
    plan = _plan()
    facts = plan.get("info_required")
    assert isinstance(facts, list)
    facts.append({"requirement": "86", "text": "86", "priority": "must",
                  "componentHints": ["DataDisplay"], "dataDisplayRole": "value"})
    raw = json.dumps({"name": "submit_card_plan", "arguments": plan}, ensure_ascii=False)
    with pytest.raises(CompactPlanValidationError, match="unique content role"):
        parse_compact_plan_call(raw, _task())


def test_full_processor_preserves_dynamic_subtitle_role() -> None:
    plan = _plan()
    facts = plan.get("info_required")
    assert isinstance(facts, list)
    subtitle = facts[1]
    subtitle.pop("text")
    subtitle["dataId"] = "/data/subtitle"
    result = DesignCompactProcessor().process(
        _source({"value": {"path": "/data/value"},
                 "secondaryInfo": {"path": "/data/subtitle"}, "unit": "%"}),
        DslProcessingContext(size="2x2", task_spec=_task(),
                             card_spec={"suggestSize": "2x2"},
                             protocol_profile={"version": "v0.9"}, compact_plan=plan,
                             layout_scope="S-center"),
    )
    assert not result.errors, result.errors
    assert "visibility" in result.standard_dsl


def test_dynamic_unit_keeps_conditional_visibility() -> None:
    nodes = _components({"unit": {"path": "/data/subtitle"}})
    assert "${/data/subtitle}" in _styles(nodes, "display_unit").get("visibility", "")


@pytest.mark.parametrize("subtitle", [None, "左耳", {"path": "/data/subtitle"}])
def test_standard_a2ui_protocol_accepts_numeric_focus(subtitle: Any) -> None:
    converted = convert_compact_dsl_to_a2ui(
        _source({"secondaryInfo": subtitle, "unit": "%"}), size="2x2",
    )
    report = validate_card(dsl_text=converted)
    errors = []
    for diagnostic in report.diagnostics:
        if diagnostic.file_kind == "cardspec":
            continue
        if diagnostic.severity == "error":
            errors.append(diagnostic)
    assert not errors, errors
