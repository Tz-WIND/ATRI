"""Audio file streaming helpers for the music routes."""

from __future__ import annotations

import mimetypes
import os
import re

from quart import Response


def stream_audio_response(filepath: str, range_header: str | None) -> Response:
    mime = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    file_size = os.path.getsize(filepath)

    if range_header:
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1

            def generate():
                with open(filepath, "rb") as file:
                    file.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk_size = min(65536, remaining)
                        data = file.read(chunk_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            return Response(
                generate(),
                status=206,
                content_type=mime,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(length),
                    "Cache-Control": "public, max-age=86400",
                },
            )

    def generate_full():
        with open(filepath, "rb") as file:
            while True:
                data = file.read(65536)
                if not data:
                    break
                yield data

    return Response(
        generate_full(),
        content_type=mime,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Cache-Control": "public, max-age=86400",
        },
    )
