"""Schémas agent STOCK (chat)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from apps.femi_agent.schemas.common import ConfidenceEnum

StockAction = Literal[
    "ENTREE",
    "SORTIE",
    "AJUSTEMENT",
    "CREATE_PRODUIT",
    "RESUME",
    "LISTE",
    "STATUT",
]


class StockExtractionLLMResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: StockAction = Field(
        ...,
        description="ENTREE=réappro, SORTIE=vente/sortie, AJUSTEMENT=inventaire, "
        "CREATE_PRODUIT=nouveau produit, RESUME=état du stock, LISTE=lister produits, "
        "STATUT=consulter le stock d'un produit précis",
    )
    produit_nom: Optional[str] = None
    sku: Optional[str] = None
    quantite: Optional[float] = None
    cout_unitaire: Optional[float] = None
    prix_vente: Optional[float] = None
    unite: Optional[str] = None
    seuil_alerte: Optional[float] = None
    note: Optional[str] = None
    needs_clarification: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    confidence: ConfidenceEnum = ConfidenceEnum.MEDIUM


class StockExtractionResult(BaseModel):
    """Résultat interne après garde-fous backend."""

    model_config = ConfigDict(extra="forbid")

    action: StockAction
    produit_nom: Optional[str] = None
    sku: Optional[str] = None
    quantite: Optional[float] = None
    cout_unitaire: Optional[float] = None
    prix_vente: Optional[float] = None
    unite: Optional[str] = None
    seuil_alerte: Optional[float] = None
    note: Optional[str] = None
    needs_clarification: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    resume_texte: Optional[str] = None

    @classmethod
    def from_llm(cls, llm: StockExtractionLLMResult) -> "StockExtractionResult":
        missing: list[str] = []
        action = llm.action
        nom = (llm.produit_nom or "").strip() or None
        qte = llm.quantite

        if action in ("ENTREE", "SORTIE", "AJUSTEMENT"):
            if not nom:
                missing.append("produit_nom")
            if qte is None or qte < 0:
                missing.append("quantite")
            if action == "SORTIE" and qte is not None and qte == 0:
                missing.append("quantite")
        elif action in ("CREATE_PRODUIT", "STATUT"):
            if not nom:
                missing.append("produit_nom")

        # missing du LLM en plus (sans doublon)
        for m in llm.missing_fields or []:
            if m not in missing:
                missing.append(m)

        return cls(
            action=action,
            produit_nom=nom,
            sku=(llm.sku or "").strip() or None,
            quantite=qte,
            cout_unitaire=llm.cout_unitaire,
            prix_vente=llm.prix_vente,
            unite=(llm.unite or "").strip() or None,
            seuil_alerte=llm.seuil_alerte,
            note=(llm.note or "").strip() or None,
            needs_clarification=bool(missing) or llm.needs_clarification,
            missing_fields=missing,
        )