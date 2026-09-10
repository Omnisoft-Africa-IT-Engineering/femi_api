import logging
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


class PaymentMethodEnum(str, Enum):
    """Modes de paiement supportés par Femi."""
    CASH = "CASH"
    MOBILE_MONEY = "MOBILE_MONEY"
    BANK_TRANSFER = "BANK_TRANSFER"
    CARD = "CARD"
    OTHER = "OTHER"


class LigneVenteExtraite(BaseModel):
    model_config = ConfigDict(extra="ignore")
    nom_produit: str = Field(description="Nom du produit, doit correspondre exactement à un nom du catalogue fourni.")
    quantite: float = Field(gt=0, description="Quantité vendue.")
    prix_unitaire: Optional[float] = Field(default=None, description="Prix unitaire si mentionné, sinon prix du catalogue.")


class LignePrestationExtraite(BaseModel):
    model_config = ConfigDict(extra="ignore")
    nom_prestation: str = Field(description="Nom de la prestation, doit correspondre exactement à un nom du catalogue fourni.")
    quantite: int = Field(default=1, gt=0)
    duree_minutes: Optional[int] = Field(default=None, description="Durée réelle en minutes si mentionnée.")
class BaseOperationSchema(BaseModel):
    """Schéma de base contenant la structure commune aux transactions."""

    model_config = ConfigDict(extra="ignore")  # tolère les champs superflus hallucinés par le LLM

    transaction_type: Literal["RECETTE", "DEPENSE", "PRET_DONNE", "PRET_RECU"] = Field(
        description=(
            "RECETTE (entrée d'argent classique), DEPENSE (sortie d'argent classique), "
            "PRET_DONNE (l'utilisateur prête de l'argent à quelqu'un), "
            "PRET_RECU (quelqu'un prête de l'argent à l'utilisateur)."
        )
    )
    currency: str = Field(
        default="XOF",
        max_length=5,
        description="Code ISO de la devise (ex: XOF, EUR, USD).",
    )
    category: str = Field(
        description="Catégorie comptable (ex: Loyer, Transport, Restauration, Vente de marchandises)."
    )
    vendor_or_client: Optional[str] = Field(
        default=None,
        description=(
            "Nom de la contrepartie (fournisseur, client, prestataire, ou personne "
            "impliquée dans un prêt donné/reçu)."
        ),
    )
    payment_method: PaymentMethodEnum = Field(
        default=PaymentMethodEnum.CASH,
        description="Moyen de paiement utilisé pour la transaction.",
    )
    transaction_date: date = Field(
        default_factory=date.today,
        description="Date d'exécution au format ISO YYYY-MM-DD (défaut : aujourd'hui si absente/invalide).",
    )
    description: str = Field(description="Synthèse concise décrivant la transaction.")
    confidence_score: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Score d'assurance fourni par l'agent (entre 0.0 et 1.0)."
    )

    @field_validator("transaction_date", mode="before")
    @classmethod
    def parse_flexible_date(cls, value: Any) -> date:
        """
        Parse une date au format ISO renvoyée par le LLM. Si la valeur est absente,
        vide ou dans un format inexploitable, retombe sur la date du jour :
        une transaction sans date précisée est présumée être enregistrée
        au moment où elle survient (cas d'usage WhatsApp/saisie en temps réel).
        """
        if value is None or value == "":
            return date.today()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return datetime.strptime(value.strip(), "%Y-%m-%d").date()
            except ValueError:
                logger.warning("[Schema] Date LLM invalide reçue ('%s'), défaut sur aujourd'hui", value)
                return date.today()
        return date.today()
    
    @model_validator(mode="after")
    def warn_if_pret_without_contact(self) -> "BaseOperationSchema":
        """
        Un prêt sans contrepartie identifiée est structurellement incomplet
        (impossible de suivre qui doit quoi) : on ne bloque pas la validation
        (le LLM peut légitimement ne pas avoir capté le nom), mais on trace
        le cas pour que manager.py sache qu'il devra demander confirmation
        ou laisser `contact` vide côté Operation.
        """
        if self.transaction_type in ("PRET_DONNE", "PRET_RECU") and not self.vendor_or_client:
            logger.warning(
                "[Schema] Transaction de type %s détectée sans contrepartie identifiée (vendor_or_client=None)",
                self.transaction_type,
            )
        return self

class LLMExtractionSchema(BaseOperationSchema):
    """
    Schéma dédié à l'extraction LLM (Ollama/Structured Output).
    Utilise 'float' pour compatibilité avec la génération de grammaire GBNF.
    """

    amount_ttc: float = Field(description="Montant total TTC extrait.")
    amount_ht: Optional[float] = Field(default=None, description="Montant hors taxes si explicite.")
    tax_amount: Optional[float] = Field(default=None, description="Montant de la TVA/Taxe si explicite.")

    def to_parsed_schema(self) -> "ParsedOperationSchema":
        """Transforme l'extraction LLM en schéma comptable strict sans perte de précision Float->Decimal."""
        return ParsedOperationSchema(
            transaction_type=self.transaction_type,
            amount_ttc=Decimal(str(self.amount_ttc)),
            amount_ht=Decimal(str(self.amount_ht)) if self.amount_ht is not None else None,
            tax_amount=Decimal(str(self.tax_amount)) if self.tax_amount is not None else None,
            currency=self.currency,
            category=self.category,
            vendor_or_client=self.vendor_or_client,
            payment_method=self.payment_method,
            transaction_date=self.transaction_date,
            description=self.description,
            confidence_score=self.confidence_score,
            
        )


class ParsedOperationSchema(BaseOperationSchema):
    """
    Structure de données canonique et strictement typée (Decimal)
    utilisée pour la persistence Django et les calculs comptables.
    """

    amount_ttc: Decimal = Field(ge=0, description="Montant total TTC exact (toujours positif ou nul).")
    amount_ht: Optional[Decimal] = Field(default=None, ge=0, description="Montant Hors Taxe exact.")
    tax_amount: Optional[Decimal] = Field(default=None, ge=0, description="Montant de TVA exact.")
    lignes_produits: Optional[list[LigneVenteExtraite]] = Field(default=None, description="Lignes produits (secteur Commerce uniquement).")
    lignes_prestations: Optional[list[LignePrestationExtraite]] = Field(default=None, description="Lignes prestations (secteur Services uniquement).")

    @field_validator("amount_ttc", "amount_ht", "tax_amount", mode="before")
    @classmethod
    def parse_float_or_int_to_decimal(cls, value: Any) -> Optional[Decimal]:
        """Sécurise la conversion en évitant les anomalies d'arrondi binaire des floats."""
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

class LLMExtractionSchemaCommerce(LLMExtractionSchema):
    lignes_produits: list[LigneVenteExtraite] = Field(
        default_factory=list,
        description="Détail des produits vendus/achetés dans cette transaction.",
    )

    def to_parsed_schema(self) -> "ParsedOperationSchema":
        parsed = super().to_parsed_schema()
        parsed.lignes_produits = self.lignes_produits
        return parsed


class LLMExtractionSchemaService(LLMExtractionSchema):
    lignes_prestations: list[LignePrestationExtraite] = Field(
        default_factory=list,
        description="Détail des prestations effectuées dans cette transaction.",
    )

    def to_parsed_schema(self) -> "ParsedOperationSchema":
        parsed = super().to_parsed_schema()
        parsed.lignes_prestations = self.lignes_prestations
        return parsed

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