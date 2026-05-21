# Chemical Drawing Tool Design

## Purpose

Add a built-in AI agent tool for drawing chemical structures. The first version supports both single molecule drawings and reaction drawings without splitting them into separate tools.

## Scope

The tool is named `chem_draw`.

It supports:

- Molecules from SMILES, InChI, or MolBlock.
- Reactions from reaction SMILES.
- SVG output by default.
- Optional SVG file saving inside the configured workspace.
- Chat attachment output so the user can see the drawing directly in the conversation.

It does not include a browser-based chemical editor in this version. It also does not accept SVG uploads from users; generated SVG attachments are output-only.

## Architecture

Add `core/tools/chemistry.py` with a `ChemDrawTool` class derived from `Tool`.

Register it in `core/tools/__init__.py` so normal agents and sub-agents receive it through the existing tool registry.

Use RDKit as the chemistry backend:

- `Chem.MolFromSmiles`, `Chem.MolFromInchi`, and `Chem.MolFromMolBlock` parse molecule inputs.
- `rdChemReactions.ReactionFromSmarts(..., useSmiles=True)` parses reaction SMILES.
- RDKit drawing APIs produce SVG.

The tool exposes one JSON-schema interface:

- `mode`: `molecule` or `reaction`.
- `input`: the molecular or reaction text.
- `input_format`: `auto`, `smiles`, `inchi`, `molblock`, or `reaction_smiles`.
- `width` and `height`: optional drawing dimensions with safe bounds.
- `legend`: optional label.
- `save_path`: optional workspace-relative path for the SVG.

## Data Flow

The agent calls `chem_draw`.

The tool validates parameters, parses the input with RDKit, generates SVG text, and either:

- stores the generated SVG as an assistant chat attachment, or
- writes it to `save_path`, or
- does both when `save_path` is provided.

To keep this general, the existing NovelAI-specific generated image attachment handling should be extended into a shared generated-asset mechanism that can handle SVG output from `chem_draw` and existing raster images from `novelai_image`.

## Errors

Invalid input returns a concise `Error: ...` string. The tool should not expose RDKit internals unless they are useful to correct the input.

Expected errors include:

- Missing `input`.
- Unknown `mode` or `input_format`.
- Molecule parse failure.
- Reaction parse failure.
- Invalid or unsafe `save_path`.
- RDKit not installed.

## Tests

Add tests for:

- Tool registry metadata includes `chem_draw` with a non-`general` capability.
- Valid SMILES produces SVG.
- Valid InChI or MolBlock produces SVG.
- Valid reaction SMILES produces SVG.
- Invalid molecule input returns an error.
- Invalid reaction input returns an error.
- `save_path` writes only inside the workspace.
- Generated SVG can be surfaced as an assistant attachment.

## Dependency

Add RDKit to Python dependencies. The intended package is `rdkit`, unless local installation constraints require using the conda-style package externally instead. The code should fail gracefully when RDKit is unavailable.

## Acceptance Criteria

The agent can answer requests like:

- "Draw aspirin from SMILES."
- "Draw this reaction: CCO.O=O>>CC=O.O."
- "Save the molecule SVG to workspace/aspirin.svg."

The generated result is SVG, visible in chat, and optionally saved to a workspace file.
