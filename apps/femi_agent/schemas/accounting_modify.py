"""
Schémas de sortie pour ACCOUNTING_MODIFY_PROMPT (UPDATE/DELETE d'opérations
existantes). Trois étapes (`step`), 6 formats de sortie possibles au total —
voir accounting_modify_prompt.py, section FORMAT RECHERCHE / RÉSOLUTION /
PROPOSITION (lignes ~795-885 au moment de l'écriture de ce fichier).

Convention de nommage des champs de recherche (search_criteria) et vocabulaire
de date_range : identiques à financial_analyst_prompt.py / tools/financials.py
(PERIODES_VALIDES) — voir accounting_modify_prompt.py section 4.

Convention confidence : ConfidenceEnum (common.py), vocabulaire partagé par
tous les agents — ne pas redéfinir localement.
"""

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .common import ConfidenceEnum


class SearchCriteria(BaseModel):
    """
    Critères de recherche d'une opération existante — section 4 du prompt.
    Tous les champs sont optionnels ; l'absence d'un champ ne bloque pas la
    recherche si d'autres critères sont exploitables (voir section 4,
    sous-section date_range).
    """

    model_config = ConfigDict(extra="forbid")

    operation_id: Optional[str] = None
    contact: Optional[str] = Field(
        default=None,
        description=(
            "Nom texte brut, jamais résolu en instance Contact par "
            "l'executor (contrairement à CUSTOMER) — l'ambiguïté est gérée "
            "par le CAS 2 (candidats multiples) au moment de la recherche."
        ),
    )
    amount_ttc: Optional[float] = None
    category: Optional[str] = None
    date_range: Optional[str] = Field(
        default=None,
        description=(
            "Vocabulaire fermé, identique à PERIODES_VALIDES "
            "(tools/financials.py) : aujourd'hui, hier, cette_semaine, "
            "semaine_derniere, ce_mois, mois_dernier, cette_annee, "
            "annee_derniere, depuis_debut, personnalisee."
        ),
    )
    date_debut: Optional[str] = Field(
        default=None, description="Format AAAA-MM-JJ, requis si date_range='personnalisee'."
    )
    date_fin: Optional[str] = Field(
        default=None, description="Format AAAA-MM-JJ, requis si date_range='personnalisee'."
    )
    description_keywords: Optional[str] = None
    transaction_type: Optional[str] = None


class AccountingModifySearchResult(BaseModel):
    """FORMAT RECHERCHE — premier appel LLM, avant exécution des tools."""

    model_config = ConfigDict(extra="forbid")

    step: Literal["search"]
    action_type: Literal["UPDATE", "DELETE"]
    search_criteria: SearchCriteria
    needs_clarification: bool = False
    missing_fields: list[str] = Field(default_factory=list)


class OperationCandidate(BaseModel):
    """Un candidat parmi plusieurs, en cas d'ambiguïté de la recherche."""

    model_config = ConfigDict(extra="forbid")

    operation_id: str
    summary: str


class AccountingModifyResolutionResult(BaseModel):
    """
    FORMAT RÉSOLUTION — couvre les 3 variantes du prompt (aucun résultat /
    erreur de recherche / plusieurs candidats), distinguées par le contenu
    de missing_fields et la présence de candidates. Une seule classe : Pydantic
    ne peut pas discriminer sur le contenu d'une liste, seulement sur un
    littéral — la cohérence entre missing_fields et candidates reste une
    convention non vérifiée par le type-checker, à respecter au moment de
    la construction de l'objet côté executor.
    """

    model_config = ConfigDict(extra="forbid")

    step: Literal["resolution"]
    action_type: Literal["UPDATE", "DELETE"]
    needs_clarification: Literal[True] = True
    missing_fields: list[str]
    candidates: Optional[list[OperationCandidate]] = None


class AccountingModifyProposeChangeResult(BaseModel):
    """
    FORMAT PROPOSITION UPDATE / DELETE. proposed_values est None uniquement
    pour DELETE (le prompt le montre explicitement à null dans ce cas).
    requires_confirmation est toujours true — règle absolue du prompt,
    l'agent ne fait jamais qu'une proposition, jamais d'écriture réelle.
    """

    model_config = ConfigDict(extra="forbid")

    step: Literal["propose_change"]
    action_type: Literal["UPDATE", "DELETE"]
    operation_id: str
    current_values: dict[str, Any] = Field(default_factory=dict)
    proposed_values: Optional[dict[str, Any]] = None
    requires_confirmation: Literal[True] = True
    confidence: ConfidenceEnum = ConfidenceEnum.MEDIUM
    needs_clarification: bool = False
    missing_fields: list[str] = Field(default_factory=list)


AccountingModifyResult = Union[
    AccountingModifySearchResult,
    AccountingModifyResolutionResult,
    AccountingModifyProposeChangeResult,
]
"""
Union discriminée sur `step`. Utiliser un `Field(discriminator="step")` au
point d'usage (ex. dans accounting_modify_executor.py) plutôt qu'ici, pour
ne pas imposer un TypeAdapter/wrapper à ce module de simples schémas.
"""