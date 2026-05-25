from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class CalificacionCreate(BaseModel):
    asignacion_id: int
    puntuacion: int = Field(..., ge=1, le=5)
    resena: Optional[str] = None


class CalificacionResponse(BaseModel):
    id: int
    asignacion_id: int
    cliente_id: int
    taller_id: int
    puntuacion: int
    comentario: Optional[str]
    estado: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BitacoraEventoResponse(BaseModel):
    id: int
    usuario_id: Optional[int]
    usuario_nombre: Optional[str]
    accion: str
    entidad: Optional[str]
    entidad_id: Optional[int]
    detalle: Optional[str]
    ip: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditoriaListResponse(BaseModel):
    items: list[BitacoraEventoResponse]
    total: int
    page: int
    size: int
    pages: int
