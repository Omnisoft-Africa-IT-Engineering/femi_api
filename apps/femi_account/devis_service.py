"""Création de devis (utilisée par l'agent QUOTE et par l'API REST).

Règles :
- le backend calcule tous les montants (jamais le LLM) ;
- le client est retrouvé par son nom (insensible à la casse) dans les
  contacts de l'entreprise, ou créé comme CLIENT s'il n'existe pas
  (même règle que l'enregistrement des opérations comptables) ;
- l'entreprise vient toujours de l'utilisateur connecté, jamais de la requête.
"""

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from django.db import transaction

from .models import Contact, Devis, LigneDevis

logger = logging.getLogger(__name__)

_CENTIMES = Decimal("0.01")


class DevisInvalideError(ValueError):
    """Données de devis inexploitables (ligne vide, quantité ou prix invalide)."""


def _vers_decimal(valeur, nom_champ: str) -> Decimal:
    try:
        nombre = Decimal(str(valeur))
    except (InvalidOperation, ValueError, TypeError):
        raise DevisInvalideError(f"{nom_champ} invalide : {valeur!r}")
    if not nombre.is_finite():
        raise DevisInvalideError(f"{nom_champ} invalide : {valeur!r}")
    return nombre.quantize(_CENTIMES)


def resoudre_client(entreprise, nom_client: Optional[str]) -> Optional[Contact]:
    """Retrouve le contact client par son nom, ou le crée."""
    nom = (nom_client or "").strip()
    if not nom:
        return None
    contact = Contact.objects.filter(entreprise=entreprise, nom__iexact=nom).first()
    if contact is not None:
        return contact
    return Contact.objects.create(entreprise=entreprise, nom=nom, type="CLIENT")


def normaliser_lignes(lignes: Iterable) -> list[dict]:
    """Valide les lignes et calcule le montant de chacune.

    Chaque ligne peut être un dict ou un objet avec les attributs
    description, quantite, prix_unitaire.
    """
    resultat = []
    for brute in lignes:
        get = brute.get if isinstance(brute, dict) else lambda k, d=None: getattr(brute, k, d)
        description = (get("description") or "").strip()
        if not description:
            raise DevisInvalideError("Une ligne du devis n'a pas de description.")
        quantite = _vers_decimal(get("quantite", 1), "quantité")
        prix = _vers_decimal(get("prix_unitaire", 0), "prix unitaire")
        if quantite <= 0:
            raise DevisInvalideError(f"Quantité invalide pour « {description} ».")
        if prix < 0:
            raise DevisInvalideError(f"Prix invalide pour « {description} ».")
        resultat.append(
            {
                "description": description,
                "quantite": quantite,
                "prix_unitaire": prix,
                "montant_total": (quantite * prix).quantize(_CENTIMES),
            }
        )
    if not resultat:
        raise DevisInvalideError("Un devis doit contenir au moins une ligne.")
    return resultat


@transaction.atomic
def creer_devis(
    entreprise,
    lignes: Iterable,
    client_nom: Optional[str] = None,
    client: Optional[Contact] = None,
    date_validite: Optional[date] = None,
) -> Devis:
    """Crée un devis BROUILLON avec ses lignes, en une transaction."""
    lignes_ok = normaliser_lignes(lignes)

    if client is not None and client.entreprise_id != entreprise.id:
        raise DevisInvalideError("Ce client n'appartient pas à votre entreprise.")
    if client is None:
        client = resoudre_client(entreprise, client_nom)

    devis = Devis.objects.create(
        entreprise=entreprise,
        client=client,
        date_validite=date_validite,
    )
    LigneDevis.objects.bulk_create(
        [LigneDevis(devis=devis, **ligne) for ligne in lignes_ok]
    )
    devis.calculer_total()

    logger.info(
        "[DevisService] Devis créé : reference=%s entreprise=%s lignes=%s total=%s",
        devis.reference,
        entreprise.id,
        len(lignes_ok),
        devis.montant_total,
    )
    return devis
