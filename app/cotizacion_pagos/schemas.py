from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional


class ItemCotizacion(BaseModel):
    descripcion: str
    cantidad: int
    precio_unitario: float

    @field_validator("cantidad")
    @classmethod
    def cantidad_positiva(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("La cantidad debe ser mayor a 0")
        return v

    @field_validator("precio_unitario")
    @classmethod
    def precio_positivo(cls, v: float) -> float:
        if v < 0:
            raise ValueError("El precio no puede ser negativo")
        return v


class CotizacionCreate(BaseModel):
    incidente_id: int
    items: list[ItemCotizacion]

    @field_validator("items")
    @classmethod
    def items_no_vacios(cls, v: list) -> list:
        if not v:
            raise ValueError("Debes agregar al menos un ítem a la cotización")
        return v


class CotizacionEstadoUpdate(BaseModel):
    estado: str   # aceptada | rechazada

    @field_validator("estado")
    @classmethod
    def estado_valido(cls, v: str) -> str:
        if v not in ("aceptada", "rechazada"):
            raise ValueError("Estado debe ser 'aceptada' o 'rechazada'")
        return v


class IncidenteDisponibleResponse(BaseModel):
    asignacion_id: int
    incidente_id: int
    estado_asignacion: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CotizacionResponse(BaseModel):
    id: int
    incidente_id: int
    taller_id: int
    monto_estimado: float
    detalle: Optional[str]
    estado: str
    estado_asignacion: Optional[str] = None  # poblado en mis_cotizaciones
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Pago ───────────────────────────────────────────────────

class PagoCreate(BaseModel):
    cotizacion_id: int
    metodo: str

    @field_validator("metodo")
    @classmethod
    def metodo_valido(cls, v: str) -> str:
        if v not in ("efectivo", "qr", "tarjeta"):
            raise ValueError("Método debe ser 'efectivo', 'qr' o 'tarjeta'")
        return v


class PagoResponse(BaseModel):
    id: int
    cotizacion_id: int
    monto: float
    metodo: str
    estado: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Comisiones ─────────────────────────────────────────────

class ComisionItem(BaseModel):
    pago_id: int
    cotizacion_id: int
    incidente_id: int
    monto_bruto: float
    comision: float
    monto_neto: float
    metodo: str
    fecha: datetime


class ComisionesResponse(BaseModel):
    taller_id: int
    total_servicios: int
    ingresos_brutos: float
    tasa_comision: float
    comision_plataforma: float
    ingresos_netos: float
    pagos: list[ComisionItem]


# ── Stripe (CU40) ──────────────────────────────────────────

class StripeIntentRequest(BaseModel):
    cotizacion_id: int


class StripeIntentResponse(BaseModel):
    client_secret: str
    payment_intent_id: str
    monto: float
    currency: str = "usd"


class StripeConfirmarRequest(BaseModel):
    cotizacion_id: int
    payment_intent_id: str
