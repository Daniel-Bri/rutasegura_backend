import json
import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func, case, text, Numeric, cast

from app.reportes.models import BitacoraEvento


async def log_evento(
    db: AsyncSession,
    accion: str,
    usuario_id: Optional[int] = None,
    usuario_nombre: Optional[str] = None,
    entidad: Optional[str] = None,
    entidad_id: Optional[int] = None,
    detalle: Optional[dict] = None,
    ip: Optional[str] = None,
) -> None:
    try:
        evento = BitacoraEvento(
            usuario_id=usuario_id,
            usuario_nombre=usuario_nombre,
            accion=accion,
            entidad=entidad,
            entidad_id=entidad_id,
            detalle=json.dumps(detalle, default=str, ensure_ascii=False) if detalle else None,
            ip=ip,
        )
        db.add(evento)
        await db.commit()
    except Exception:
        await db.rollback()


async def listar_eventos(
    db: AsyncSession,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    accion: Optional[str] = None,
    usuario_id: Optional[int] = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[BitacoraEvento], int]:
    filters = []
    if desde:
        filters.append(BitacoraEvento.created_at >= desde)
    if hasta:
        filters.append(BitacoraEvento.created_at <= hasta)
    if accion:
        filters.append(BitacoraEvento.accion == accion)
    if usuario_id:
        filters.append(BitacoraEvento.usuario_id == usuario_id)

    base_q = select(BitacoraEvento)
    if filters:
        base_q = base_q.where(and_(*filters))

    count_result = await db.execute(
        select(func.count()).select_from(base_q.subquery())
    )
    total = count_result.scalar_one()

    query = base_q.order_by(desc(BitacoraEvento.created_at)).offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    return list(result.scalars().all()), total


async def obtener_evento(evento_id: int, db: AsyncSession) -> Optional[BitacoraEvento]:
    result = await db.execute(select(BitacoraEvento).where(BitacoraEvento.id == evento_id))
    return result.scalar_one_or_none()


# ── CU32 - Recordatorios de mantenimiento ─────────────────
async def obtener_recordatorios_mantenimiento(
    usuario_id: int, db: AsyncSession
) -> list[dict]:
    from app.acceso_registro.models import Vehiculo
    from app.talleres_tecnicos.models import ServicioRealizado, Asignacion
    from app.emergencias.models import Incidente

    vehiculos_result = await db.execute(
        select(Vehiculo).where(Vehiculo.usuario_id == usuario_id, Vehiculo.activo.is_(True))
    )
    vehiculos = list(vehiculos_result.scalars().all())

    recordatorios = []
    UMBRAL_GLOBAL = 90  # fallback cuando no hay historial suficiente

    CATEGORIAS: dict[str, list[str]] = {
        "batería":    ["batería", "bateria", "battery", "arranque", "carga eléctrica"],
        "frenos":     ["freno", "frenos", "pastilla", "disco", "frena"],
        "aceite":     ["aceite", "lubricante", "cambio de aceite"],
        "llantas":    ["llanta", "neumático", "neumatico", "goma", "ponchadura", "pinchazo"],
        "motor":      ["motor", "radiador", "sobrecalentamiento", "culata"],
        "suspensión": ["suspensión", "suspension", "amortiguador", "dirección", "direccion"],
        "eléctrico":  ["eléctrico", "electrico", "alternador", "fusible", "corto"],
    }

    for v in vehiculos:
        query = (
            select(ServicioRealizado)
            .join(Asignacion, ServicioRealizado.asignacion_id == Asignacion.id)
            .join(Incidente, Asignacion.incidente_id == Incidente.id)
            .where(Incidente.vehiculo_id == v.id)
            .order_by(ServicioRealizado.fecha_cierre.asc())
        )
        srv_result = await db.execute(query)
        servicios = list(srv_result.scalars().all())

        if not servicios:
            recordatorios.append({
                "vehiculo_id": v.id,
                "placa": v.placa,
                "marca": v.marca,
                "modelo": v.modelo,
                "anio": v.anio,
                "dias_desde_ultimo_servicio": None,
                "ultimo_servicio": None,
                "intervalo_recomendado": None,
                "problemas_recurrentes": [],
                "mensaje": f"El vehículo {v.marca} {v.modelo} ({v.placa}) no tiene historial de servicios en la plataforma.",
                "urgencia": "sin_historial",
            })
            continue

        fechas = []
        for s in servicios:
            f = s.fecha_cierre
            if f.tzinfo is None:
                f = f.replace(tzinfo=timezone.utc)
            fechas.append(f)

        intervalo_recomendado: int | None = None
        if len(fechas) >= 2:
            intervalos = [(fechas[i] - fechas[i - 1]).days for i in range(1, len(fechas))]
            promedio = sum(intervalos) / len(intervalos)
            intervalo_recomendado = max(30, int(promedio))

        umbral = intervalo_recomendado if intervalo_recomendado else UMBRAL_GLOBAL

        conteo: dict[str, int] = {}
        for s in servicios:
            texto = ((s.descripcion_trabajo or "") + " " + (s.observaciones or "")).lower()
            for categoria, palabras in CATEGORIAS.items():
                if any(p in texto for p in palabras):
                    conteo[categoria] = conteo.get(categoria, 0) + 1
        recurrentes = [cat for cat, cnt in conteo.items() if cnt >= 2]

        ahora = datetime.now(timezone.utc)
        dias = (ahora - fechas[-1]).days

        if dias >= int(umbral * 1.5):
            urgencia = "alta"
        elif dias >= umbral:
            urgencia = "media"
        elif dias >= int(umbral * 0.75):
            urgencia = "baja"
        else:
            continue

        if intervalo_recomendado:
            base = (
                f"Han pasado {dias} días desde el último servicio de {v.marca} {v.modelo} ({v.placa}). "
                f"Basado en tu historial, este vehículo se revisa cada ~{intervalo_recomendado} días."
            )
        else:
            base = (
                f"Han pasado {dias} días desde el último servicio de {v.marca} {v.modelo} ({v.placa}). "
                f"Se recomienda mantenimiento preventivo cada {UMBRAL_GLOBAL} días."
            )
        if recurrentes:
            base += f" Problemas frecuentes detectados: {', '.join(recurrentes)}."

        recordatorios.append({
            "vehiculo_id": v.id,
            "placa": v.placa,
            "marca": v.marca,
            "modelo": v.modelo,
            "anio": v.anio,
            "dias_desde_ultimo_servicio": dias,
            "ultimo_servicio": fechas[-1].isoformat(),
            "intervalo_recomendado": intervalo_recomendado,
            "problemas_recurrentes": recurrentes,
            "mensaje": base,
            "urgencia": urgencia,
        })

    return recordatorios


async def exportar_csv(
    db: AsyncSession,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    accion: Optional[str] = None,
    usuario_id: Optional[int] = None,
) -> str:
    eventos, _ = await listar_eventos(db, desde, hasta, accion, usuario_id, page=1, size=10000)
    lines = ["id,fecha_hora,usuario,accion,entidad,entidad_id,ip,detalle"]
    for e in eventos:
        det = (e.detalle or "").replace('"', "'").replace("\n", " ")
        fecha = e.created_at.strftime("%Y-%m-%d %H:%M:%S") if e.created_at else ""
        lines.append(
            f'{e.id},{fecha},"{e.usuario_nombre or ""}",{e.accion},"{e.entidad or ""}",{e.entidad_id or ""},'
            f'{e.ip or ""},"{det}"'
        )
    return "\n".join(lines)


async def listar_calificaciones_pendientes(cliente_id: int, db: AsyncSession) -> list[dict]:
    from app.acceso_registro.models import Taller
    from app.emergencias.models import Incidente
    from app.talleres_tecnicos.models import Asignacion
    from app.reportes.models import CalificacionServicio

    res = await db.execute(
        select(
            Asignacion.id,
            Asignacion.incidente_id,
            Asignacion.taller_id,
            Taller.nombre,
            Asignacion.created_at,
        )
        .join(Incidente, Incidente.id == Asignacion.incidente_id)
        .join(Taller, Taller.id == Asignacion.taller_id)
        .outerjoin(CalificacionServicio, CalificacionServicio.asignacion_id == Asignacion.id)
        .where(
            Incidente.usuario_id == cliente_id,
            Asignacion.estado == "finalizado",
            CalificacionServicio.id.is_(None),
        )
        .order_by(desc(Asignacion.created_at))
    )
    return [
        {
            "asignacion_id": r[0],
            "incidente_id": r[1],
            "taller_id": r[2],
            "taller_nombre": r[3],
            "fecha_finalizacion": r[4],
        }
        for r in res.all()
    ]


async def crear_calificacion(
    cliente_id: int,
    asignacion_id: int,
    puntuacion: int,
    resena: Optional[str],
    db: AsyncSession,
):
    from fastapi import HTTPException
    from app.acceso_registro.models import Taller
    from app.emergencias.models import Incidente
    from app.talleres_tecnicos.models import Asignacion
    from app.reportes.models import CalificacionServicio

    if puntuacion < 1 or puntuacion > 5:
        raise HTTPException(status_code=400, detail="La puntuación debe estar entre 1 y 5")

    asig_res = await db.execute(
        select(Asignacion, Incidente.usuario_id, Taller.id)
        .join(Incidente, Incidente.id == Asignacion.incidente_id)
        .join(Taller, Taller.id == Asignacion.taller_id)
        .where(Asignacion.id == asignacion_id)
    )
    row = asig_res.first()
    if not row:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")

    asignacion, incidente_usuario_id, taller_id = row
    if incidente_usuario_id != cliente_id:
        raise HTTPException(status_code=403, detail="No puedes calificar esta asignación")
    if asignacion.estado != "finalizado":
        raise HTTPException(status_code=400, detail="Solo puedes calificar servicios finalizados")

    existing = await db.execute(
        select(CalificacionServicio).where(CalificacionServicio.asignacion_id == asignacion_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Esta asignación ya fue calificada")

    cal = CalificacionServicio(
        asignacion_id=asignacion_id,
        cliente_id=cliente_id,
        taller_id=taller_id,
        puntuacion=puntuacion,
        resena=(resena or "").strip() or None,
    )
    db.add(cal)
    await db.commit()
    await db.refresh(cal)
    return cal


async def obtener_metricas(
    db: AsyncSession,
    desde: Optional[datetime],
    hasta: Optional[datetime],
    taller_id: Optional[int] = None,
) -> dict:
    from app.cotizacion_pagos.models import Cotizacion, Pago
    from app.talleres_tecnicos.models import Asignacion
    from app.reportes.models import Calificacion

    pago_filters = []
    asig_filters = []
    cal_filters = []

    if taller_id is not None:
        pago_filters.append(Cotizacion.taller_id == taller_id)
        asig_filters.append(Asignacion.taller_id == taller_id)
        cal_filters.append(Calificacion.taller_id == taller_id)
    if desde:
        pago_filters.append(Pago.created_at >= desde)
        asig_filters.append(Asignacion.created_at >= desde)
        cal_filters.append(Calificacion.created_at >= desde)
    if hasta:
        pago_filters.append(Pago.created_at <= hasta)
        asig_filters.append(Asignacion.created_at <= hasta)
        cal_filters.append(Calificacion.created_at <= hasta)

    pagos_q = (
        select(Pago.id, Pago.monto, Pago.metodo, Pago.created_at, Pago.cotizacion_id, Cotizacion.incidente_id)
        .join(Cotizacion, Cotizacion.id == Pago.cotizacion_id)
        .order_by(desc(Pago.created_at))
    )
    if pago_filters:
        pagos_q = pagos_q.where(and_(*pago_filters))
    pagos_res = await db.execute(pagos_q)
    pagos = pagos_res.all()

    servicios_q = select(func.count()).select_from(Asignacion)
    if asig_filters:
        servicios_q = servicios_q.where(and_(*asig_filters))
    total_servicios = (await db.execute(servicios_q)).scalar_one()

    finalizados_q = select(func.count()).select_from(Asignacion).where(Asignacion.estado == "finalizado")
    if asig_filters:
        finalizados_q = finalizados_q.where(and_(*asig_filters))
    servicios_finalizados = (await db.execute(finalizados_q)).scalar_one()

    cal_q = select(func.avg(Calificacion.puntuacion), func.count(Calificacion.id))
    if cal_filters:
        cal_q = cal_q.where(and_(*cal_filters))
    avg_cal, total_cal = (await db.execute(cal_q)).first()

    ingresos_brutos = round(sum(float(p[1]) for p in pagos), 2)
    comision = round(ingresos_brutos * 0.10, 2)
    netos = round(ingresos_brutos - comision, 2)
    pagados = len(pagos)
    ticket = round(ingresos_brutos / pagados, 2) if pagados else 0.0

    return {
        "desde": desde,
        "hasta": hasta,
        "total_servicios": int(total_servicios or 0),
        "servicios_finalizados": int(servicios_finalizados or 0),
        "servicios_pagados": pagados,
        "ingresos_brutos": ingresos_brutos,
        "comision_plataforma": comision,
        "ingresos_netos": netos,
        "ticket_promedio": ticket,
        "promedio_calificacion": round(float(avg_cal), 2) if avg_cal is not None else None,
        "total_calificaciones": int(total_cal or 0),
        "detalle_pagos": [
            {
                "pago_id": p[0],
                "monto": float(p[1]),
                "metodo": p[2],
                "fecha": p[3].isoformat() if p[3] else None,
                "cotizacion_id": p[4],
                "incidente_id": p[5],
            }
            for p in pagos
        ],
    }


# ── CU41 · KPIs operacionales ─────────────────────────────
async def obtener_kpis(
    db: AsyncSession,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    tenant_id: Optional[int] = None,
    taller_id_filtro: Optional[int] = None,
) -> dict:
    from app.emergencias.models import Incidente
    from app.talleres_tecnicos.models import Asignacion, ServicioRealizado
    from app.acceso_registro.models import Taller

    # ── Filtros base ──────────────────────────────────────────
    inc_filters = []
    asig_filters = []
    if desde:
        inc_filters.append(Incidente.created_at >= desde)
        asig_filters.append(Asignacion.created_at >= desde)
    if hasta:
        inc_filters.append(Incidente.created_at <= hasta)
        asig_filters.append(Asignacion.created_at <= hasta)
    if taller_id_filtro:
        asig_filters.append(Asignacion.taller_id == taller_id_filtro)

    # Para filtrar por tenant: pasar por la tabla talleres
    tenant_join_needed = tenant_id is not None and taller_id_filtro is None

    # ── 1. Tiempo promedio de asignación (incidente → taller acepta) ─
    q_asig_time = (
        select(func.avg(
            func.extract('epoch', Asignacion.created_at - Incidente.created_at) / 60
        ))
        .join(Incidente, Asignacion.incidente_id == Incidente.id)
    )
    if tenant_join_needed:
        q_asig_time = q_asig_time.join(Taller, Asignacion.taller_id == Taller.id).where(Taller.tenant_id == tenant_id)
    if asig_filters:
        q_asig_time = q_asig_time.where(and_(*asig_filters))
    avg_asig = (await db.execute(q_asig_time)).scalar_one_or_none()

    # ── 2. Tiempo promedio de servicio (aceptación → cierre) ─────────
    q_srv_time = (
        select(func.avg(
            func.extract('epoch', ServicioRealizado.fecha_cierre - Asignacion.created_at) / 60
        ))
        .join(Asignacion, ServicioRealizado.asignacion_id == Asignacion.id)
    )
    if tenant_join_needed:
        q_srv_time = q_srv_time.join(Taller, Asignacion.taller_id == Taller.id).where(Taller.tenant_id == tenant_id)
    if asig_filters:
        q_srv_time = q_srv_time.where(and_(*asig_filters))
    avg_srv = (await db.execute(q_srv_time)).scalar_one_or_none()

    # ── 3. Incidentes por tipo ────────────────────────────────────────
    q_tipos = select(Incidente.tipo_incidente, func.count(Incidente.id)).group_by(Incidente.tipo_incidente)
    if inc_filters:
        q_tipos = q_tipos.where(and_(*inc_filters))
    if taller_id_filtro:
        q_tipos = q_tipos.join(Asignacion, Asignacion.incidente_id == Incidente.id).where(Asignacion.taller_id == taller_id_filtro)
    if tenant_join_needed:
        q_tipos = (
            q_tipos.join(Asignacion, Asignacion.incidente_id == Incidente.id)
            .join(Taller, Asignacion.taller_id == Taller.id)
            .where(Taller.tenant_id == tenant_id)
        )
    tipos_rows = (await db.execute(q_tipos)).all()
    por_tipo = {(r[0] or "otros"): r[1] for r in tipos_rows}

    # ── 4. Talleres más eficientes (top 5 por tiempo promedio) ───────
    q_efic = (
        select(
            Asignacion.taller_id,
            Taller.nombre,
            func.avg(func.extract('epoch', ServicioRealizado.fecha_cierre - Asignacion.created_at) / 60).label("avg_min"),
            func.count(ServicioRealizado.id).label("total"),
        )
        .join(ServicioRealizado, ServicioRealizado.asignacion_id == Asignacion.id)
        .join(Taller, Taller.id == Asignacion.taller_id)
        .group_by(Asignacion.taller_id, Taller.nombre)
        .order_by(text("avg_min ASC"))
        .limit(5)
    )
    if tenant_join_needed:
        q_efic = q_efic.where(Taller.tenant_id == tenant_id)
    if taller_id_filtro:
        q_efic = q_efic.where(Asignacion.taller_id == taller_id_filtro)
    if asig_filters:
        q_efic = q_efic.where(and_(*asig_filters))
    efic_rows = (await db.execute(q_efic)).all()
    talleres_eficientes = [
        {"taller_id": r[0], "nombre": r[1], "tiempo_promedio_min": round(float(r[2] or 0), 1), "total_servicios": r[3]}
        for r in efic_rows
    ]

    # ── 5. Zonas con más incidentes (top 10) ─────────────────────────
    lat_col = func.round(cast(Incidente.latitud,  Numeric(10, 6)), 1).label("lat")
    lon_col = func.round(cast(Incidente.longitud, Numeric(10, 6)), 1).label("lon")
    q_zonas = (
        select(lat_col, lon_col, func.count(Incidente.id).label("total"))
        .where(Incidente.latitud.isnot(None), Incidente.longitud.isnot(None))
        .group_by(lat_col, lon_col)
        .order_by(text("total DESC"))
        .limit(10)
    )
    if inc_filters:
        q_zonas = q_zonas.where(and_(*inc_filters))
    zonas_rows = (await db.execute(q_zonas)).all()
    zonas_calientes = [
        {"lat": float(r[0] or 0), "lon": float(r[1] or 0), "total": r[2]}
        for r in zonas_rows
    ]

    # ── 6. Casos cancelados ───────────────────────────────────────────
    q_canc_asig = select(func.count(Asignacion.id)).where(Asignacion.estado == "cancelado")
    q_canc_inc  = select(func.count(Incidente.id)).where(Incidente.estado == "cancelado")
    if asig_filters:
        q_canc_asig = q_canc_asig.where(and_(*asig_filters))
    if inc_filters:
        q_canc_inc = q_canc_inc.where(and_(*inc_filters))
    if tenant_join_needed:
        q_canc_asig = q_canc_asig.join(Taller, Asignacion.taller_id == Taller.id).where(Taller.tenant_id == tenant_id)
    if taller_id_filtro:
        q_canc_asig = q_canc_asig.where(Asignacion.taller_id == taller_id_filtro)
    cancelados_asig = (await db.execute(q_canc_asig)).scalar_one()
    cancelados_inc  = (await db.execute(q_canc_inc)).scalar_one()

    # ── 7. SLA compliance (servicio completo en < 120 min) ───────────
    SLA_MINUTOS = 120
    q_sla = (
        select(
            func.count(ServicioRealizado.id).label("total"),
            func.sum(case(
                (func.extract('epoch', ServicioRealizado.fecha_cierre - Asignacion.created_at) <= SLA_MINUTOS * 60, 1),
                else_=0
            )).label("dentro_sla"),
        )
        .join(Asignacion, ServicioRealizado.asignacion_id == Asignacion.id)
    )
    if tenant_join_needed:
        q_sla = q_sla.join(Taller, Asignacion.taller_id == Taller.id).where(Taller.tenant_id == tenant_id)
    if taller_id_filtro:
        q_sla = q_sla.where(Asignacion.taller_id == taller_id_filtro)
    if asig_filters:
        q_sla = q_sla.where(and_(*asig_filters))
    sla_row = (await db.execute(q_sla)).first()
    total_srv   = int(sla_row[0] or 0)
    dentro_sla  = int(sla_row[1] or 0)
    sla_pct     = round(dentro_sla / total_srv * 100, 1) if total_srv > 0 else 0.0

    # ── 8. Total incidentes y servicios ──────────────────────────────
    q_total_inc = select(func.count(Incidente.id))
    if inc_filters:
        q_total_inc = q_total_inc.where(and_(*inc_filters))
    total_incidentes = (await db.execute(q_total_inc)).scalar_one()

    return {
        "tiempo_promedio_asignacion_min": round(float(avg_asig or 0), 1),
        "tiempo_promedio_servicio_min":   round(float(avg_srv or 0), 1),
        "total_incidentes":               int(total_incidentes),
        "total_servicios_completados":    total_srv,
        "casos_cancelados_asignacion":    int(cancelados_asig),
        "casos_cancelados_incidente":     int(cancelados_inc),
        "sla_compliance_pct":             sla_pct,
        "sla_minutos":                    SLA_MINUTOS,
        "dentro_sla":                     dentro_sla,
        "incidentes_por_tipo":            por_tipo,
        "talleres_eficientes":            talleres_eficientes,
        "zonas_calientes":                zonas_calientes,
    }
