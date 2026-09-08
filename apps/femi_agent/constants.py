"""
Constantes partagées de l'agent Femi (patterns de détection d'intention).

Centralisées ici pour être réutilisables depuis manager.py, pipeline.py,
ou tout autre module de l'agent sans dépendance circulaire.
"""

import re

# Détecte une salutation simple en début de message (max 3 mots dans l'appelant)
GREETING_PATTERN = re.compile(
    r"^(salut|bonjour|hello|coucou|bonsoir|ca va|ça va|sava)\b",
    re.IGNORECASE,
)

# Détecte une intention de requête analytique (bilan, chiffre d'affaires, etc.)
ANALYTICAL_PATTERN = re.compile(
    r"\b(chiffre d'affaires?|combien|bilan|résumé|rapport|solde|statistique|total)\b",
    re.IGNORECASE,
)