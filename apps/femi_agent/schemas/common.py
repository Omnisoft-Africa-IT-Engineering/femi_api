
"""
Schémas partagés, indépendants du pipeline d'extraction utilisé
(ancien Ollama/GBNF ou nouveau Router/ACCOUNTING_PROMPT).

ProcessResult est le contrat de réponse retourné par le Manager vers les
Webhooks et l'API — il survit à la migration entre les deux pipelines,
contrairement à ollama_gbnf_extraction.py.
"""
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field

from .ollama_gbnf_extraction import ParsedOperationSchema


class ConfidenceEnum(str, Enum):
    """
    Niveau de confiance catégorique — vocabulaire unique partagé par tous
    les agents (ACCOUNTING, ACCOUNTING_MODIFY, et futurs FINANCIAL_ANALYST/
    CUSTOMER si besoin). Ne pas redéfinir localement dans un autre schéma.
    """
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class ProcessResult(BaseModel):
    """
    Contrat de réponse standardisé (Data Transfer Object) retourné
    par le Manager vers les Webhooks et l'API.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool = Field(description="Statut du succès du traitement.")
    operation_id: Optional[str] = Field(default=None, description="UUID de l'opération enregistrée en BDD.")
    message: str = Field(description="Message formaté destiné à l'affichage utilisateur.")
    parsed_data: Optional[ParsedOperationSchema] = Field(default=None, description="Données extraites validées.")
    operation_instance: Optional[Any] = Field(
        default=None, description="Instance ORM Django liée (usage interne, non sérialisée en API)."
    )