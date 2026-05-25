from sqlalchemy import Boolean, Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.db.base import Base


class Mensaje(Base):
    __tablename__ = "mensajes"

    id            = Column(Integer, primary_key=True, index=True)
    asignacion_id = Column(Integer, ForeignKey("asignaciones.id"), nullable=False)
    usuario_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    contenido     = Column(String(2000), nullable=False)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


class Notificacion(Base):
    __tablename__ = "notificaciones"

    id            = Column(Integer, primary_key=True, index=True)
    usuario_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    titulo        = Column(String(200), nullable=False)
    mensaje       = Column(String(1000), nullable=False)
    tipo          = Column(String(50), nullable=True)   # asignacion | estado | sistema
    leida         = Column(Boolean, default=False)
    referencia_id = Column(Integer, nullable=True)      # incidente_id o asignacion_id
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
