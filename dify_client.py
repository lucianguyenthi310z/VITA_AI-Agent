"""Client gọi Dify Workflow API từ backend."""

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

    def _run_streaming(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Đọc SSE của Dify và trả sự kiện workflow_finished như JSON cũ."""

        payload["response_mode"] = "streaming"

        try:
            with self.session.post(
                f"{self.api_base_url}/workflows/run",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                json=payload,
                timeout=self.timeout_seconds,
                stream=True,
            ) as response:
                if not response.ok:
                    response_text = response.text[:500]
                    try:
                        error_body = response.json()
                    except ValueError:
                        error_body = None
                    message = (
                        error_body.get("message")
                        if isinstance(error_body, dict)
                        else response_text
                    )
                    raise DifyClientError(
                        f"Dify trả lỗi HTTP {response.status_code}: {message}"
                    )

                response.encoding = response.encoding or "utf-8"
                for line in response.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"):
                        continue

                    raw_event = line[len("data:"):].strip()
                    if not raw_event:
                        continue

                    try:
                        event = json.loads(raw_event)
                    except (TypeError, ValueError) as exc:
                        raise DifyClientError(
                            "Dify trả về một sự kiện streaming không hợp lệ."
                        ) from exc

                    if not isinstance(event, dict):
                        continue

                    event_name = event.get("event")
                    if event_name == "error":
                        raise DifyClientError(
                            event.get("message") or "Dify streaming gặp lỗi"
                        )

                    if event_name == "workflow_finished":
                        data = event.get("data", {})
                        if data.get("status") == "failed":
                            raise DifyClientError(
                                data.get("error") or "Dify workflow thất bại"
                            )
                        return event

        except requests.Timeout as exc:
            raise DifyClientError(
                f"Dify không gửi dữ liệu trong {self.timeout_seconds} giây."
            ) from exc
        except requests.RequestException as exc:
            raise DifyClientError(f"Không thể kết nối tới Dify: {exc}") from exc

        raise DifyClientError(
            "Kết nối streaming Dify kết thúc nhưng không có workflow_finished."
        )

    def run_workflow(
        self,
        *,
        contract_id: str,
        case_data: dict[str, Any],
        user: str | None = None,
        extra_inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Gửi dữ liệu hợp đồng sang Dify và chờ kết quả hoàn chỉnh."""

        contract_input = os.getenv("DIFY_CONTRACT_ID_INPUT", "contract_id").strip()
        case_input = os.getenv("DIFY_CASE_DATA_INPUT", "case_data").strip()
        inputs: dict[str, Any] = {
            contract_input: contract_id,
            case_input: json.dumps(case_data, ensure_ascii=False, default=str),
        }
        if extra_inputs:
            inputs.update(extra_inputs)

        return self._run_streaming({
            "inputs": inputs,
            "user": user or os.getenv("DIFY_USER", "opc-dashboard-user"),
        })

    def run_with_inputs(
        self,
        *,
        inputs: dict[str, Any],
        user: str | None = None,
    ) -> dict[str, Any]:
        """Chạy workflow với bộ input đã chuẩn hóa (dùng cho Agent 2)."""

        return self._run_streaming({
            "inputs": inputs,
            "user": user or os.getenv("DIFY_USER", "opc-dashboard-user"),
        })


def extract_outputs(dify_response: dict[str, Any]) -> dict[str, Any]:
    """Lấy data.outputs từ response đầy đủ của Dify."""

    outputs = dify_response.get("data", {}).get("outputs", {})
    return outputs if isinstance(outputs, dict) else {"result": outputs}
