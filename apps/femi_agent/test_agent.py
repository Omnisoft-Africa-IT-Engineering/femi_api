import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from apps.femi_agent.agent_manager import FemiAgentManager

res1 = FemiAgentManager.process_transaction_text(
    text_input="Achat de carburant pour la livraison 12000 FCFA par Flooz",
    source="TEST"
)
print("--- TEST 1 (Dépense) ---")
print(res1.message)

res2 = FemiAgentManager.process_transaction_text(
    text_input="Vente de 2 sacs de riz à Koffi pour 45000 FCFA cash",
    source="TEST"
)
print("\n--- TEST 2 (Recette) ---")
print(res2.message)