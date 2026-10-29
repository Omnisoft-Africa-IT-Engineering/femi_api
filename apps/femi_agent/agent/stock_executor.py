"""Agent STOCK : extraction structurée + appels stock_service."""

from __future__ import annotations

import logging
import difflib
from decimal import Decimal

from apps.femi_account.models import Produit
from apps.femi_account.stock_service import (
    MouvementInvalideError,
    ProduitIntrouvableError,
    StockError,
    StockInsuffisantError,
    enregistrer_mouvement,
    resume_stock,
)
from apps.femi_agent.agent.base_executor import BaseAgentExecutionError, StructuredLLMExecutor
from apps.femi_agent.agent.prompts.stock_prompt import STOCK_PROMPT
from apps.femi_agent.schemas.stock import StockExtractionLLMResult, StockExtractionResult
from apps.femi_agent.schemas.tool_loop import FinalAnswerOutput

logger = logging.getLogger(__name__)

STOCK_NUM_CTX = 4096

QUESTIONS = {
    "produit_nom": "C'est pour quel produit ?",
    "quantite": "Quelle quantité ?",
    "wrong_agent": (
        "Pour une vente ou une dépense en argent, dis-le clairement "
        "(ex. « j'ai vendu pour 5000 »). Pour le stock, précise le produit et la quantité."
    ),
}


class StockExecutionError(BaseAgentExecutionError):
    pass


class StockFinalAnswer(FinalAnswerOutput):
    """Réponse stock ; `awaiting_user` = Femi attend une précision de l'utilisateur
    (le routeur enregistre alors l'attente pour fusionner la réponse suivante)."""

    awaiting_user: bool = False


def _resoudre_produit(entreprise, nom: str | None, sku: str | None = None):
    """Retourne (produit, candidats).

    - (produit, []) : une seule correspondance sûre ;
    - (None, [..])  : plusieurs correspondances → on demande, on ne devine pas ;
    - (None, [])    : aucune correspondance.
    """
    qs = Produit.objects.filter(entreprise=entreprise, actif=True)
    if sku:
        p = qs.filter(sku__iexact=sku.strip()).first()
        if p:
            return p, []
    if not nom:
        return None, []
    nom = nom.strip()
    exacts = list(qs.filter(nom__iexact=nom)[:2])
    if len(exacts) == 1:
        return exacts[0], []
    if len(exacts) > 1:
        return None, exacts
    contient = list(qs.filter(nom__icontains=nom).order_by("nom")[:6])
    if len(contient) == 1:
        return contient[0], []
    if len(contient) > 1:
        return None, contient
    # « sacs de riz » alors que le produit s'appelle « Riz »
    bas = nom.casefold()
    inclus = [p for p in qs.order_by("nom")[:500] if p.nom.casefold() in bas]
    if len(inclus) == 1:
        return inclus[0], []
    if len(inclus) > 1:
        return None, inclus[:6]
    return None, []


def _question_choix(nom: str | None, candidats) -> str:
    noms = " / ".join(f"« {p.nom} »" for p in candidats[:5])
    return f"J'ai plusieurs produits pour « {nom} » : {noms}. Lequel est-ce ?"


def _proches(entreprise, nom: str | None) -> list[str]:
    if not nom:
        return []
    noms = list(
        Produit.objects.filter(entreprise=entreprise, actif=True).values_list("nom", flat=True)[:500]
    )
    return difflib.get_close_matches(nom, noms, n=3, cutoff=0.6)


def _etat(p) -> str:
    if p.stock_actuel <= 0:
        return "rupture de stock"
    if p.seuil_alerte and p.seuil_alerte > 0 and p.stock_actuel <= p.seuil_alerte:
        return f"sous le seuil d'alerte ({_fmt(p.seuil_alerte)})"
    return "stock correct"


def _fmt(n) -> str:
    try:
        x = float(n)
    except (TypeError, ValueError):
        return str(n)
    if abs(x - round(x)) < 0.001:
        return f"{int(round(x)):,}".replace(",", " ")
    return f"{x:,.3f}".replace(",", " ").rstrip("0").rstrip(".")


class StockExecutor:
    @classmethod
    def execute(cls, message_text: str, entreprise=None) -> FinalAnswerOutput:
        llm = StructuredLLMExecutor.execute(
            prompt_text=STOCK_PROMPT,
            message_text=message_text,
            output_schema=StockExtractionLLMResult,
            num_ctx=STOCK_NUM_CTX,
            error_cls=StockExecutionError,
            log_prefix="StockExecutor",
        )
        extracted = StockExtractionResult.from_llm(llm)
        return cls._appliquer(extracted, entreprise)

    @classmethod
    async def aexecute(cls, message_text: str, entreprise=None) -> FinalAnswerOutput:
        llm = await StructuredLLMExecutor.aexecute(
            prompt_text=STOCK_PROMPT,
            message_text=message_text,
            output_schema=StockExtractionLLMResult,
            num_ctx=STOCK_NUM_CTX,
            error_cls=StockExecutionError,
            log_prefix="StockExecutor",
        )
        extracted = StockExtractionResult.from_llm(llm)
        return cls._appliquer(extracted, entreprise)

    @classmethod
    def _appliquer(cls, extracted: StockExtractionResult, entreprise) -> FinalAnswerOutput:
        if "wrong_agent" in (extracted.missing_fields or []):
            return StockFinalAnswer(
                step="final_answer",
                answer=QUESTIONS["wrong_agent"],
                key_figures={},
            )

        if extracted.needs_clarification and extracted.missing_fields:
            qs = []
            for code in extracted.missing_fields:
                q = QUESTIONS.get(code)
                if q and q not in qs:
                    qs.append(q)
            if not qs:
                qs.append("Peux-tu préciser le produit et la quantité ?")
            return StockFinalAnswer(
                step="final_answer",
                answer=" ".join(qs),
                key_figures={},
                awaiting_user=True,
            )

        awaiting = False
        try:
            answer = cls._executer_action(extracted, entreprise)
            if isinstance(answer, tuple):
                answer, awaiting = answer
        except StockInsuffisantError as exc:
            answer = str(exc)
        except (ProduitIntrouvableError, MouvementInvalideError, StockError) as exc:
            answer = str(exc)
        except Exception:
            logger.exception("[StockExecutor] échec action=%s", extracted.action)
            answer = "Je n'ai pas pu mettre à jour le stock. Réessaie ou passe par le formulaire."

        return StockFinalAnswer(
            step="final_answer",
            answer=answer,
            key_figures={},
            awaiting_user=awaiting,
        )

    @classmethod
    def _executer_action(cls, extracted: StockExtractionResult, entreprise):
        action = extracted.action

        if action == "RESUME":
            data = resume_stock(entreprise)
            return (
                f"Stock : {data.get('nombre_produits', 0)} produit(s) actif(s). "
                f"Valeur ≈ {_fmt(data.get('valeur_stock', 0))}. "
                f"Ruptures : {data.get('nb_ruptures', 0)}, "
                f"sous seuil d'alerte : {data.get('nb_alertes', 0)}."
            )

        if action == "LISTE":
            produits = list(
                Produit.objects.filter(entreprise=entreprise, actif=True).order_by("nom")[:30]
            )
            if not produits:
                return "Tu n'as encore aucun produit en stock."
            lignes = [
                f"• {p.nom} — {_fmt(p.stock_actuel)} {p.unite}"
                for p in produits
            ]
            return ("Voici tes produits :" + chr(10) + chr(10).join(lignes))

        if action == "STATUT":
            produit, candidats = _resoudre_produit(entreprise, extracted.produit_nom, extracted.sku)
            trouves = [produit] if produit else candidats
            if not trouves:
                proches = _proches(entreprise, extracted.produit_nom)
                if proches:
                    return (
                        f"Je ne trouve pas « {extracted.produit_nom} ». "
                        f"Tu voulais dire : {', '.join(proches)} ?",
                        True,
                    )
                return f"Je ne trouve pas « {extracted.produit_nom} » dans ton stock."
            return "\n".join(
                f"• {p.nom} : {_fmt(p.stock_actuel)} {p.unite} — {_etat(p)}" for p in trouves
            )

        if action == "CREATE_PRODUIT":
            existant = None
            qs_actifs = Produit.objects.filter(entreprise=entreprise, actif=True)
            if extracted.sku:
                existant = qs_actifs.filter(sku__iexact=extracted.sku.strip()).first()
            if existant is None and extracted.produit_nom:
                existant = qs_actifs.filter(nom__iexact=extracted.produit_nom.strip()).first()
            if existant is not None:
                return (
                    f"Le produit « {extracted.produit_nom} » existe déjà. "
                    "Dis-moi une entrée ou une sortie de stock pour le mettre à jour."
                )
            p = Produit.objects.create(
                entreprise=entreprise,
                nom=extracted.produit_nom,
                sku=extracted.sku or "",
                unite=extracted.unite or "pièce",
                prix_vente=Decimal(str(extracted.prix_vente or 0)),
                seuil_alerte=Decimal(str(extracted.seuil_alerte or 0)),
            )
            return (
                f"Produit « {p.nom} » créé "
                f"(unité : {p.unite}). Stock à 0 — ajoute une entrée si besoin."
            )

        # ENTREE / SORTIE / AJUSTEMENT
        produit, candidats = _resoudre_produit(entreprise, extracted.produit_nom, extracted.sku)
        if candidats:
            return _question_choix(extracted.produit_nom, candidats), True
        if produit is None and action == "ENTREE":
            # Création auto à l'entrée si le produit n'existe pas
            produit = Produit.objects.create(
                entreprise=entreprise,
                nom=extracted.produit_nom,
                sku=extracted.sku or "",
                unite=extracted.unite or "pièce",
                prix_vente=Decimal(str(extracted.prix_vente or 0)),
            )
            auto = True
        else:
            auto = False

        if produit is None:
            return (
                f"Je ne trouve pas le produit « {extracted.produit_nom} ». "
                "Crée-le d'abord ou vérifie le nom."
            )

        type_m = action  # ENTREE | SORTIE | AJUSTEMENT
        motif = {
            "ENTREE": "ACHAT",
            "SORTIE": "VENTE",
            "AJUSTEMENT": "INVENTAIRE",
        }.get(action)

        mouvement, _cree = enregistrer_mouvement(
            entreprise=entreprise,
            produit_id=produit.pk,
            type_mouvement=type_m,
            quantite=extracted.quantite,
            motif=motif,
            cout_unitaire=extracted.cout_unitaire,
            note=extracted.note or "",
        )
        produit.refresh_from_db()
        prefix = "Produit créé et " if auto else ""
        return (
            f"{prefix}{type_m.capitalize()} enregistrée pour « {produit.nom} » : "
            f"{_fmt(extracted.quantite)} {produit.unite}. "
            f"Stock actuel : {_fmt(produit.stock_actuel)} {produit.unite}."
        )