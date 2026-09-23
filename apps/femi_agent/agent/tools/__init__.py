"""Package tools de l'agent Femi (nouveau système Router).

Ré-exporte les fonctions utilisées telles quelles ailleurs dans le code, pour
que `from apps.femi_agent.agent.tools import <nom>` fonctionne — jusqu'ici ce
dossier n'avait pas de __init__.py (namespace package implicite), ce qui
suffisait pour les imports de sous-module entier (ex: `import financials`)
mais pas pour importer une fonction précise directement depuis le package.
"""

from apps.femi_agent.agent.tools.debts import get_contact_open_debts, get_contact_open_loans

__all__ = ["get_contact_open_debts", "get_contact_open_loans"]