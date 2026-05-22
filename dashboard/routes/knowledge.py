"""Knowledge base management routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from quart import jsonify, request

if TYPE_CHECKING:
    from core.knowledge.manager import KnowledgeBaseManager
    from dashboard.server import Dashboard


def register(dashboard: Dashboard) -> None:
    app = dashboard.app

    @app.route("/api/knowledge/bases", methods=["GET"])
    async def list_knowledge_bases():
        manager = _knowledge_manager(dashboard)
        return jsonify({"items": await manager.list_knowledge_bases()})

    @app.route("/api/knowledge/bases", methods=["POST"])
    async def create_knowledge_base():
        manager = _knowledge_manager(dashboard)
        data = await request.get_json(silent=True) or {}
        try:
            kb = await manager.create_knowledge_base(
                name=str(data.get("name") or data.get("kb_name") or ""),
                description=str(data.get("description") or ""),
                embedding_provider=_optional_str(data.get("embedding_provider")),
                embedding_model=_optional_str(data.get("embedding_model")),
                rerank_provider=_optional_str(data.get("rerank_provider")),
                rerank_model=_optional_str(data.get("rerank_model")),
                chunk_size=_int_at_least(data.get("chunk_size"), 800, "chunk_size", 1),
                chunk_overlap=_int_at_least(data.get("chunk_overlap"), 120, "chunk_overlap", 0),
                top_k_dense=_int_at_least(data.get("top_k_dense"), 30, "top_k_dense", 1),
                top_k_sparse=_int_at_least(data.get("top_k_sparse"), 30, "top_k_sparse", 1),
                top_m_final=_int_at_least(data.get("top_m_final"), 5, "top_m_final", 1),
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(kb)

    @app.route("/api/knowledge/bases/<kb_id>", methods=["GET"])
    async def get_knowledge_base(kb_id: str):
        manager = _knowledge_manager(dashboard)
        try:
            return jsonify(await manager.get_knowledge_base(kb_id))
        except ValueError as e:
            return jsonify({"error": str(e)}), 404

    @app.route("/api/knowledge/bases/<kb_id>", methods=["PATCH"])
    async def update_knowledge_base(kb_id: str):
        manager = _knowledge_manager(dashboard)
        data = await request.get_json(silent=True) or {}
        try:
            return jsonify(await manager.update_knowledge_base(kb_id, **data))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/knowledge/bases/<kb_id>", methods=["DELETE"])
    async def delete_knowledge_base(kb_id: str):
        manager = _knowledge_manager(dashboard)
        deleted = await manager.delete_knowledge_base(kb_id)
        if not deleted:
            return jsonify({"error": "knowledge base not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/knowledge/bases/<kb_id>/documents", methods=["GET"])
    async def list_documents(kb_id: str):
        manager = _knowledge_manager(dashboard)
        try:
            return jsonify({"items": await manager.list_documents(kb_id)})
        except ValueError as e:
            return jsonify({"error": str(e)}), 404

    @app.route("/api/knowledge/bases/<kb_id>/documents/import", methods=["POST"])
    async def import_document(kb_id: str):
        manager = _knowledge_manager(dashboard)
        data = await request.get_json(silent=True) or {}
        try:
            task = await manager.import_document(
                kb_id,
                file_name=str(data.get("file_name") or data.get("doc_name") or "document.txt"),
                content=str(data.get("content") or ""),
                file_type=_optional_str(data.get("file_type")),
                source=str(data.get("source") or "import"),
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(task)

    @app.route("/api/knowledge/bases/<kb_id>/documents/upload", methods=["POST"])
    async def upload_document(kb_id: str):
        manager = _knowledge_manager(dashboard)
        files = await request.files
        upload = files.get("file")
        if upload is None:
            return jsonify({"error": "file is required"}), 400
        content = upload.read()
        if hasattr(content, "__await__"):
            content = await content
        try:
            task = await manager.upload_document(
                kb_id,
                file_name=upload.filename or "upload.txt",
                content=bytes(content),
            )
        except (UnicodeDecodeError, ValueError) as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(task)

    @app.route("/api/knowledge/documents/<doc_id>", methods=["DELETE"])
    async def delete_document(doc_id: str):
        manager = _knowledge_manager(dashboard)
        deleted = await manager.delete_document(doc_id)
        if not deleted:
            return jsonify({"error": "document not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/knowledge/documents/<doc_id>/chunks", methods=["GET"])
    async def list_chunks(doc_id: str):
        manager = _knowledge_manager(dashboard)
        try:
            page = _int_at_least(request.args.get("page"), 1, "page", 1)
            page_size = _int_at_least(
                request.args.get("page_size"), 100, "page_size", 1, maximum=500
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        offset = max(0, page - 1) * page_size
        return jsonify(
            {
                "items": await manager.list_chunks(doc_id, offset=offset, limit=page_size),
                "page": page,
                "page_size": page_size,
            }
        )

    @app.route("/api/knowledge/chunks/<chunk_id>", methods=["DELETE"])
    async def delete_chunk(chunk_id: str):
        manager = _knowledge_manager(dashboard)
        deleted = await manager.delete_chunk(chunk_id)
        if not deleted:
            return jsonify({"error": "chunk not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/knowledge/retrieve", methods=["POST"])
    async def retrieve():
        manager = _knowledge_manager(dashboard)
        data = await request.get_json(silent=True) or {}
        try:
            result = await manager.retrieve(
                query=str(data.get("query") or ""),
                kb_ids=_str_list(data.get("kb_ids")),
                kb_names=_str_list(data.get("kb_names")),
                top_k=_int_at_least(data.get("top_k"), 5, "top_k", 1),
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(result)

    @app.route("/api/knowledge/tasks/<task_id>", methods=["GET"])
    async def get_task(task_id: str):
        manager = _knowledge_manager(dashboard)
        try:
            return jsonify(await manager.get_task(task_id))
        except ValueError as e:
            return jsonify({"error": str(e)}), 404


def _knowledge_manager(dashboard: Dashboard) -> KnowledgeBaseManager:
    manager = getattr(dashboard.lifecycle, "knowledge_manager", None)
    if manager is None:
        raise RuntimeError("knowledge manager is not available")
    return cast("KnowledgeBaseManager", manager)


def _optional_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_at_least(
    value: object,
    default: int,
    field: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if value is None:
        parsed = default
    else:
        try:
            parsed = int(cast(Any, value))
        except (TypeError, ValueError) as e:
            raise ValueError(f"{field} must be an integer") from e
    if parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field} must be <= {maximum}")
    return parsed


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]
