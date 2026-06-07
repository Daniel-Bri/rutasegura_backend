from typing import Any, Optional

from fastapi import APIRouter, Depends, File, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.dependencies import get_current_user
from app.acceso_registro.models import User
from app.emergencias import schemas, service
from app.emergencias.schemas import IncidenteResponse, UbicacionUpdate, DescripcionUpdate

router = APIRouter()


class SOSCreate(BaseModel):
    latitud: Optional[float] = None
    longitud: Optional[float] = None


# ── CU05 – Reportar emergencia ────────────────────────────────────────────
@router.post("/", response_model=IncidenteResponse, status_code=status.HTTP_201_CREATED)
async def reportar_emergencia(
    data: schemas.IncidenteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incidente = await service.crear_incidente(data, current_user.id, db)
    from app.reportes.service import log_evento
    await log_evento(db, accion="crear_incidente", usuario_id=current_user.id,
                     usuario_nombre=getattr(current_user, 'username', None), entidad="incidente",
                     entidad_id=incidente.id,
                     detalle={"prioridad": data.prioridad, "vehiculo_id": data.vehiculo_id})
    return IncidenteResponse.model_validate(incidente)


# ── CU10 – Mis solicitudes (incidente + asignación + fotos) ──────────────
@router.get("/mis-solicitudes", response_model=list[dict[str, Any]])
async def listar_mis_solicitudes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.listar_mis_solicitudes(current_user.id, db)


# ── Listar mis incidentes ─────────────────────────────────────────────────
@router.get("/mis-incidentes", response_model=list[IncidenteResponse])
async def listar_mis_incidentes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incidentes = await service.listar_incidentes_usuario(current_user.id, db)
    return [IncidenteResponse.model_validate(i) for i in incidentes]


# ── CU06 – Enviar ubicación GPS ───────────────────────────────────────────
@router.patch("/{incidente_id}/ubicacion", response_model=IncidenteResponse)
async def enviar_ubicacion(
    incidente_id: int,
    data: UbicacionUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incidente = await service.actualizar_ubicacion(incidente_id, current_user.id, data, db)
    return IncidenteResponse.model_validate(incidente)


# ── CU07 – Adjuntar fotos (§4.4 + §4.5 análisis IA) ─────────────────────
@router.post("/{incidente_id}/fotos", status_code=status.HTTP_201_CREATED)
async def adjuntar_fotos(
    incidente_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contenido = await file.read()
    return await service.guardar_foto(
        incidente_id, current_user.id, contenido, file.filename or "foto.jpg", db
    )


# ── CU08 – Enviar audio (§4.5 transcripción + clasificación IA) ──────────
@router.post("/{incidente_id}/audio", status_code=status.HTTP_201_CREATED)
async def enviar_audio(
    incidente_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contenido = await file.read()
    return await service.guardar_audio(
        incidente_id, current_user.id, contenido, file.filename or "audio.wav", db
    )


# ── CU09 – Agregar / actualizar descripción + re-clasificación IA ────────
@router.patch("/{incidente_id}/descripcion", response_model=IncidenteResponse)
async def agregar_descripcion(
    incidente_id: int,
    data: DescripcionUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incidente = await service.actualizar_descripcion(
        incidente_id, current_user.id, data.descripcion, db
    )
    return IncidenteResponse.model_validate(incidente)


# ── CU30 – Botón SOS ──────────────────────────────────────────────────────
@router.post("/sos", response_model=IncidenteResponse, status_code=status.HTTP_201_CREATED)
async def boton_sos(
    data: SOSCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incidente = await service.crear_incidente_sos(
        current_user.id, data.latitud, data.longitud, db
    )
    from app.reportes.service import log_evento
    await log_evento(db, accion="boton_sos", usuario_id=current_user.id,
                     usuario_nombre=getattr(current_user, 'username', None), entidad="incidente",
                     entidad_id=incidente.id,
                     detalle={"latitud": data.latitud, "longitud": data.longitud})
    return IncidenteResponse.model_validate(incidente)
