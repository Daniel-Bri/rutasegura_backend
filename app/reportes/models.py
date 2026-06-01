from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from app.db.base import Base


class Calificacion(Base):
    __tablename__ = "calificaciones"

    id            = Column(Integer, primary_key=True, index=True)
    asignacion_id = Column(Integer, ForeignKey("asignaciones.id"), nullable=False, unique=True)
    cliente_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    taller_id     = Column(Integer, ForeignKey("talleres.id"), nullable=False, index=True)
    puntuacion    = Column(Integer, nullable=False)        # 1–5
    comentario    = Column(String(500), nullable=True)
    estado        = Column(String(20), default="activa")   # activa | eliminada
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


class BitacoraEvento(Base):
    __tablename__ = "bitacora_eventos"

    id             = Column(Integer, primary_key=True, index=True)
    usuario_id     = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    usuario_nombre = Column(String(255), nullable=True)
    accion         = Column(String(100), nullable=False, index=True)
    entidad        = Column(String(100), nullable=True, index=True)
    entidad_id     = Column(Integer, nullable=True)
    detalle        = Column(Text, nullable=True)   # JSON: {"antes": {...}, "despues": {...}}
    ip             = Column(String(50), nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class CalificacionServicio(Base):
    __tablename__ = "calificaciones_servicio"

    id            = Column(Integer, primary_key=True, index=True)
    asignacion_id = Column(Integer, ForeignKey("asignaciones.id"), nullable=False, unique=True, index=True)
    cliente_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    taller_id     = Column(Integer, ForeignKey("talleres.id"), nullable=False, index=True)
    puntuacion    = Column(Integer, nullable=False)  # 1..5
    resena        = Column(Text, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), index=True)
