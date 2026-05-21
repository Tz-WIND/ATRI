"""Chemical structure drawing tool."""

from __future__ import annotations

import base64
import importlib
import secrets
import threading
from typing import Any

from .base import Tool, ToolCapabilities

_GENERATED_BATCH_MARKER = "ATRI_GENERATED_CHEM_IMAGE_BATCH:"
_GENERATED_BATCHES: dict[str, list[dict[str, Any]]] = {}
_GENERATED_BATCH_LOCK = threading.Lock()
_MIN_DIMENSION = 120
_MAX_DIMENSION = 2400
_DEFAULT_MOLECULE_SIZE = (420, 320)
_DEFAULT_REACTION_SIZE = (900, 260)


def pop_generated_chem_images_from_result(result: str) -> list[dict[str, Any]]:
    """Consume generated chemical image batches referenced by a tool result."""
    batch_ids = []
    for line in str(result or "").splitlines():
        if line.startswith(_GENERATED_BATCH_MARKER):
            batch_id = line.split(":", 1)[1].strip()
            if batch_id:
                batch_ids.append(batch_id)
    if not batch_ids:
        return []

    images: list[dict[str, Any]] = []
    with _GENERATED_BATCH_LOCK:
        for batch_id in batch_ids:
            images.extend(_GENERATED_BATCHES.pop(batch_id, []))
    return images


def _store_generated_images(images: list[dict[str, Any]]) -> str:
    batch_id = secrets.token_urlsafe(12)
    with _GENERATED_BATCH_LOCK:
        if len(_GENERATED_BATCHES) > 100:
            oldest_key = next(iter(_GENERATED_BATCHES))
            _GENERATED_BATCHES.pop(oldest_key, None)
        _GENERATED_BATCHES[batch_id] = images
    return batch_id


def _clamp_dimension(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, _MIN_DIMENSION), _MAX_DIMENSION)


def _rdkit_modules() -> tuple[Any, Any, Any, Any]:
    try:
        chem = importlib.import_module("rdkit.Chem")
        rd_logger = importlib.import_module("rdkit.RDLogger")
        rd_chem_reactions = importlib.import_module("rdkit.Chem.rdChemReactions")
        rd_mol_draw_2d = importlib.import_module("rdkit.Chem.Draw.rdMolDraw2D")
    except ImportError as e:
        raise RuntimeError(
            "RDKit is not installed. Run `uv sync` to install the project dependencies."
        ) from e
    return chem, rd_logger, rd_chem_reactions, rd_mol_draw_2d


def _parse_molecule(raw_input: str, input_format: str):
    chem, rd_logger, _rd_chem_reactions, _rd_mol_draw_2d = _rdkit_modules()
    text = raw_input.strip()
    if not text:
        raise ValueError("input is required")

    rd_logger.DisableLog("rdApp.error")
    try:
        if input_format == "auto":
            if text.lower().startswith("inchi="):
                mol = chem.MolFromInchi(text)
            elif "\n" in text:
                mol = chem.MolFromMolBlock(text, sanitize=True)
            else:
                mol = chem.MolFromSmiles(text)
        elif input_format == "smiles":
            mol = chem.MolFromSmiles(text)
        elif input_format == "inchi":
            mol = chem.MolFromInchi(text)
        elif input_format == "molblock":
            mol = chem.MolFromMolBlock(text, sanitize=True)
        else:
            raise ValueError(f"input_format '{input_format}' is not valid for molecule mode")
    finally:
        rd_logger.EnableLog("rdApp.error")

    if mol is None:
        raise ValueError("Could not parse molecule input")
    chem.rdDepictor.Compute2DCoords(mol)
    return mol


def _parse_reaction(raw_input: str, input_format: str):
    _chem, rd_logger, rd_chem_reactions, _rd_mol_draw_2d = _rdkit_modules()
    text = raw_input.strip()
    if not text:
        raise ValueError("input is required")
    if input_format not in {"auto", "reaction_smiles"}:
        raise ValueError(f"input_format '{input_format}' is not valid for reaction mode")

    rd_logger.DisableLog("rdApp.error")
    try:
        try:
            reaction = rd_chem_reactions.ReactionFromSmarts(text, useSmiles=True)
        except ValueError as e:
            raise ValueError("Could not parse reaction input") from e
    finally:
        rd_logger.EnableLog("rdApp.error")
    if reaction is None or not (
        reaction.GetNumReactantTemplates() and reaction.GetNumProductTemplates()
    ):
        raise ValueError("Could not parse reaction input")
    return reaction


def _draw_molecule_svg(mol, width: int, height: int, legend: str) -> str:
    _chem, _rd_logger, _rd_chem_reactions, rd_mol_draw_2d = _rdkit_modules()
    drawer = rd_mol_draw_2d.MolDraw2DSVG(width, height)
    drawer.DrawMolecule(mol, legend=legend)
    drawer.FinishDrawing()
    return str(drawer.GetDrawingText())


def _draw_reaction_svg(reaction, width: int, height: int) -> str:
    _chem, _rd_logger, _rd_chem_reactions, rd_mol_draw_2d = _rdkit_modules()
    drawer = rd_mol_draw_2d.MolDraw2DSVG(width, height)
    drawer.DrawReaction(reaction)
    drawer.FinishDrawing()
    return str(drawer.GetDrawingText())


def _svg_image(svg: str, name: str) -> dict[str, Any]:
    raw = svg.encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return {
        "url": f"data:image/svg+xml;base64,{encoded}",
        "file": f"base64://{encoded}",
        "mime_type": "image/svg+xml",
        "size": len(raw),
        "name": name,
    }


class ChemDrawTool(Tool):
    name = "chem_draw"
    description = (
        "Draw chemical structures as SVG. Supports molecule mode for SMILES, InChI, "
        "or MolBlock, and reaction mode for reaction SMILES."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["molecule", "reaction"],
                "description": "Draw a single molecule or a reaction.",
                "default": "molecule",
            },
            "input": {
                "type": "string",
                "description": "SMILES, InChI, MolBlock, or reaction SMILES text to draw.",
            },
            "input_format": {
                "type": "string",
                "enum": ["auto", "smiles", "inchi", "molblock", "reaction_smiles"],
                "description": "Input parser to use. Auto detects InChI, MolBlock, or SMILES.",
                "default": "auto",
            },
            "width": {
                "type": "integer",
                "description": "SVG width in pixels.",
            },
            "height": {
                "type": "integer",
                "description": "SVG height in pixels.",
            },
            "legend": {
                "type": "string",
                "description": "Optional molecule label shown below the drawing.",
            },
            "save_path": {
                "type": "string",
                "description": "Optional workspace-relative path where the SVG should be saved.",
            },
        },
        "required": ["input"],
    }
    capabilities = ToolCapabilities(
        capability="chemistry.draw",
        writes_files=True,
        supports_parallel=True,
    )

    def execute(
        self,
        input: str,  # noqa: A002
        mode: str = "molecule",
        input_format: str = "auto",
        width: int | None = None,
        height: int | None = None,
        legend: str = "",
        save_path: str = "",
        **kwargs: Any,
    ) -> str:
        try:
            mode = str(mode or "molecule").strip().lower()
            input_format = str(input_format or "auto").strip().lower()
            if mode not in {"molecule", "reaction"}:
                raise ValueError("mode must be 'molecule' or 'reaction'")
            if input_format not in {"auto", "smiles", "inchi", "molblock", "reaction_smiles"}:
                raise ValueError("input_format is not supported")

            default_width, default_height = (
                _DEFAULT_REACTION_SIZE if mode == "reaction" else _DEFAULT_MOLECULE_SIZE
            )
            draw_width = _clamp_dimension(width, default_width)
            draw_height = _clamp_dimension(height, default_height)

            if mode == "reaction":
                reaction = _parse_reaction(str(input or ""), input_format)
                svg = _draw_reaction_svg(reaction, draw_width, draw_height)
            else:
                mol = _parse_molecule(str(input or ""), input_format)
                svg = _draw_molecule_svg(mol, draw_width, draw_height, str(legend or "").strip())

            if save_path:
                path = self.resolve_path(str(save_path))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(svg, encoding="utf-8")

            image = _svg_image(svg, "chem-draw.svg")
            batch_id = _store_generated_images([image])
        except (OSError, PermissionError, RuntimeError, ValueError) as e:
            return f"Error: {e}"

        lines = [
            "Generated chemical SVG for the chat reply.",
            f"{_GENERATED_BATCH_MARKER} {batch_id}",
            f"Mode: {mode} | Input format: {input_format} | Size: {draw_width}x{draw_height}",
            (
                "The SVG data is attached automatically; do not print or rewrite "
                "the internal batch id."
            ),
        ]
        if save_path:
            lines.append(f"Saved SVG to {save_path}")
        return "\n".join(lines)
