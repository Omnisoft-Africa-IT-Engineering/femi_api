"""Schéma OCR stock : photo → champs formulaire (sans écriture en base)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StockOcrLigne(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nom: str = Field(..., description="Nom du produit / article lu")
    quantite: Optional[float] = Field(default=None, description="Quantité si visible")
    cout_unitaire: Optional[float] = Field(
        default=None, description="Prix d'achat unitaire si visible"
    )
    prix_vente: Optional[float] = Field(
        default=None, description="Prix de vente si visible"
    )
    sku: Optional[str] = Field(default=None, description="Code article / SKU / code-barres")
    unite: Optional[str] = Field(default=None, description="Unité (kg, pièce, carton…)")


class StockOcrPreviewResult(BaseModel):
    """Sortie OCR pour préremplir un formulaire stock (aucune persistance)."""

    model_config = ConfigDict(extra="forbid")

    type_suggere: Literal["ENTREE", "SORTIE", "AJUSTEMENT", "CREATE_PRODUIT", "INCONNU"] = "INCONNU"
    lignes: list[StockOcrLigne] = Field(default_factory=list)
    note: Optional[str] = Field(
        default=None,
        description="Précision libre (ex. document illisible partiellement)",
    )
    confidence: Literal["high", "medium", "low"] = "medium"
