"""Skill management routes: list, get, update, delete, upload, download."""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from quart import Response, jsonify, request

if TYPE_CHECKING:
    from dashboard.server import Dashboard


def register(dashboard: Dashboard) -> None:
    app = dashboard.app

    def _skill_manager():
        ps = dashboard.lifecycle.process_stage
        return ps.skill_manager if ps else None

    @app.route("/api/skills", methods=["GET"])
    async def list_skills():
        sm = _skill_manager()
        if sm is None:
            return jsonify([])
        skills = sm.list_skills(active_only=False)
        return jsonify(
            [
                {
                    "name": s.name,
                    "description": s.description,
                    "path": s.path,
                    "active": s.active,
                    "root": s.root,
                    "source": s.source,
                    "format": s.format,
                    "companion_files": s.companion_files,
                    "warnings": s.warnings,
                    "can_delete": s.can_delete,
                }
                for s in skills
            ]
        )

    @app.route("/api/skills/<name>")
    async def get_skill(name: str):
        sm = _skill_manager()
        if sm is None:
            return jsonify({"error": "skill manager not available"}), 503
        try:
            loaded = sm.load_skill(name, active_only=False)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except KeyError:
            return jsonify({"error": "skill not found"}), 404
        s = loaded.info
        return jsonify(
            {
                "name": s.name,
                "description": s.description,
                "path": s.path,
                "active": s.active,
                "root": s.root,
                "source": s.source,
                "format": s.format,
                "companion_files": s.companion_files,
                "warnings": s.warnings,
                "can_delete": s.can_delete,
                "content": loaded.content,
            }
        )

    @app.route("/api/skills/<name>", methods=["PUT"])
    async def update_skill(name: str):
        data = await request.get_json()
        sm = _skill_manager()
        if sm is None:
            return jsonify({"error": "skill manager not available"}), 503
        try:
            if "active" in data:
                sm.set_skill_active(name, bool(data["active"]))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        dashboard.lifecycle.save_config()
        dashboard._reload_skills_prompt()
        return jsonify({"ok": True})

    @app.route("/api/skills/<name>", methods=["DELETE"])
    async def delete_skill(name: str):
        sm = _skill_manager()
        if sm is None:
            return jsonify({"error": "skill manager not available"}), 503
        try:
            sm.delete_skill(name)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except PermissionError as e:
            return jsonify({"error": str(e)}), 403
        dashboard.lifecycle.save_config()
        dashboard._reload_skills_prompt()
        return jsonify({"ok": True})

    @app.route("/api/skills/upload", methods=["POST"])
    async def upload_skill():
        sm = _skill_manager()
        if sm is None:
            return jsonify({"error": "skill manager not available"}), 503
        files = await request.files
        uploaded = files.get("file")
        if uploaded is None:
            return jsonify({"error": "no file uploaded"}), 400
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp.write(uploaded.read())
                tmp_path = tmp.name
            names = sm.install_skill_from_zip(tmp_path, overwrite=True)
            dashboard.lifecycle.save_config()
            dashboard._reload_skills_prompt()
            return jsonify({"ok": True, "installed": names})
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    @app.route("/api/skills/<name>/download")
    async def download_skill(name: str):
        sm = _skill_manager()
        if sm is None:
            return jsonify({"error": "skill manager not available"}), 503
        try:
            skill = sm.get_skill(name, active_only=False)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        if skill is None:
            return jsonify({"error": "skill not found"}), 404
        skill_dir = Path(skill.path).parent
        if not skill_dir.exists() or not skill_dir.is_dir():
            return jsonify({"error": "skill not found"}), 404
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in skill_dir.rglob("*"):
                if file_path.is_file():
                    arcname = str(file_path.relative_to(skill_dir.parent))
                    zf.write(file_path, arcname=arcname)
        buf.seek(0)
        return Response(
            buf.getvalue(),
            mimetype="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
        )
