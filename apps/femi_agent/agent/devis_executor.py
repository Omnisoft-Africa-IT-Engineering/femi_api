import json
import logging
from .prompts.devis_prompt import DEVIS_SYSTEM_PROMPT
from .llm import get_llm

logger = logging.getLogger(__name__)

def run_devis_sub_agent(texte_utilisateur, profile_pme):
    """
    Exécuteur du Sous-Agent Devis.
    Injecte le PMEProfile dans le prompt système et interroge Gemini avec réponse JSON structurée.
    """

    # 1. Formatage dynamique du prompt avec le profil de l'entreprise
    prompt_formate = DEVIS_SYSTEM_PROMPT.format(
        nom_commercial=profile_pme.nom_commercial if profile_pme else 'Entreprise',
        telephone_pro=getattr(profile_pme, 'telephone_pro', None) or 'Non renseigné',
        adresse=getattr(profile_pme, 'adresse', None) or 'Non renseignée',
        nif_rccm=getattr(profile_pme, 'nif_rccm', None) or 'Non renseigné',
        moyens_paiement=getattr(profile_pme, 'moyens_paiement', None) or 'Mobile Money / Espèces',
        conditions_defaut=getattr(profile_pme, 'conditions_defaut', None) or 'Paiement à la réception'
    )

    # 2. Instanciation du modèle Gemini via LangChain
    model = get_llm()

    prompt_final = f"{prompt_formate}\n\n[Message utilisateur - Petit Chat] : \"{texte_utilisateur}\""

    # 3. Appel de l'API Gemini
    response = model.invoke(prompt_final)

    # 4. Extraction sécurisée du texte depuis response.content (si string ou liste)
    if isinstance(response.content, list):
        texte_reponse = "".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in response.content
        )
    else:
        texte_reponse = str(response.content)

    # 5. Nettoyage des balises Markdown ```json
    texte_reponse = texte_reponse.strip()
    if texte_reponse.startswith("```json"):
        texte_reponse = texte_reponse[7:]
    elif texte_reponse.startswith("```"):
        texte_reponse = texte_reponse[3:]

    if texte_reponse.endswith("```"):
        texte_reponse = texte_reponse[:-3]

    texte_reponse = texte_reponse.strip()

    # 6. Parsing sécurisé du JSON
    try:
        return json.loads(texte_reponse)
    except json.JSONDecodeError as e:
        logger.error(f"Erreur de parsing JSON depuis Gemini : {e}. Réponse brute : {texte_reponse}")
        return {
            "error": True,
            "message": "La réponse générée par l'agent n'est pas au format JSON valide.",
            "raw_response": texte_reponse
        }