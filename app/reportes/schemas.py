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


class CalificacionCreate(BaseModel):
    asignacion_id: int
    puntuacion: int
    resena: Optional[str] = None


class CalificacionResponse(BaseModel):
    id: int
    asignacion_id: int
    cliente_id: int
    taller_id: int
    puntuacion: int
    resena: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class CalificacionPendienteItem(BaseModel):
    asignacion_id: int
    incidente_id: int
    taller_id: int
    taller_nombre: Optional[str]
    fecha_finalizacion: Optional[datetime]


class MetricasResumenResponse(BaseModel):
    desde: Optional[datetime]
    hasta: Optional[datetime]
    total_servicios: int
    servicios_finalizados: int
    servicios_pagados: int
    ingresos_brutos: float
    comision_plataforma: float
    ingresos_netos: float
    ticket_promedio: float
    promedio_calificacion: Optional[float]
    total_calificaciones: int
    detalle_pagos: list[dict]
