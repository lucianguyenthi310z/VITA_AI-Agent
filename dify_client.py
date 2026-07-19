"""Client gọi Dify Workflow API từ backend.

API key chỉ được lưu trong file .env của backend, tuyệt đối không đưa vào
frontend.html hoặc frontend.js.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


class DifyClientError(RuntimeError):
    """Lỗi kết nối hoặc lỗi phản hồi từ Dify."""


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("YOUR_") or "YOUR_" in value:
        raise DifyClientError(f"Thiếu hoặc chưa cấu hình biến {name} trong file .env")
    return value


class DifyWorkflowClient:
    """Chạy một Workflow app đã Publish trên Dify."""

    def __init__(
        self,
        api_base_url: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "DIFY_API_KEY",
        timeout_seconds: int | None = None,
    ) -> None:
        self.api_base_url = (
            api_base_url
            or os.getenv("DIFY_API_BASE_URL", "https://api.dify.ai/v1")
        ).rstrip("/")
        self.api_key = api_key or _required_env(api_key_env)
        self.timeout_seconds = timeout_seconds or int(
            os.getenv("DIFY_TIMEOUT_SECONDS", "120")
        )
        self.session = requests.Session()

    def run_workflow(
        self,
        *,
        contract_id: str,
        case_data: dict[str, Any],
        user: str | None = None,
        extra_inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Gửi dữ liệu hợp đồng sang Dify và chờ kết quả hoàn chỉnh."""

        contract_input = os.getenv(
            "DIFY_CONTRACT_ID_INPUT", "contract_id"
        ).strip()
        case_input = os.getenv("DIFY_CASE_DATA_INPUT", "case_data").strip()

        # Node Start của Dify nên khai báo case_data là Text/Paragraph.
        # Vì vậy dictionary Python được chuyển thành chuỗi JSON Unicode.
        inputs: dict[str, Any] = {
            contract_input: contract_id,
            case_input: json.dumps(
                case_data,
                ensure_ascii=False,
                default=str,
            ),
        }

        if extra_inputs:
            inputs.update(extra_inputs)

        payload = {
            "inputs": inputs,
            "response_mode": "blocking",
            "user": user or os.getenv("DIFY_USER", "opc-dashboard-user"),
        }

        try:
            response = self.session.post(
                f"{self.api_base_url}/workflows/run",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise DifyClientError(
                f"Dify không phản hồi sau {self.timeout_seconds} giây."
            ) from exc
        except requests.RequestException as exc:
            raise DifyClientError(f"Không thể kết nối tới Dify: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise DifyClientError(
                f"Dify trả dữ liệu không phải JSON (HTTP {response.status_code}): "
                f"{response.text[:500]}"
            ) from exc

        if not response.ok:
            message = body.get("message") if isinstance(body, dict) else str(body)
            raise DifyClientError(
                f"Dify trả lỗi HTTP {response.status_code}: {message or body}"
            )

        workflow_status = body.get("data", {}).get("status")
        if workflow_status == "failed":
            error = body.get("data", {}).get("error") or "Workflow thất bại"
            raise DifyClientError(f"Dify workflow failed: {error}")

        return body

    def run_with_inputs(
        self,
        *,
        inputs: dict[str, Any],
        user: str | None = None,
    ) -> dict[str, Any]:
        """Chạy workflow với bộ input đã chuẩn hóa (dùng cho Agent 2)."""
        payload = {
            "inputs": inputs,
            "response_mode": "blocking",
            "user": user or os.getenv("DIFY_USER", "opc-dashboard-user"),
        }
        try:
            response = self.session.post(
                f"{self.api_base_url}/workflows/run",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise DifyClientError(
                f"Dify không phản hồi sau {self.timeout_seconds} giây."
            ) from exc
        except requests.RequestException as exc:
            raise DifyClientError(f"Không thể kết nối tới Dify: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise DifyClientError(
                f"Dify trả dữ liệu không phải JSON (HTTP {response.status_code})."
            ) from exc
        if not response.ok:
            message = body.get("message") if isinstance(body, dict) else str(body)
            raise DifyClientError(f"Dify trả lỗi HTTP {response.status_code}: {message or body}")
        if body.get("data", {}).get("status") == "failed":
            raise DifyClientError(body.get("data", {}).get("error") or "Dify workflow failed")
        return body


def extract_outputs(dify_response: dict[str, Any]) -> dict[str, Any]:
    """Lấy data.outputs từ response đầy đủ của Dify."""

    outputs = dify_response.get("data", {}).get("outputs", {})
    return outputs if isinstance(outputs, dict) else {"result": outputs}
