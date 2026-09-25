"""M2 测试公共辅助。

刻意放在单独的模块里，避免每个测试文件重复拼 multipart 与轮询逻辑。
"""

from __future__ import annotations

import io
import zipfile

import httpx

from tests.conftest import auth, login


def zip_bytes(files: dict[str, bytes]) -> bytes:
    """按 `{路径: 内容}` 构造一个 zip 的字节内容。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


async def create_tool(
    client: httpx.AsyncClient,
    token: str,
    *,
    name: str,
    tool_type: str = "file",
    summary: str = "测试用工具简介",
    description_md: str = "# 说明\n\n正文",
    visibility: str = "public",
    webapp_url: str | None = None,
    category_id: int | None = None,
    tags: list[str] | None = None,
) -> dict:
    payload: dict = {
        "name": name,
        "summary": summary,
        "description_md": description_md,
        "tool_type": tool_type,
        "visibility": visibility,
    }
    if webapp_url:
        payload["webapp_url"] = webapp_url
    if category_id is not None:
        payload["category_id"] = category_id
    if tags:
        payload["tags"] = tags
    response = await client.post("/api/v1/me/tools", json=payload, headers=auth(token))
    assert response.status_code == 201, response.text
    return response.json()


async def upload_version(
    client: httpx.AsyncClient,
    token: str,
    tool_id: int,
    *,
    version: str,
    file_bytes: bytes | None = None,
    file_name: str = "package.zip",
    changelog_md: str = "变更说明",
    prompt_content: str | None = None,
    webapp_url: str | None = None,
    auto_submit: bool = False,
) -> httpx.Response:
    """上传版本（multipart）。"""
    data: dict[str, str] = {
        "version": version,
        "changelog_md": changelog_md,
        "auto_submit": "true" if auto_submit else "false",
    }
    if prompt_content is not None:
        data["prompt_content"] = prompt_content
    if webapp_url is not None:
        data["webapp_url"] = webapp_url

    files = None
    if file_bytes is not None:
        files = {"file": (file_name, file_bytes, "application/octet-stream")}

    return await client.post(
        f"/api/v1/me/tools/{tool_id}/versions",
        data=data,
        files=files,
        headers=auth(token),
    )


async def submit(client: httpx.AsyncClient, token: str, tool_id: int) -> httpx.Response:
    """显式提交审批（`POST /me/tools/{id}/submit`）。"""
    return await client.post(
        f"/api/v1/me/tools/{tool_id}/submit", headers=auth(token)
    )


async def approve(
    client: httpx.AsyncClient,
    token: str,
    tool_id: int,
    *,
    note: str | None = None,
    version_id: int | None = None,
    expected_version_seq: int | None = None,
) -> httpx.Response:
    payload: dict = {}
    if note is not None:
        payload["note"] = note
    if version_id is not None:
        payload["version_id"] = version_id
    if expected_version_seq is not None:
        payload["expected_version_seq"] = expected_version_seq
    return await client.post(
        f"/api/v1/admin/approvals/{tool_id}/approve", json=payload, headers=auth(token)
    )


async def reject(
    client: httpx.AsyncClient, token: str, tool_id: int, *, reason: str
) -> httpx.Response:
    return await client.post(
        f"/api/v1/admin/approvals/{tool_id}/reject",
        json={"reason": reason},
        headers=auth(token),
    )


async def publish_tool(
    client: httpx.AsyncClient,
    owner_token: str,
    approver_token: str,
    *,
    name: str,
    tool_type: str = "file",
) -> tuple[str, int, bytes]:
    """一步到位：创建 → 上传 1.0.0 → 提交 → 批准。

    返回 `(slug, tool_id, v1 文件内容)`，供「新版本」类测试作为基线。
    """
    tool = await create_tool(client, owner_token, name=name, tool_type=tool_type)
    content = zip_bytes({"v1.bin": b"version-one-content"})
    created = await upload_version(
        client, owner_token, tool["id"], version="1.0.0", file_bytes=content, file_name="v1.zip"
    )
    assert created.status_code == 201, created.text
    submitted = await submit(client, owner_token, tool["id"])
    assert submitted.status_code == 200, submitted.text
    approved = await approve(client, approver_token, tool["id"])
    assert approved.status_code == 200, approved.text
    slug = (
        await client.get(f"/api/v1/me/tools/{tool['id']}", headers=auth(owner_token))
    ).json()["slug"]
    return slug, tool["id"], content


async def get_tool_id_by_slug(client: httpx.AsyncClient, token: str, slug: str) -> int:
    response = await client.get(f"/api/v1/tools/{slug}", headers=auth(token))
    assert response.status_code == 200, response.text
    return int(response.json()["id"])


__all__ = [
    "approve",
    "create_tool",
    "get_tool_id_by_slug",
    "login",
    "publish_tool",
    "reject",
    "submit",
    "upload_version",
    "zip_bytes",
]
