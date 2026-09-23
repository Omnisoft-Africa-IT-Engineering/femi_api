"""Catégories disponibles par tenant, partagées entre AccountingExecutor et
AccountingModifyExecutor (même TODO Point 6 dans les deux, résolu ici une
seule fois — même logique de factorisation que tools/periodes.py).

Source de vérité : modèle Categorie (apps/femi_account/models.py), lié à
Entreprise par ForeignKey, avec contrainte unique_together sur
(entreprise, nom, type). Le format groupé par type ci-dessous préserve
cette distinction RECETTE/DEPENSE, qu'un simple aplatissement en liste
perdrait -- une même catégorie textuelle peut exister sous les deux types
pour une même entreprise.
"""


def get_categories_disponibles(entreprise) -> str:
    """
    Construit la représentation texte des catégories disponibles pour ce
    tenant, groupées par type (RECETTE / DEPENSE), à injecter dans
    {categories_disponibles} (accounting_prompt.py, accounting_modify_prompt.py).

    Args:
        entreprise: instance Entreprise (jamais résolue en interne).

    Returns:
        Texte formaté, une ligne par type non vide :
            "RECETTE : Ventes marchandises, Prestations de services
DEPENSE : Achat marchandises, Loyer"
        Chaîne vide si aucune catégorie n'est déclarée pour ce tenant
        (cas réel : entreprises créées hors du seed de base, voir
        TestServices SARL au moment de l'écriture de ce module).
    """
    categories = entreprise.categories.all().order_by("type", "nom")

    par_type: dict[str, list[str]] = {}
    for c in categories:
        par_type.setdefault(c.type, []).append(c.nom)

    lignes = [f"{type_}  : {', '.join(noms)}" for type_, noms in par_type.items()]
    return "\n".join(lignes)
