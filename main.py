"""FastAPI backend cho OPC AI Agent.

Luồng chính:
Frontend -> FastAPI -> Supabase -> Dify -> FastAPI -> Frontend/Terminal
"""

from __future__ import annotations

import hashlib
import json
import hmac
import os
import re
import secrets
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from supabase import Client, create_client

BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "UI"

# Ưu tiên cấu hình bí mật cục bộ, sau đó mới nạp cấu hình chung.
# Biến môi trường của hệ điều hành vẫn có độ ưu tiên cao nhất.
load_dotenv(BASE_DIR / ".env.local")
load_dotenv(BASE_DIR / ".env")

from dify_client import DifyClientError, DifyWorkflowClient, extract_outputs

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
_supabase_write: Client | None = None
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


class NewCustomerPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    customer_name: str = Field(min_length=1, max_length=250)
    customer_type: Literal["SME", "Cooperative", "Household"]
    province: str = Field(min_length=1, max_length=150)
    payment_reliability: float | None = Field(default=None, ge=0, le=1)
    strategic_value: Literal["Low", "Medium", "High"] | None = None
    industry: str | None = Field(default=None, max_length=150)
    revenue_model: str | None = Field(default=None, max_length=150)


class NewContractDetails(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    # Các cột tiền trong Supabase là bigint, phải giữ kiểu số nguyên.
    contract_value: int = Field(gt=0)
    gross_margin: float | None = Field(default=None, ge=0, le=1)
    start_date: date
    end_date: date
    status: Literal["Active", "Pending expansion", "Negotiation"] = "Negotiation"
    payment_terms: Literal[
        "Monthly payment",
        "Milestone payment",
        "Performance bond required",
    ]
    description: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_date_range(self) -> "NewContractDetails":
        if self.end_date < self.start_date:
            raise ValueError("end_date không được trước start_date")
        return self


class NewOrderDetails(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    order_revenue: int = Field(ge=0)
    estimated_cost: int = Field(ge=0)
    due_date: date
    service_id: str | None = Field(default=None, max_length=50)
    delivery_note: str | None = Field(default=None, max_length=1000)

    @field_validator("service_id")
    @classmethod
    def normalize_service_id(cls, value: str | None) -> str | None:
        return value.strip().upper() if value and value.strip() else None


class CreateContractPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    customer_id: str | None = Field(default=None, max_length=50)
    new_customer: NewCustomerPayload | None = None
    contract: NewContractDetails
    order: NewOrderDetails

    @field_validator("customer_id")
    @classmethod
    def normalize_customer_id(cls, value: str | None) -> str | None:
        normalized = value.strip().upper() if value and value.strip() else None
        if normalized and not re.fullmatch(r"CUS-[A-Z0-9-]+", normalized):
            raise ValueError("customer_id phải có dạng CUS-...")
        return normalized

    @model_validator(mode="after")
    def validate_customer_choice(self) -> "CreateContractPayload":
        if bool(self.customer_id) == bool(self.new_customer):
            raise ValueError("Chọn đúng một phương án: Customer có sẵn hoặc Customer mới")
        return self


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


def supabase_write_key() -> str:
    """Lấy secret key chỉ dành cho backend; publishable key không được phép ghi."""
    for name in ("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            if "your_" in value.lower() or value.endswith("..."):
                raise RuntimeError(f"{name} vẫn đang là giá trị mẫu, chưa phải key thật")
            if value.startswith("sb_publishable_"):
                raise RuntimeError(f"{name} không được dùng publishable key")
            return value
    raise RuntimeError(
        "Chưa cấu hình SUPABASE_SECRET_KEY hoặc SUPABASE_SERVICE_ROLE_KEY ở backend. "
        "SUPABASE_KEY hiện là publishable key nên bị RLS chặn INSERT."
    )


def supabase_write_is_configured() -> bool:
    try:
        supabase_write_key()
    except RuntimeError:
        return False
    return True


def get_supabase_write_client() -> Client:
    global _supabase_write
    if _supabase_write is None:
        _supabase_write = create_client(
            required_env("SUPABASE_URL"),
            supabase_write_key(),
        )
    return _supabase_write


def contract_table() -> str:
    return os.getenv("SUPABASE_CONTRACT_TABLE", "contracts").strip()


def contract_id_column() -> str:
    return os.getenv("SUPABASE_CONTRACT_ID_COLUMN", "contract_id").strip()


def credit_profile_table() -> str:
    """API table name for the 10_CREDIT_PROFILE business dataset."""
    return os.getenv("SUPABASE_CREDIT_PROFILE_TABLE", "credit_profile").strip()


def normalize_contract_id(contract_id: str) -> str:
    normalized = contract_id.strip().upper()
    if not normalized:
        raise HTTPException(status_code=400, detail="contract_id không được để trống")
    return normalized


RR002_DESCRIPTION = (
    "Dòng tiền cuối kỳ dự kiến thấp hơn mức dự trữ tiền mặt tối thiểu."
)


def parse_output_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def normalize_month_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            value = parsed
        elif value.strip():
            value = value.split(",")
        else:
            value = []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(month).strip() for month in value if str(month).strip()]


def build_rr002_assessment(outputs: dict[str, Any]) -> dict[str, Any]:
    """Chuẩn hóa kết quả RR-002 để terminal và giao diện dùng cùng một diễn giải."""
    decision = parse_output_mapping(outputs.get("decision"))
    finance = parse_output_mapping(outputs.get("finance_result"))
    summary = parse_output_mapping(decision.get("summary"))
    cashflow_summary = parse_output_mapping(finance.get("cashflow_summary"))

    candidates = (
        outputs.get("months_below_reserve"),
        decision.get("months_below_reserve"),
        summary.get("months_below_reserve"),
        finance.get("months_below_reserve"),
        cashflow_summary.get("months_below_reserve"),
    )
    months: list[str] = []
    for candidate in candidates:
        months = normalize_month_list(candidate)
        if months:
            break

    return {
        "rule_id": "RR-002",
        "violated": bool(months),
        "description": RR002_DESCRIPTION,
        "months": months,
    }


def print_rr002_assessment(assessment: dict[str, Any]) -> None:
    status = "VI PHẠM" if assessment["violated"] else "KHÔNG VI PHẠM"
    print("KẾT QUẢ RISK & COMPLIANCE")
    print(f"RR-002: {status}")
    print(f"Nội dung: {assessment['description']}")
    if assessment["months"]:
        print(f"Tháng vi phạm: {', '.join(assessment['months'])}")


SENSITIVE_LOG_FIELDS = {
    "access_token": "redaction",
    "accesstoken": "redaction",
    "api_key": "redaction",
    "dify_api_key": "redaction",
    "dify_api_key_2": "redaction",
    "supabase_key": "redaction",
    "username": "redaction",
    "user_name": "redaction",
    "login_username": "redaction",
    "ten_dang_nhap": "redaction",
    "tên đăng nhập": "redaction",
    "password": "redaction",
    "login_password": "redaction",
    "mat_khau_dang_nhap": "redaction",
    "mật khẩu đăng nhập": "redaction",
}


def mask_customer_id(value: Any) -> str:
    text = str(value).strip().upper()
    if "-" in text:
        prefix, suffix = text.split("-", 1)
        return f"{prefix}-***{suffix[-3:]}"
    return f"{text[:3]}-***{text[-3:]}"


def mask_account_id(value: Any) -> str:
    text = str(value).strip().upper()
    prefix = text.split("_", 1)[0] if "_" in text else text[:3]
    return f"{prefix}_****"


def mask_company_name(value: Any) -> str:
    def mask_word(word: str) -> str:
        core = word.rstrip(".,")
        punctuation = word[len(core):]
        if len(core) <= 3:
            return word
        return core[0] + ("*" * (len(core) - 1)) + punctuation

    return " ".join(mask_word(word) for word in str(value).strip().split())


def tokenize_audit_value(field: str, value: Any) -> str:
    secret = os.getenv("TOKENIZATION_SECRET", "").strip() or _auth_token
    namespace = {"customer_id": "CUS", "account_id": "ACC", "company_name": "ORG"}[field]
    normalized = str(value).strip().upper()
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{field}:{normalized}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:8].upper()
    return f"TOK-{namespace}-{digest}"


def bucket_contract_value(value: Any) -> str:
    try:
        raw = str(value).strip()
        # Hỗ trợ cả 4.200.000.000, 4,200,000,000 và kiểu số chuẩn từ Supabase.
        if isinstance(value, str) and (raw.count(".") > 1 or raw.count(",") > 1):
            raw = raw.replace(".", "").replace(",", "")
        else:
            raw = raw.replace(",", "")
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return "[INVALID_AMOUNT]"
    billion = amount / Decimal("1000000000")
    if billion >= 1:
        return f"{billion.quantize(Decimal('0.1'))}B VND"
    million = amount / Decimal("1000000")
    return f"{million.quantize(Decimal('1'))}M VND"


def safe_original_for_log(field: str, value: Any) -> Any:
    """Không bao giờ ghi bí mật dạng rõ, kể cả trong cột 'trước masking'."""
    if field in SENSITIVE_LOG_FIELDS:
        return None
    return value


def masked_log_value(field: str, value: Any) -> Any:
    if field == "customer_id":
        return mask_customer_id(value)
    if field == "account_id":
        return mask_account_id(value)
    if field == "company_name":
        return mask_company_name(value)
    if field == "contract_value":
        return bucket_contract_value(value)
    if field in SENSITIVE_LOG_FIELDS:
        return "[SECRET]"
    return value


def collect_masking_rows(value: Any, path: str = "payload") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).strip().lower()
            child_path = f"{path}.{key}"
            account_from_bank_transactions = (
                normalized_key == "account_id"
                and path.startswith("payload.related_data.bank_transactions")
            )
            company_from_opc_profile = (
                normalized_key == "company_name"
                and path.startswith("payload.related_data.opc_profile")
            )
            is_business_field = (
                normalized_key in {"customer_id", "contract_value"}
                or account_from_bank_transactions
                or company_from_opc_profile
            )
            if is_business_field or normalized_key in SENSITIVE_LOG_FIELDS:
                is_tokenized = normalized_key in {"customer_id", "account_id", "company_name"}
                rows.append({
                    "field": child_path,
                    "source_field": normalized_key,
                    "method": (
                        "Partial Masking + HMAC-SHA256 Tokenization" if is_tokenized
                        else "Bucketing" if normalized_key == "contract_value"
                        else "Redaction"
                    ),
                    "before": safe_original_for_log(normalized_key, child),
                    "masked": masked_log_value(normalized_key, child),
                    "tokenized": tokenize_audit_value(normalized_key, child) if is_tokenized else "N/A",
                })
            rows.extend(collect_masking_rows(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(collect_masking_rows(child, f"{path}[{index}]"))
    return rows


def collect_backend_secret_rows() -> list[dict[str, Any]]:
    """Liệt kê bí mật backend ở dạng đã redaction, không đưa giá trị vào row/log."""
    configured_secrets = {
        "backend.login_username": bool(os.getenv("VITA_LOGIN_USERNAME", "admin")),
        "backend.login_password": bool(os.getenv("VITA_LOGIN_PASSWORD", "VITA")),
        "backend.DIFY_API_KEY": bool(os.getenv("DIFY_API_KEY")),
        "backend.DIFY_API_KEY_2": bool(os.getenv("DIFY_API_KEY_2")),
        "backend.SUPABASE_KEY": bool(os.getenv("SUPABASE_KEY")),
        "backend.SUPABASE_SECRET_KEY": bool(os.getenv("SUPABASE_SECRET_KEY")),
        "backend.SUPABASE_SERVICE_ROLE_KEY": bool(os.getenv("SUPABASE_SERVICE_ROLE_KEY")),
        "backend.ACCESS_TOKEN": bool(os.getenv("ACCESS_TOKEN")),
        "backend.DIFY_ACCESS_TOKEN": bool(os.getenv("DIFY_ACCESS_TOKEN")),
        "backend.SUPABASE_ACCESS_TOKEN": bool(os.getenv("SUPABASE_ACCESS_TOKEN")),
    }
    return [
        {
            "field": field,
            "source_field": field,
            "method": "Redaction",
            "before": None,
            "masked": "[SECRET]" if is_configured else "[NOT CONFIGURED]",
            "tokenized": "N/A",
        }
        for field, is_configured in configured_secrets.items()
    ]


def print_masking_audit(contract_id: str, case_data: dict[str, Any]) -> None:
    rows = collect_masking_rows(case_data) + collect_backend_secret_rows()
    print("\n" + "=" * 72)
    print(f"MASKING AUDIT TRƯỚC KHI CHẠY AGENT 1 — {contract_id}")
    print("Lưu ý: payload gửi Agent 1 vẫn giữ nguyên, không bị mask.")
    print("=" * 72)
    if not rows:
        print("Không tìm thấy trường cần masking trong payload.")
    for row in rows:
        print(f"Field  : {row['field']}")
        print(f"Method : {row['method']}")
        if row["before"] is not None:
            print(f"Before : {row['before']}")
        print(f"Masked : {row['masked']}")
        print(f"Tokenized : {row['tokenized']}")
        print("-" * 72)
    print("=" * 72 + "\n")


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
        for name in os.getenv(
            "SUPABASE_RELATED_TABLES",
            "orders,invoices,bank_transactions,opc_profile,cashflow",
        ).split(",")
        if name.strip()
    ]
    global_table_names = {
        name.strip()
        for name in os.getenv(
            "SUPABASE_GLOBAL_RELATED_TABLES",
            "bank_transactions,opc_profile",
        ).split(",")
        if name.strip()
    }
    related_table_limit = int(os.getenv("SUPABASE_RELATED_TABLE_LIMIT", "500"))
    
    # BẮT BUỘC thêm bảng customers để đảm bảo giao diện luôn có dữ liệu tĩnh
    if "customers" not in table_names:
        table_names.append("customers")

    # Bảng được đặt tên 10_CREDIT_PROFILE trong tài liệu nghiệp vụ, còn tên API
    # mặc định trên Supabase là credit_profile.
    profile_table = credit_profile_table()
    if profile_table and profile_table not in table_names:
        table_names.append(profile_table)

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
            elif table_name in global_table_names:
                # Hai bảng này không có contract_id: bank_transactions cung cấp
                # account_id, còn opc_profile cung cấp company_name cho audit.
                limit = 1 if table_name == "opc_profile" else related_table_limit
                response = query.limit(limit).execute()
            elif table_name == profile_table:
                response = query.eq(contract_id_column(), contract_id).execute()
                if not response.data:
                    # SỬA LỖI: Tìm chuỗi contract_id nằm bên trong cột collateral_or_basis 
                    # thay vì chuyển chuỗi thành CR-xxx cứng nhắc.
                    response = (
                        get_supabase_client()
                        .table(table_name)
                        .select("*")
                        .ilike("collateral_or_basis", f"%{contract_id}%")
                        .execute()
                    )
            else:
                response = query.eq(contract_id_column(), contract_id).execute()
                
            related[table_name] = response.data or []
        except Exception as exc:  # noqa: BLE001 - cần tiếp tục các bảng còn lại
            related[table_name] = []
            warnings.append(f"Không đọc được bảng {table_name}: {exc}")

    # Giữ một key ổn định cho frontend ngay cả khi tên bảng được cấu hình khác.
    related["credit_profile"] = related.get(profile_table, [])

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


def next_prefixed_id(table_name: str, column_name: str, prefix: str) -> str:
    """Sinh mã kế tiếp theo định dạng PREFIX-001 từ các mã đang có."""
    response = (
        get_supabase_client()
        .table(table_name)
        .select(column_name)
        .limit(1000)
        .execute()
    )
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$", re.IGNORECASE)
    numbers = []
    for row in response.data or []:
        match = pattern.fullmatch(str(row.get(column_name, "")).strip())
        if match:
            numbers.append(int(match.group(1)))
    return f"{prefix}-{max(numbers, default=0) + 1:03d}"


def fetch_one_by_id(table_name: str, column_name: str, value: str) -> dict[str, Any] | None:
    response = (
        get_supabase_client()
        .table(table_name)
        .select("*")
        .eq(column_name, value)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def without_none(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if value is not None}


def rollback_created_rows(rows: list[tuple[str, str, str]]) -> list[str]:
    """Dọn các dòng đã tạo nếu luồng nhiều bảng thất bại giữa chừng."""
    errors: list[str] = []
    for table_name, column_name, value in reversed(rows):
        try:
            (
                get_supabase_write_client()
                .table(table_name)
                .delete()
                .eq(column_name, value)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 - ghi nhận để trả lỗi gốc rõ ràng
            errors.append(f"{table_name}.{column_name}={value}: {exc}")
    return errors


# -----------------------------------------------------------------------------
# Phục vụ frontend cùng origin với backend
# -----------------------------------------------------------------------------
@app.get("/UI/add_contract.html", include_in_schema=False)
def add_contract_html() -> FileResponse:
    return FileResponse(UI_DIR / "add_contract.html")

@app.get("/UI/add_contract.js", include_in_schema=False)
def add_contract_js() -> FileResponse:
    return FileResponse(UI_DIR / "add_contract.js", media_type="application/javascript")

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


@app.get("/api/new-contract/options")
def new_contract_options() -> dict[str, Any]:
    """Dữ liệu dropdown và mã hợp đồng dự kiến cho form thêm mới."""
    try:
        customers_response = (
            get_supabase_client()
            .table("customers")
            .select(
                "customer_id,customer_name,customer_type,province,"
                "payment_reliability,strategic_value,industry,revenue_model"
            )
            .order("customer_id")
            .limit(500)
            .execute()
        )
        products_response = (
            get_supabase_client()
            .table("products")
            .select("service_id,service_name,pricing_model,list_price,target_margin,target_segment")
            .order("service_id")
            .limit(500)
            .execute()
        )
        return {
            "customers": customers_response.data or [],
            "products": products_response.data or [],
            "next_contract_id": next_prefixed_id(
                contract_table(), contract_id_column(), "CON"
            ),
            "next_order_id": next_prefixed_id("orders", "order_id", "ORD"),
            "database_write_ready": supabase_write_is_configured(),
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Không tải được dữ liệu cho form hợp đồng: {exc}",
        ) from exc


@app.post("/api/contracts", status_code=201)
def create_contract(payload: CreateContractPayload) -> dict[str, Any]:
    """Tạo Customer (nếu cần), Contract và Order theo schema trong form Excel."""
    try:
        client = get_supabase_write_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    created_rows: list[tuple[str, str, str]] = []

    try:
        if payload.customer_id:
            customer = fetch_one_by_id("customers", "customer_id", payload.customer_id)
            if customer is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Không tìm thấy Customer {payload.customer_id}",
                )
            customer_id = payload.customer_id
            created_new_customer = False
        else:
            customer_id = next_prefixed_id("customers", "customer_id", "CUS")
            customer = without_none({
                "customer_id": customer_id,
                **payload.new_customer.model_dump(mode="json"),
                # Cột này có trong database nhưng không thuộc form Excel.
                "banking_fit_hint": "Team analysis required",
            })
            created_new_customer = True

        if payload.order.service_id and not fetch_one_by_id(
            "products", "service_id", payload.order.service_id
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Không tìm thấy service_id {payload.order.service_id} trong bảng products",
            )

        contract_id = next_prefixed_id(
            contract_table(), contract_id_column(), "CON"
        )
        order_id = next_prefixed_id("orders", "order_id", "ORD")
        contract_record = without_none({
            contract_id_column(): contract_id,
            "customer_id": customer_id,
            **payload.contract.model_dump(mode="json"),
        })
        order_record = without_none({
            "order_id": order_id,
            "service_id": payload.order.service_id,
            "customer_id": customer_id,
            "contract_id": contract_id,
            "order_date": date.today().isoformat(),
            "due_date": payload.order.due_date.isoformat(),
            "status": "Pending approval",
            "order_revenue": payload.order.order_revenue,
            "estimated_cost": payload.order.estimated_cost,
            "delivery_note": payload.order.delivery_note,
        })

        if created_new_customer:
            client.table("customers").insert(customer).execute()
            created_rows.append(("customers", "customer_id", customer_id))

        client.table(contract_table()).insert(contract_record).execute()
        created_rows.append((contract_table(), contract_id_column(), contract_id))

        client.table("orders").insert(order_record).execute()
        created_rows.append(("orders", "order_id", order_id))

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        rollback_errors = rollback_created_rows(created_rows)
        detail = f"Không tạo được hợp đồng: {exc}"
        if rollback_errors:
            detail += f". Cần kiểm tra dữ liệu trung gian: {'; '.join(rollback_errors)}"
        status_code = 409 if "23505" in str(exc) or "duplicate" in str(exc).lower() else 500
        raise HTTPException(status_code=status_code, detail=detail) from exc

    return {
        "success": True,
        "message": f"Đã tạo hợp đồng {contract_id} và đơn hàng {order_id}",
        "data": {
            "customer": customer,
            "contract": contract_record,
            "order": order_record,
            "created_new_customer": created_new_customer,
        },
    }


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
        print_masking_audit(contract_id, case_data)
        dify_response = DifyWorkflowClient().run_workflow(
            contract_id=contract_id,
            case_data=case_data,
        )
        outputs = extract_outputs(dify_response)

        print("\n" + "=" * 72)
        print(f"KẾT QUẢ DIFY CHO {contract_id}")
        print("=" * 72)
        print(json.dumps(outputs, ensure_ascii=False, indent=2, default=str))
        rr002_assessment = build_rr002_assessment(outputs)
        print("-" * 72)
        print_rr002_assessment(rr002_assessment)
        print("=" * 72 + "\n")

    except Exception as exc:  # Bắt mọi lỗi từ Dify (chưa có key, timeout, v.v.)
        print(f"⚠️ Dify Agent chưa sẵn sàng hoặc lỗi: {exc}")
        dify_response = {"data": {"status": "failed", "error": str(exc)}}
        outputs = {
            "message": "⚠️ Chưa kết nối Dify Agent. Đang hiển thị dữ liệu tĩnh.",
            "status": "partial",
            "risk_level": "UNKNOWN"
        }
        rr002_assessment = build_rr002_assessment(outputs)

    return {
        "contract": contract,
        "case_data": case_data,
        "outputs": outputs,
        "compliance": {"rr_002": rr002_assessment},
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
