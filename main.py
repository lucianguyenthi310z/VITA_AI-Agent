"""FastAPI backend cho OPC AI Agent.

Luồng chính:
Frontend -> FastAPI -> Supabase -> Dify -> FastAPI -> Frontend/Terminal
"""

from __future__ import annotations

import json
import hmac
import os
import secrets
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from supabase import Client, create_client

from dify_client import DifyClientError, DifyWorkflowClient, extract_outputs

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "UI"

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
_auth_token = secrets.token_urlsafe(32)
AUTH_COOKIE = "vita_session"


class DecisionPayload(BaseModel):
    decision: Literal["ACCEPT", "REQUEST_MORE_DATA", "REJECT"]
    workflow_run_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    decided_at: str | None = None
    source: str = "opc-web-dashboard"


class AnalyzePayload(BaseModel):
    supplemental_data: dict[str, Any] = Field(default_factory=dict)
    skip_missing_data: bool = False


class FounderDecisionPayload(BaseModel):
    founder_decision: Literal["approve", "request_more_info", "reject"]
    external_send_confirmation: Literal["confirm", "cancel"] | None = None


class LoginPayload(BaseModel):
    username: str
    password: str


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(AUTH_COOKIE, "")
    return bool(token) and hmac.compare_digest(token, _auth_token)


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    public_paths = {"/", "/health", "/api/auth/login"}
    public_prefixes = ("/UI/login",)
    protected = path == "/dashboard" or path.startswith("/api/")
    if protected and path not in public_paths and not path.startswith(public_prefixes):
        if not is_authenticated(request):
            if path.startswith("/api/"):
                return JSONResponse(status_code=401, content={"detail": "Phiên đăng nhập không hợp lệ."})
            return RedirectResponse(url="/", status_code=303)
    return await call_next(request)


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


def fetch_related_data(contract_id: str, customer_id: str | None = None) -> tuple[dict[str, list[Any]], list[str]]:
    """Đọc các bảng liên quan nhưng không làm hỏng toàn bộ request nếu một bảng chưa có."""

    table_names = [
        name.strip()
        for name in os.getenv("SUPABASE_RELATED_TABLES", "orders,invoices,bank_txn,cashflow").split(",")
        if name.strip()
    ]
    
    # BẮT BUỘC thêm bảng customers để đảm bảo giao diện luôn có dữ liệu tĩnh
    if "customers" not in table_names:
        table_names.append("customers")

    related: dict[str, list[Any]] = {}
    warnings: list[str] = []

    for table_name in table_names:
        try:
            query = get_supabase_client().table(table_name).select("*")
            
            if table_name == "customers":
                if customer_id:
                    response = query.eq("customer_id", customer_id).execute()
                else:
                    continue  # Bỏ qua nếu hợp đồng không có customer_id
            else:
                response = query.eq(contract_id_column(), contract_id).execute()
                
            related[table_name] = response.data or []
        except Exception as exc:  # noqa: BLE001 - cần tiếp tục các bảng còn lại
            related[table_name] = []
            warnings.append(f"Không đọc được bảng {table_name}: {exc}")

    return related, warnings


def build_case_data(contract_id: str, contract: dict[str, Any]) -> dict[str, Any]:
    customer_id = contract.get("customer_id")
    related_data, warnings = fetch_related_data(contract_id, customer_id)
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
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/dashboard", status_code=303)
    return FileResponse(UI_DIR / "login.html")


@app.get("/dashboard", include_in_schema=False)
def frontend_page() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.get("/UI/login.css", include_in_schema=False)
def login_css() -> FileResponse:
    return FileResponse(UI_DIR / "login.css", media_type="text/css")


@app.get("/UI/login.js", include_in_schema=False)
def login_js() -> FileResponse:
    return FileResponse(UI_DIR / "login.js", media_type="application/javascript")


@app.get("/UI/style.css", include_in_schema=False)
def frontend_css() -> FileResponse:
    return FileResponse(UI_DIR / "style.css", media_type="text/css")


@app.get("/UI/frontend.js", include_in_schema=False)
def frontend_js() -> FileResponse:
    return FileResponse(
        UI_DIR / "frontend.js",
        media_type="application/javascript",
    )


# -----------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(payload: LoginPayload) -> JSONResponse:
    expected_username = os.getenv("VITA_LOGIN_USERNAME", "admin")
    expected_password = os.getenv("VITA_LOGIN_PASSWORD", "VITA")
    username_ok = hmac.compare_digest(payload.username, expected_username)
    password_ok = hmac.compare_digest(payload.password, expected_password)
    if not (username_ok and password_ok):
        raise HTTPException(status_code=401, detail="Tên đăng nhập hoặc mật khẩu không đúng.")
    response = JSONResponse({"success": True, "message": "Đăng nhập thành công."})
    response.set_cookie(
        key=AUTH_COOKIE,
        value=_auth_token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        max_age=8 * 60 * 60,
    )
    return response


@app.post("/api/auth/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"success": True})
    response.delete_cookie(AUTH_COOKIE)
    return response


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
def analyze_contract(contract_id: str, payload: AnalyzePayload | None = None) -> dict[str, Any]:
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

    # Đã sửa: Cho dù Agent lỗi, vẫn gửi dữ liệu tĩnh về giao diện
    try:
        # Giữ nguyên schema Start của Agent 1 (contract_id + case_data), dữ liệu
        # bổ sung được gói vào case_data để không tạo input Dify chưa khai báo.
        if payload and payload.supplemental_data:
            case_data["supplemental_data"] = payload.supplemental_data
        if payload and payload.skip_missing_data:
            case_data["skip_missing_data"] = True
        dify_response = DifyWorkflowClient().run_workflow(
            contract_id=contract_id,
            case_data=case_data,
        )
        outputs = extract_outputs(dify_response)

        print("\n" + "=" * 72)
        print(f"KẾT QUẢ DIFY CHO {contract_id}")
        print("=" * 72)
        print(json.dumps(outputs, ensure_ascii=False, indent=2, default=str))
        print("=" * 72 + "\n")

    except Exception as exc:  # Bắt mọi lỗi từ Dify (chưa có key, timeout, v.v.)
        print(f"⚠️ Dify Agent chưa sẵn sàng hoặc lỗi: {exc}")
        dify_response = {"data": {"status": "failed", "error": str(exc)}}
        outputs = {
            "message": "⚠️ Chưa kết nối Dify Agent. Đang hiển thị dữ liệu tĩnh.",
            "status": "partial",
            "risk_level": "UNKNOWN"
        }

    return {
        "contract": contract,
        "case_data": case_data,
        "outputs": outputs,
        "dify_response": dify_response,
    }


@app.post("/api/agent/founder-decision/{contract_id}")
def run_founder_decision(contract_id: str, payload: FounderDecisionPayload) -> dict[str, Any]:
    """Gửi quyết định của Founder sang Agent 2; API key không bao giờ đi xuống frontend."""
    contract_id = normalize_contract_id(contract_id)
    inputs: dict[str, Any] = {
        "contract_id": contract_id,
        "founder_decision": payload.founder_decision,
    }
    if payload.external_send_confirmation is not None:
        inputs["external_send_confirmation"] = payload.external_send_confirmation
    try:
        response = DifyWorkflowClient(api_key_env="DIFY_API_KEY_2").run_with_inputs(inputs=inputs)
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=f"Agent 2 lỗi: {exc}") from exc
    return {"outputs": extract_outputs(response), "dify_response": response}


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
