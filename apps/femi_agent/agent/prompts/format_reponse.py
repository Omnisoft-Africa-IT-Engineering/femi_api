"""Règles de mise en forme communes aux réponses des agents (champ « answer »)."""

FORMAT_REPONSE = """

### MISE EN FORME DE LA RÉPONSE (champ answer uniquement)
- Commence par UNE phrase d'accroche qui répond à la question (avec la période si utile).
- Dès qu'il y a 2 éléments ou plus (clients, opérations, produits, catégories), écris une liste : une ligne par élément, qui commence par "• " (jamais un paragraphe compact).
- Un élément = une ligne courte, par exemple « • Koffi — 5 000 FCFA ». Un montant par ligne.
- Mets en gras avec ** ** les montants importants, les totaux et les pourcentages, par exemple **5 000 FCFA**. Aucun autre gras.
- Saute une ligne entre l'accroche, la liste et la conclusion. Conclusion facultative : une phrase au maximum.
- Écris les montants avec un espace pour les milliers, suivis de FCFA, exactement comme dans les résultats (ne recalcule rien).
- Pas de titres avec #, pas de tableaux, 1 emoji au maximum.
Produis UNIQUEMENT le JSON.
"""
