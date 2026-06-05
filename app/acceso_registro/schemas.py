from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional
import re


# ── Usuario ────────────────────────────────────────────────
class UserCreate(BaseModel):
    email: str
    username: str
    full_name: Optional[str] = None
    telefono: Optional[str] = None
    password: str

    @field_validator("email")
    @classmethod
    def email_valido(cls, v: str) -> str:
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Correo electrónico inválido")
        return v.lower()

    @field_validator("password")
    @classmethod
    def password_seguro(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("La contraseña debe tener al menos 6 caracteres")
        return v

    @field_validator("username")
    @classmethod
    def username_valido(cls, v: str) -> str:
        if len(v) < 3:
            raise ValueError("El username debe tener al menos 3 caracteres")
        if not re.match(r"^[a-zA-Z0-9_]+$", v):
            raise ValueError("El username solo puede contener letras, números y _")
        return v


class UserLogin(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    username: str
    full_name: Optional[str]
    telefono: Optional[str]
    is_active: bool
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def new_password_seguro(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("La contraseña debe tener al menos 6 caracteres")
        return v


class RequestResetRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def new_password_seguro(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("La contraseña debe tener al menos 6 caracteres")
        return v


# ── Vehículo ───────────────────────────────────────────────
class VehiculoCreate(BaseModel):
    placa: str
    marca: str
    modelo: str
    anio: int
    color: str
    numero_seguro: Optional[str] = None

    @field_validator("placa")
    @classmethod
    def placa_valida(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) < 5:
            raise ValueError("La placa debe tener al menos 5 caracteres")
        return v

    @field_validator("anio")
    @classmethod
    def anio_valido(cls, v: int) -> int:
        if v < 1900 or v > 2100:
            raise ValueError("Año inválido")
        return v


class VehiculoResponse(BaseModel):
    id: int
    usuario_id: int
    placa: str
    marca: str
    modelo: str
    anio: int
    color: str
    numero_seguro: Optional[str] = None
    activo: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Taller ─────────────────────────────────────────────────
ESPECIALIDADES_VALIDAS = [
    "mecanica_general", "electromecanica", "chaperia",
    "llanteria", "electricista", "pintura",
]


class TallerCreate(BaseModel):
    nombre: str
    direccion: str
    telefono: Optional[str] = None
    email_comercial: Optional[str] = None
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    especialidades: Optional[list[str]] = None

    @field_validator("especialidades")
    @classmethod
    def especialidades_validas(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        invalidas = [e for e in v if e not in ESPECIALIDADES_VALIDAS]
        if invalidas:
            raise ValueError(f"Especialidades inválidas: {invalidas}. Válidas: {ESPECIALIDADES_VALIDAS}")
        return v

    @field_validator("nombre")
    @classmethod
    def nombre_valido(cls, v: str) -> str:
        if len(v.strip()) < 3:
            raise ValueError("El nombre debe tener al menos 3 caracteres")
        return v.strip()


class TallerUpdate(BaseModel):
    nombre:          Optional[str]        = None
    direccion:       Optional[str]        = None
    telefono:        Optional[str]        = None
    email_comercial: Optional[str]        = None
    latitud:         Optional[float]      = None
    longitud:        Optional[float]      = None
    especialidades:  Optional[list[str]]  = None

    @field_validator("especialidades")
    @classmethod
    def especialidades_validas(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        invalidas = [e for e in v if e not in ESPECIALIDADES_VALIDAS]
        if invalidas:
            raise ValueError(f"Especialidades inválidas: {invalidas}")
        return v


class TallerResponse(BaseModel):
    id: int
    usuario_id: int
    nombre: str
    direccion: str
    telefono: Optional[str]
    email_comercial: Optional[str]
    latitud: Optional[float]
    longitud: Optional[float]
    disponible: bool
    estado: str
    rating: float
    especialidades: Optional[str] = None   # JSON string
    created_at: datetime

    model_config = {"from_attributes": True}


# ── CU27 - Gestionar usuarios ──────────────────────────────
class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None

    @field_validator("email")
    @classmethod
    def email_valido(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Correo electrónico inválido")
        return v.lower()

    @field_validator("role")
    @classmethod
    def role_valido(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ("cliente", "taller", "tecnico", "admin"):
            raise ValueError("Rol inválido")
        return v


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    page: int
    size: int
    pages: int


# ── CU42 - Tenants ────────────────────────────────────────
class TenantCreate(BaseModel):
    nombre: str
    slug: str
    descripcion: Optional[str] = None
    config: Optional[str] = None

    @field_validator("slug")
    @classmethod
    def slug_valido(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(r"^[a-z0-9_-]+$", v):
            raise ValueError("El slug solo puede contener letras minúsculas, números, - y _")
        if len(v) < 2:
            raise ValueError("El slug debe tener al menos 2 caracteres")
        return v

    @field_validator("nombre")
    @classmethod
    def nombre_valido(cls, v: str) -> str:
        if len(v.strip()) < 2:
            raise ValueError("El nombre debe tener al menos 2 caracteres")
        return v.strip()


class TenantUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    activo: Optional[bool] = None
    config: Optional[str] = None


class TenantResponse(BaseModel):
    id: int
    nombre: str
    slug: str
    descripcion: Optional[str]
    activo: bool
    config: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class TenantDetalle(TenantResponse):
    usuarios: list[UserResponse] = []
    talleres: list["TallerResponse"] = []


class AsignarUsuarioRequest(BaseModel):
    usuario_id: int


class AsignarTallerRequest(BaseModel):
    taller_id: int


# ── CU32 - Recordatorios de mantenimiento ─────────────────
class RecordatorioMantenimiento(BaseModel):
    vehiculo_id: int
    placa: str
    marca: str
    modelo: str
    anio: int
    dias_desde_ultimo_servicio: Optional[int]
    ultimo_servicio: Optional[datetime]
    mensaje: str
    urgencia: str  # alta | media | sin_historial
