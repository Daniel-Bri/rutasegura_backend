from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional


class TecnicoCreate(BaseModel):
    nombre: str
    especialidad: str
    telefono: Optional[str] = None
    email: Optional[str] = None  # email del usuario con rol "tecnico" para vincular

    @field_validator("nombre")
    @classmethod
    def nombre_valido(cls, v: str) -> str:
        if len(v.strip()) < 2:
            raise ValueError("El nombre debe tener al menos 2 caracteres")
        return v.strip()

    @field_validator("especialidad")
    @classmethod
    def especialidad_valida(cls, v: str) -> str:
        if len(v.strip()) < 2:
            raise ValueError("La especialidad debe tener al menos 2 caracteres")
        return v.strip()


class TecnicoUpdate(BaseModel):
    nombre: Optional[str] = None
    especialidad: Optional[str] = None
    telefono: Optional[str] = None
    estado: Optional[str] = None
    email: Optional[str] = None  # para vincular/cambiar usuario


class TecnicoResponse(BaseModel):
    id: int
    usuario_id: Optional[int]
    taller_id: int
    nombre: str
    especialidad: str
    telefono: Optional[str]
    estado: str
    activo: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AsignacionResponse(BaseModel):
    id: int
    incidente_id: int
    taller_id: int
    tecnico_id: Optional[int]
    estado: str
    eta: Optional[int]
    observacion: Optional[str]
    created_at: datetime
    es_sos: bool = False  # Poblado solo en /asignaciones/activas

    model_config = {"from_attributes": True}


class AsignarTecnicoPayload(BaseModel):
    tecnico_id: int


# ── CU15 · Estado del servicio ─────────────────────────────
# Transiciones que puede hacer el taller/técnico desde CU15.
# en_sitio es preferiblemente confirmado por el cliente (CU31), pero el taller
# puede hacerlo como fallback para casos SOS o cuando el cliente no tiene señal.
TRANSICIONES_VALIDAS: dict[str, set[str]] = {
    "invitado":      {"cancelado"},   # solo puede cancelarse si el cliente no eligió
    "aceptado":      {"en_camino", "cancelado"},
    "en_camino":     {"en_sitio",  "cancelado"},
    "en_sitio":      {"en_reparacion"},
    "en_reparacion": {"finalizado"},
}


class CobroVisitaCreate(BaseModel):
    monto: float
    concepto: Optional[str] = None

    @field_validator("monto")
    @classmethod
    def monto_positivo(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("El monto debe ser mayor a 0")
        return round(v, 2)


class AsignacionEstadoUpdate(BaseModel):
    estado: str
    observacion: Optional[str] = None

    @field_validator("estado")
    @classmethod
    def estado_valido(cls, v: str) -> str:
        validos = {"en_camino", "en_sitio", "en_reparacion", "finalizado", "cancelado"}
        if v not in validos:
            raise ValueError(f"Estado inválido: {v}")
        return v


# ── CU22 · Servicio Realizado ──────────────────────────────
class RepuestoItem(BaseModel):
    descripcion: str
    cantidad: int

    @field_validator("cantidad")
    @classmethod
    def cant_positiva(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("La cantidad debe ser mayor a 0")
        return v


class ServicioRealizadoCreate(BaseModel):
    asignacion_id: int
    descripcion_trabajo: str
    repuestos: Optional[list[RepuestoItem]] = None
    observaciones: Optional[str] = None

    @field_validator("descripcion_trabajo")
    @classmethod
    def desc_valida(cls, v: str) -> str:
        if len(v.strip()) < 5:
            raise ValueError("La descripción debe tener al menos 5 caracteres")
        return v.strip()


class ServicioRealizadoResponse(BaseModel):
    id: int
    asignacion_id: int
    descripcion_trabajo: str
    repuestos: Optional[str]
    observaciones: Optional[str]
    fecha_cierre: datetime

    model_config = {"from_attributes": True}


# ── CU16 · Disponibilidad ──────────────────────────────────
class DisponibilidadUpdate(BaseModel):
    disponible: bool


class TallerInfoResponse(BaseModel):
    id: int
    nombre: str
    direccion: str
    telefono: Optional[str]
    email_comercial: Optional[str]
    disponible: bool
    estado: str
    rating: float
    total_tecnicos: int
    tecnicos_disponibles: int
    tecnicos_ocupados: int

    model_config = {"from_attributes": True}
