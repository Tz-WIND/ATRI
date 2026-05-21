import base64
from typing import Any

from core.pipeline.stages.process import (
    _attach_generated_images_to_assistant_message,
    _image_components_from_extras,
)
from core.tools.chemistry import ChemDrawTool, pop_generated_chem_images_from_result


def test_chem_draw_tool_renders_smiles_svg_attachment(tmp_path):
    result = ChemDrawTool(str(tmp_path)).execute(
        mode="molecule",
        input="CCO",
        input_format="smiles",
        legend="ethanol",
    )

    assert "Generated chemical SVG for the chat reply." in result
    images = pop_generated_chem_images_from_result(result)

    assert len(images) == 1
    image = images[0]
    assert image["mime_type"] == "image/svg+xml"
    assert image["name"] == "chem-draw.svg"
    assert image["size"] > 0
    assert image["url"].startswith("data:image/svg+xml;base64,")
    encoded = image["url"].split(",", 1)[1]
    svg = base64.b64decode(encoded).decode("utf-8")
    assert "<svg" in svg
    assert "class='legend'" in svg
    assert list(tmp_path.iterdir()) == []


def test_chem_draw_tool_renders_inchi_and_saves_svg(tmp_path):
    result = ChemDrawTool(str(tmp_path)).execute(
        mode="molecule",
        input="InChI=1S/H2O/h1H2",
        input_format="inchi",
        save_path="drawings/water.svg",
    )

    assert "Saved SVG to drawings/water.svg" in result
    saved = tmp_path / "drawings" / "water.svg"
    assert saved.exists()
    assert "<svg" in saved.read_text(encoding="utf-8")


def test_chem_draw_tool_renders_reaction_smiles(tmp_path):
    result = ChemDrawTool(str(tmp_path)).execute(
        mode="reaction",
        input="CCO.O=O>>CC=O.O",
        input_format="reaction_smiles",
        width=900,
        height=260,
    )

    assert "Generated chemical SVG for the chat reply." in result
    images = pop_generated_chem_images_from_result(result)
    svg = base64.b64decode(images[0]["url"].split(",", 1)[1]).decode("utf-8")
    assert "<svg" in svg


def test_chem_draw_tool_reports_invalid_inputs(tmp_path):
    molecule_result = ChemDrawTool(str(tmp_path)).execute(
        mode="molecule",
        input="not-a-smiles",
        input_format="smiles",
    )
    reaction_result = ChemDrawTool(str(tmp_path)).execute(
        mode="reaction",
        input="not-a-reaction",
        input_format="reaction_smiles",
    )

    assert molecule_result.startswith("Error:")
    assert "Could not parse molecule" in molecule_result
    assert reaction_result.startswith("Error:")
    assert "Could not parse reaction" in reaction_result


def test_chem_draw_tool_blocks_save_path_escape(tmp_path):
    result = ChemDrawTool(str(tmp_path)).execute(
        mode="molecule",
        input="CCO",
        input_format="smiles",
        save_path="../escape.svg",
    )

    assert result.startswith("Error:")
    assert "outside workspace" in result


def test_generated_svg_can_be_surfaced_as_assistant_attachment(tmp_path):
    result = ChemDrawTool(str(tmp_path)).execute(mode="molecule", input="CCO")
    images = pop_generated_chem_images_from_result(result)

    components = _image_components_from_extras(images)
    messages: list[dict[str, Any]] = [{"role": "assistant", "content": "Here is the molecule."}]
    _attach_generated_images_to_assistant_message(messages, images)

    assert len(components) == 1
    assert components[0].mime_type == "image/svg+xml"
    assert messages[0]["_atri_attachments"][0]["type"] == "image/svg+xml"
