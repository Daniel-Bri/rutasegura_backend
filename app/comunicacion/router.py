from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.acceso_registro.models import User
from app.comunicacion import schemas, service
from app.comunicacion.models import Notificacion
from app.core.dependencies import get_current_user, require_role
from app.db.session import get_db

router = APIRouter()


# ── CU17 · Ver técnico en mapa ────────────────────────────────

# Técnico: envía su posición GPS cada ~5 s
@router.patch("/tecnicos/mi-ubicacion", status_code=status.HTTP_200_OK)
async def actualizar_mi_ubicacion(
    data: schemas.UbicacionTecnicoUpdate,
    current_user: User = Depends(require_role("tecnico")),
    db: AsyncSession = Depends(get_db),
):
    return await service.actualizar_ubicacion_tecnico(current_user.id, data, db)


# Cliente: consulta la posición actual del técnico asignado a su incidente
@router.get(
    "/asignaciones/{asignacion_id}/tecnico-ubicacion",
    response_model=schemas.UbicacionTecnicoResponse,
)
async def tecnico_ubicacion(
    asignacion_id: int,
    current_user: User = Depends(require_role("cliente")),
    db: AsyncSession = Depends(get_db),
):
    return await service.obtener_ubicacion_tecnico(asignacion_id, current_user.id, db)


# ── CU18 · Chat en tiempo real ────────────────────────────────

@router.post(
    "/mensajes",
    response_model=schemas.MensajeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def enviar_mensaje(
    data: schemas.MensajeCreate,
    current_user: User = Depends(require_role("taller", "cliente", "tecnico")),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select as _sel
    msg = await service.enviar_mensaje(current_user.id, current_user.role, data, db)

    try:
        from app.notificaciones.service import notificar_usuario
        from app.talleres_tecnicos.models import Asignacion as _Asig
        from app.emergencias.models import Incidente as _Inc
        from app.acceso_registro.models import Taller as _Taller

        asig_r = await db.execute(_sel(_Asig).where(_Asig.id == data.asignacion_id))
        asig = asig_r.scalar_one_or_none()
        if asig:
            remitente = msg.remitente or "Nuevo mensaje"
            preview   = data.contenido[:60] + ("…" if len(data.contenido) > 60 else "")

            if current_user.role == "cliente":
                # Notificar al taller
                t_r = await db.execute(_sel(_Taller.usuario_id).where(_Taller.id == asig.taller_id))
                t_row = t_r.first()
                if t_row:
                    await notificar_usuario(
                        t_row[0], f"💬 {remitente}", preview, db,
                        {"tipo": "mensaje", "asignacion_id": str(asig.id)},
                    )
            else:
                # Notificar al cliente
                i_r = await db.execute(_sel(_Inc.usuario_id).where(_Inc.id == asig.incidente_id))
                i_row = i_r.first()
                if i_row:
                    await notificar_usuario(
                        i_row[0], f"💬 {remitente}", preview, db,
                        {"tipo": "mensaje", "asignacion_id": str(asig.id)},
                    )
    except Exception:
        pass

    return msg


@router.get(
    "/asignaciones/{asignacion_id}/mensajes",
    response_model=list[schemas.MensajeResponse],
)
async def listar_mensajes(
    asignacion_id: int,
    current_user: User = Depends(require_role("taller", "cliente", "tecnico")),
    db: AsyncSession = Depends(get_db),
):
    return await service.listar_mensajes(asignacion_id, current_user.id, current_user.role, db)


# ── CU19 · Listar notificaciones ──────────────────────────────
@router.get("/notificaciones")
async def listar_notificaciones(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notificacion)
        .where(Notificacion.usuario_id == current_user.id)
        .order_by(Notificacion.created_at.desc())
        .limit(50)
    )
    notificaciones = result.scalars().all()
    return [
        {
            "id": n.id,
            "titulo": n.titulo,
            "mensaje": n.mensaje,
            "tipo": n.tipo,
            "leida": n.leida,
            "referencia_id": n.referencia_id,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notificaciones
    ]


@router.patch("/notificaciones/{notificacion_id}/leer")
async def marcar_leida(
    notificacion_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notificacion).where(
            Notificacion.id == notificacion_id,
            Notificacion.usuario_id == current_user.id,
        )
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notificación no encontrada")
    notif.leida = True
    await db.commit()
    return {"ok": True}


@router.patch("/notificaciones/leer-todas")
async def marcar_todas_leidas(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(Notificacion)
        .where(Notificacion.usuario_id == current_user.id, Notificacion.leida.is_(False))
        .values(leida=True)
    )
    await db.commit()
    return {"ok": True}
