from sqlalchemy import Boolean, Column, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import deferred
from sqlalchemy.sql import func
from app.db.base import Base


class Tecnico(Base):
    __tablename__ = "tecnicos"

    id            = Column(Integer, primary_key=True, index=True)
    usuario_id    = Column(Integer, ForeignKey("users.id"), nullable=True, unique=True)
    taller_id     = Column(Integer, ForeignKey("talleres.id"), nullable=False)
    nombre        = Column(String(200), nullable=False)
    especialidad  = Column(String(200), nullable=False)
    telefono      = Column(String(20), nullable=True)
    estado        = Column(String(20), default="disponible")   # disponible | ocupado | inactivo
    activo        = Column(Boolean, default=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    # CU17 — ubicación en tiempo real; carga diferida para no romper SELECT si la BD
    # fue creada antes de que estas columnas se agregaran al modelo.
    latitud              = deferred(Column(Float, nullable=True))
    longitud             = deferred(Column(Float, nullable=True))
    ultima_actualizacion = deferred(Column(DateTime(timezone=True), nullable=True))


class Asignacion(Base):
    __tablename__ = "asignaciones"

    id           = Column(Integer, primary_key=True, index=True)
    incidente_id = Column(Integer, ForeignKey("incidentes.id"), nullable=False)
    taller_id    = Column(Integer, ForeignKey("talleres.id"), nullable=False)
    tecnico_id   = Column(Integer, ForeignKey("tecnicos.id"), nullable=True)
    # aceptado | en_camino | en_sitio | en_reparacion | finalizado | cancelado
    estado       = Column(String(20), default="aceptado")
    eta          = Column(Integer, nullable=True)
    observacion  = Column(String(500), nullable=True)
    fecha_llegada = Column(DateTime(timezone=True), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


class CobroVisita(Base):
    """Cobro por desplazamiento cuando el técnico llegó pero no realizó el servicio
    (el cliente canceló estando en_sitio, o el vehículo tenía seguro y el seguro cubrió)."""
    __tablename__ = "cobros_visita"

    id            = Column(Integer, primary_key=True, index=True)
    asignacion_id = Column(Integer, ForeignKey("asignaciones.id"), nullable=False, unique=True)
    tecnico_id    = Column(Integer, ForeignKey("tecnicos.id"), nullable=True)
    monto         = Column(Float, nullable=False)
    concepto      = Column(String(500), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


class ServicioRealizado(Base):
    __tablename__ = "servicios_realizados"

    id                  = Column(Integer, primary_key=True, index=True)
    asignacion_id       = Column(Integer, ForeignKey("asignaciones.id"), nullable=False, unique=True)
    descripcion_trabajo = Column(String(1000), nullable=False)
    repuestos           = Column(String(2000), nullable=True)   # JSON string
    observaciones       = Column(String(500), nullable=True)
    fecha_cierre        = Column(DateTime(timezone=True), server_default=func.now())
