"""Tests de robustesse face aux réponses de LLM sous forme de blocs (Gemini) et aux quotas."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.femi_agent.agent.base_executor import _content_to_text
from apps.femi_agent.agent.prompts import eval_prompts
from apps.femi_agent.agent.router_executor import RouterExecutor

ROUTAGE_VALIDE = {
    "intents": [
        {
            "intent_id": "i1",
            "agent": "FINANCIAL_ANALYST",
            "action_type": "READ",
            "confidence": 0.9,
            "is_sensitive": False,
            "requires_confirmation": False,
            "requires_tool": True,
            "needs_clarification": False,
            "missing_fields": [],
            "merge_context": False,
            "context_resolved": True,
            "raw_segment": "Combien j'ai gagné ce mois-ci ?",
        }
    ]
}


class ContenuEnBlocsTests(SimpleTestCase):
    def test_conversion_du_contenu(self):
        self.assertEqual(_content_to_text("texte"), "texte")
        self.assertEqual(_content_to_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]), "a\nb")
        self.assertEqual(_content_to_text(["a", {"type": "text", "text": "b"}]), "a\nb")

    def test_secours_du_router_accepte_un_contenu_en_blocs(self):
        faux_llm = SimpleNamespace(
            invoke=lambda _messages: SimpleNamespace(
                content=[{"type": "text", "text": json.dumps(ROUTAGE_VALIDE)}]
            )
        )
        with patch("apps.femi_agent.agent.router_executor.get_llm", return_value=faux_llm):
            sortie = RouterExecutor._fallback_parse("prompt", "Combien j'ai gagné ce mois-ci ?")
        self.assertEqual(sortie.intents[0].agent, "FINANCIAL_ANALYST")

    def test_evaluation_lit_un_contenu_en_blocs(self):
        reponse = SimpleNamespace(content=[{"type": "text", "text": '{"ok": true}'}])
        texte = eval_prompts._reponse_en_texte(reponse)
        self.assertEqual(eval_prompts.extract_json(texte), {"ok": True})


class QuotaTests(SimpleTestCase):
    def test_reprise_apres_un_429(self):
        appels = []

        def invoke(_messages):
            appels.append(1)
            if len(appels) < 3:
                raise RuntimeError("429 RESOURCE_EXHAUSTED ... Please retry in 3.5s.")
            return SimpleNamespace(content="ok")

        pauses = []
        with patch.object(eval_prompts.time, "sleep", side_effect=pauses.append):
            reponse = eval_prompts._appeler_llm(SimpleNamespace(invoke=invoke), [])
        self.assertEqual(reponse.content, "ok")
        self.assertEqual(len(appels), 3)
        self.assertEqual(pauses, [5.5, 5.5])  # délai du fournisseur + marge de 2 s

    def test_une_autre_erreur_n_est_pas_retentee(self):
        def invoke(_messages):
            raise RuntimeError("clé invalide")

        with patch.object(eval_prompts.time, "sleep") as pause:
            with self.assertRaises(RuntimeError):
                eval_prompts._appeler_llm(SimpleNamespace(invoke=invoke), [])
        pause.assert_not_called()

    def test_abandon_apres_le_nombre_maximal_d_essais(self):
        def invoke(_messages):
            raise RuntimeError("429 RESOURCE_EXHAUSTED retry in 1s")

        with patch.object(eval_prompts.time, "sleep"):
            with self.assertRaises(RuntimeError):
                eval_prompts._appeler_llm(SimpleNamespace(invoke=invoke), [], essais=2)
