import uuid

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from lsmfapi.database.db import Base


class Recipe(Base):
    __tablename__ = "recipes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    station_id = Column(String, nullable=True)
    description = Column(String, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())

    rules = relationship("RecipeRule", back_populates="recipe", cascade="all, delete-orphan")


class RecipeRule(Base):
    __tablename__ = "recipe_rules"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    recipe_id = Column(String, ForeignKey("recipes.id"), nullable=False)
    variable = Column(String, nullable=False)
    correction_type = Column(String, nullable=False)  # additive | multiplicative
    value = Column(Float, nullable=False)
    condition_json = Column(String, nullable=True)

    recipe = relationship("Recipe", back_populates="rules")
