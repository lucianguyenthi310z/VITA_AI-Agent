"""FastAPI backend cho OPC AI Agent.

Luồng chính:
Frontend -> FastAPI -> Supabase -> Dify -> FastAPI -> Frontend/Terminal
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from supabase import Client, create_client

from dify_client import DifyClientError, DifyWorkflowClient, extract_outputs

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="OPC AI Agent Backend",
    version="1.0.0",
    description="Đọc dữ liệu Supabase, gọi Dify Workflow và trả kết quả cho dashboard.",
)

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_supabase: Client | None = None


class DecisionPayload(BaseModel):
    decision: Literal["ACCEPT", "REQUEST_MORE_DATA", "REJECT"]
    workflow_run_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    decided_at: str | None = None
    source: str = "opc-web-dashboard"


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or "YOUR_" in value:
        raise RuntimeError(f"Thiếu hoặc chưa cấu hình {name} trong file .env")
    return value


def get_supabase_client() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(
            required_env("SUPABASE_URL"),
            required_env("SUPABASE_KEY"),
        )
    return _supabase


def contract_table() -> str:
    return os.getenv("SUPABASE_CONTRACT_TABLE", "contracts").strip()


def contract_id_column() -> str:
    return os.getenv("SUPABASE_CONTRACT_ID_COLUMN", "contract_id").strip()


def normalize_contract_id(contract_id: str) -> str:
    normalized = contract_id.strip().upper()
    if not normalized:
        raise HTTPException(status_code=400, detail="contract_id không được để trống")
    return normalized


def fetch_contract(contract_id: str) -> dict[str, Any] | None:
    response = (
        get_supabase_client()
        .table(contract_table())
        .select("*")
        .eq(contract_id_column(), contract_id)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def fetch_related_data(contract_id: str) -> tuple[dict[str, list[Any]], list[str]]:
    """Đọc các bảng liên quan nhưng không làm hỏng toàn bộ request nếu một bảng chưa có."""

    table_names = [
        name.strip()
        for name in os.getenv("SUPABASE_RELATED_TABLES", "").split(",")
        if name.strip()
    ]

    related: dict[str, list[Any]] = {}
    warnings: list[str] = []

    for table_name in table_names:
        try:
            response = (
                get_supabase_client()
                .table(table_name)
                .select("*")
                .eq(contract_id_column(), contract_id)
                .execute()
            )
            related[table_name] = response.data or []
        except Exception as exc:  # noqa: BLE001 - cần tiếp tục các bảng còn lại
            related[table_name] = []
            warnings.append(f"Không đọc được bảng {table_name}: {exc}")

    return related, warnings


def build_case_data(contract_id: str, contract: dict[str, Any]) -> dict[str, Any]:
    related_data, warnings = fetch_related_data(contract_id)
    return {
        "contract_id": contract_id,
        "contract": contract,
        "related_data": related_data,
        "source_warnings": warnings,
    }


# -----------------------------------------------------------------------------
# Phục vụ frontend cùng origin với backend
# -----------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def frontend_page() -> FileResponse:
    return FileResponse(BASE_DIR / "frontend.html")


@app.get("/frontend.css", include_in_schema=False)
def frontend_css() -> FileResponse:
    return FileResponse(BASE_DIR / "frontend.css", media_type="text/css")


@app.get("/frontend.js", include_in_schema=False)
def frontend_js() -> FileResponse:
    return FileResponse(
        BASE_DIR / "frontend.js",
        media_type="application/javascript",
    )


# -----------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/contracts")
def list_contracts() -> dict[str, Any]:
    try:
        response = (
            get_supabase_client()
            .table(contract_table())
            .select("*")
            .order(contract_id_column())
            .limit(100)
            .execute()
        )
        return {"data": response.data or []}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Lỗi Supabase: {exc}") from exc


@app.get("/api/contracts/{contract_id}")
def get_contract(contract_id: str) -> dict[str, Any]:
    contract_id = normalize_contract_id(contract_id)
    try:
        contract = fetch_contract(contract_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Lỗi Supabase: {exc}") from exc

    if contract is None:
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy hợp đồng {contract_id}",
        )

    return {"data": contract}


@app.post("/api/agent/analyze/{contract_id}")
def analyze_contract(contract_id: str) -> dict[str, Any]:
    """Lấy hợp đồng từ Supabase, gửi sang Dify và in outputs ra Terminal."""

    contract_id = normalize_contract_id(contract_id)

    try:
        contract = fetch_contract(contract_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Lỗi Supabase: {exc}") from exc

    if contract is None:
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy hợp đồng {contract_id}",
        )

    case_data = build_case_data(contract_id, contract)

    try:
        dify_response = DifyWorkflowClient().run_workflow(
            contract_id=contract_id,
            case_data=case_data,
        )
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    outputs = extract_outputs(dify_response)

    print("\n" + "=" * 72)
    print(f"KẾT QUẢ DIFY CHO {contract_id}")
    print("=" * 72)
    print(json.dumps(outputs, ensure_ascii=False, indent=2, default=str))
    print("=" * 72 + "\n")

    return {
        "contract": contract,
        "case_data": case_data,
        "outputs": outputs,
        "dify_response": dify_response,
    }


@app.post("/api/contracts/{contract_id}/decision")
def save_decision(
    contract_id: str,
    payload: DecisionPayload,
) -> dict[str, Any]:
    """Lưu lựa chọn Chấp nhận/Thêm dữ liệu/Từ chối vào Supabase."""

    contract_id = normalize_contract_id(contract_id)
    table_name = os.getenv("SUPABASE_DECISION_TABLE", "agent_decisions").strip()

    record = {
        "contract_id": contract_id,
        "decision": payload.decision,
        "workflow_run_id": payload.workflow_run_id,
        "note": payload.note,
        "decided_at": payload.decided_at,
        "source": payload.source,
    }

    # Không gửi field None để tránh lỗi với schema không có default phù hợp.
    record = {key: value for key, value in record.items() if value is not None}

    try:
        response = (
            get_supabase_client()
            .table(table_name)
            .insert(record)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=(
                f"Không lưu được quyết định vào bảng {table_name}: {exc}. "
                "Hãy tạo bảng này hoặc đổi SUPABASE_DECISION_TABLE trong .env."
            ),
        ) from exc

    return {
        "success": True,
        "message": "Đã lưu quyết định",
        "data": response.data,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )