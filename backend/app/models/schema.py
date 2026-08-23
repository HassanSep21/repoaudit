from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship, declarative_base
from pydantic import BaseModel, ConfigDict

Base = declarative_base()


class Repo(Base):
    __tablename__ = "repos"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, index=True, nullable=False)
    owner = Column(String, nullable=False)
    name = Column(String, nullable=False)
    default_branch = Column(String, nullable=True)
    primary_languages = Column(Text, nullable=True)
    size_kb = Column(Integer, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)

    analysis_runs = relationship("AnalysisRun", back_populates="repo")


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id = Column(Integer, primary_key=True, index=True)
    repo_id = Column(Integer, ForeignKey("repos.id"), nullable=False)
    status = Column(String, nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    overall_score = Column(Integer, nullable=True)
    overall_verdict = Column(String, nullable=True)
    pillars_completed = Column(String, nullable=True)

    repo = relationship("Repo", back_populates="analysis_runs")
    pillar_results = relationship("PillarResult", back_populates="run")


class PillarResult(Base):
    __tablename__ = "pillar_results"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("analysis_runs.id"), nullable=False)
    pillar_name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    score = Column(Integer, nullable=True)
    tier = Column(Integer, nullable=True)
    summary = Column(Text, nullable=True)

    run = relationship("AnalysisRun", back_populates="pillar_results")
    findings = relationship("Finding", back_populates="pillar_result")


class Finding(Base):
    __tablename__ = "findings"

    id = Column(Integer, primary_key=True, index=True)
    pillar_result_id = Column(Integer, ForeignKey("pillar_results.id"), nullable=False)
    severity = Column(String, nullable=False)
    category = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    file_path = Column(String, nullable=True)
    line = Column(Integer, nullable=True)

    pillar_result = relationship("PillarResult", back_populates="findings")


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    severity: str
    category: str
    message: str
    file_path: Optional[str] = None
    line: Optional[int] = None


class PillarResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    status: str
    tier: int
    score: Optional[int] = None
    summary: str
    findings: list[FindingOut] = []


class AnalysisRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: str
    overall_score: Optional[int] = None
    overall_verdict: Optional[str] = None
    pillars_completed: str
    pillars: list[PillarResultOut] = []