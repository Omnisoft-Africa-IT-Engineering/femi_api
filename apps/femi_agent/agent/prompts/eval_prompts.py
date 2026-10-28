#!/usr/bin/env python3
"""
Script d'évaluation des prompts optimisés Femi.

Usage :
  # Mode offline (affiche les cas de test + attentes)
  python -m apps.femi_agent.agent.prompts.eval_prompts

  # Mode LLM (nécessite GROQ_API_KEY / MISTRAL_API_KEY / etc. + Django settings)
  python -m apps.femi_agent.agent.prompts.eval_prompts --llm

  # Uniquement un agent
  python -m apps.femi_agent.agent.prompts.eval_prompts --agent router
  python -m apps.femi_agent.agent.prompts.eval_prompts --agent accounting --llm
"""

from __future__ import annotations

import argparse
import time
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Cas de test
# ---------------------------------------------------------------------------

@dataclass
class TestCase:
    id: str
    agent: str  # router | accounting | customer | financial | modify
    message: str
    history: list[str] = field(default_factory=list)
    # Attentes (souples pour permettre une évaluation manuelle ou semi-auto)
    expected_agent: Optional[str] = None          # pour router
    expected_action: Optional[str] = None         # CREATE / READ / UPDATE / DELETE
    expected_transaction_type: Optional[str] = None  # pour accounting
    expected_check_open_loan: Optional[bool] = None
    expected_check_open_debt: Optional[bool] = None
    expected_statut: Optional[str] = None         # PAYE | CREDIT
    expected_needs_clarification: Optional[bool] = None
    expected_missing_fields_contains: list[str] = field(default_factory=list)
    notes: str = ""


ROUTER_CASES: list[TestCase] = [
    TestCase(
        id="R01",
        agent="router",
        message="Koffi m'a donné 30 000",
        expected_agent="ACCOUNTING",
        expected_action="CREATE",
        notes="Encaissement simple sans mention de dette → ACCOUNTING",
    ),
    TestCase(
        id="R02",
        agent="router",
        message="Koffi a payé 20 000 sur sa dette",
        expected_agent="CUSTOMER",
        expected_action="CREATE",
        notes="Paiement explicite de dette client → CUSTOMER",
    ),
    TestCase(
        id="R03",
        agent="router",
        message="Koffi m'a remboursé les 50 000 que je lui avais prêtés",
        expected_agent="ACCOUNTING",
        expected_action="CREATE",
        notes="Remboursement de prêt → ACCOUNTING",
    ),
    TestCase(
        id="R04",
        agent="router",
        message="Il m'a remboursé",
        history=["J'ai prêté 40 000 à Paul"],
        expected_agent="ACCOUNTING",
        expected_action="CREATE",
        notes="Pronom résolu via historique (prêt)",
    ),
    TestCase(
        id="R05",
        agent="router",
        message="Il m'a remboursé",
        history=[],
        expected_needs_clarification=True,
        notes="Pronom sans historique → clarification",
    ),
    TestCase(
        id="R06",
        agent="router",
        message="Modifie la dépense de transport de 8 000 à 10 000",
        expected_agent="ACCOUNTING_MODIFY",
        expected_action="UPDATE",
        notes="Modification d'opération existante",
    ),
    TestCase(
        id="R07",
        agent="router",
        message="J'ai vendu 2 sacs à Koffi à crédit et j'ai payé 5 000 de transport",
        expected_agent="ACCOUNTING",
        expected_action="CREATE",
        notes="Multi-opérations → au moins un intent ACCOUNTING/CREATE",
    ),
    TestCase(
        id="R08",
        agent="router",
        message="Combien j'ai gagné ce mois-ci ?",
        expected_agent="FINANCIAL_ANALYST",
        expected_action="READ",
        expected_needs_clarification=True,
        notes="'gagné' est ambigu (bénéfice vs CA)",
    ),
    TestCase(
        id="R09",
        agent="router",
        message="Combien Koffi me doit ?",
        expected_agent="CUSTOMER",
        expected_action="READ",
    ),
    TestCase(
        id="R10",
        agent="router",
        message="Fais un devis pour Koffi : 2 caméras à 125000",
        expected_agent="QUOTE",
        expected_action="CREATE",
    ),
]

ACCOUNTING_CASES: list[TestCase] = [
    TestCase(
        id="A01",
        agent="accounting",
        message="J'ai vendu 3 chemises à 5 000 chacune à Koffi en espèces",
        expected_transaction_type="RECETTE",
        expected_statut="PAYE",
        expected_check_open_debt=False,
        expected_check_open_loan=False,
        notes="quantity + unit_price, amount_ttc peut être null",
    ),
    TestCase(
        id="A02",
        agent="accounting",
        message="J'ai prêté 50 000 à Paul",
        expected_transaction_type="PRET_DONNE",
        expected_statut="CREDIT",
        expected_check_open_loan=False,
    ),
    TestCase(
        id="A03",
        agent="accounting",
        message="Koffi m'a remboursé les 30 000 que je lui avais prêtés",
        expected_transaction_type="RECETTE",
        expected_statut="PAYE",
        expected_check_open_loan=True,
        notes="Remboursement de prêt donné → RECETTE + check_open_loan",
    ),
    TestCase(
        id="A04",
        agent="accounting",
        message="J'ai vendu une chemise à Koffi pour 10 000 à crédit",
        expected_transaction_type="RECETTE",
        expected_statut="CREDIT",
        expected_check_open_debt=False,
        notes="Nouvelle créance, pas un remboursement",
    ),
    TestCase(
        id="A05",
        agent="accounting",
        message="J'ai donné 25 000 à Ama, elle doit me rembourser",
        expected_transaction_type="PRET_DONNE",
        expected_statut="CREDIT",
    ),
    TestCase(
        id="A06",
        agent="accounting",
        message="J'ai payé 15 000 de transport et j'ai vendu 2 sacs à 8 000",
        expected_transaction_type="DEPENSE",  # au moins une DEPENSE
        notes="Deux opérations attendues",
    ),
    TestCase(
        id="A07",
        agent="accounting",
        message="J'ai emprunté 100 000 à Jean",
        expected_transaction_type="PRET_RECU",
        expected_statut="CREDIT",
    ),
]

CUSTOMER_CASES: list[TestCase] = [
    TestCase(
        id="C01",
        agent="customer",
        message="Combien me doit Koffi ?",
        expected_action="READ",
        notes="Doit sélectionner get_contact_open_debts",
    ),
    TestCase(
        id="C02",
        agent="customer",
        message="Qui me doit de l'argent ?",
        expected_action="READ",
        notes="get_all_open_debts",
    ),
    TestCase(
        id="C03",
        agent="customer",
        message="Koffi a payé 20 000 sur sa dette",
        expected_action="CREATE",
        expected_needs_clarification=False,
    ),
    TestCase(
        id="C04",
        agent="customer",
        message="Koffi m'a remboursé le prêt de 50 000",
        expected_needs_clarification=True,
        expected_missing_fields_contains=["wrong_agent"],
        notes="C'est un prêt → doit signaler wrong_agent",
    ),
    TestCase(
        id="C05",
        agent="customer",
        message="J'ai reçu 30 000 de Koffi",
        expected_needs_clarification=True,
        expected_missing_fields_contains=["wrong_agent"],
        notes="Pas de mention de dette → wrong_agent",
    ),
    TestCase(
        id="C06",
        agent="customer",
        message="Il a payé 15 000",
        history=["Koffi me doit 40 000 pour la commande"],
        expected_action="CREATE",
        expected_needs_clarification=False,
        notes="Pronom résolu via historique (dette client)",
    ),
]

FINANCIAL_CASES: list[TestCase] = [
    TestCase(
        id="F01",
        agent="financial",
        message="Quel est mon chiffre d'affaires ce mois-ci ?",
        expected_action="READ",
        expected_needs_clarification=False,
        notes="get_revenue(periode=ce_mois)",
    ),
    TestCase(
        id="F02",
        agent="financial",
        message="Où va mon argent ce mois-ci ?",
        expected_needs_clarification=False,
        notes="detail_par_categorie=true",
    ),
    TestCase(
        id="F03",
        agent="financial",
        message="Combien ai-je gagné ce mois-ci ?",
        expected_needs_clarification=True,
        expected_missing_fields_contains=["indicateur"],
    ),
    TestCase(
        id="F04",
        agent="financial",
        message="Quelle est ma rentabilité par produit ?",
        expected_needs_clarification=True,
        expected_missing_fields_contains=["fonctionnalite_non_disponible"],
    ),
    TestCase(
        id="F05",
        agent="financial",
        message="Combien Koffi me doit ?",
        expected_needs_clarification=True,
        expected_missing_fields_contains=["wrong_agent"],
    ),
]

MODIFY_CASES: list[TestCase] = [
    TestCase(
        id="M01",
        agent="modify",
        message="Supprime la dépense de transport de 2 000",
        expected_action="DELETE",
        expected_needs_clarification=False,
        notes="Recherche avec amount_ttc=2000 + keywords=transport",
    ),
    TestCase(
        id="M02",
        agent="modify",
        message="Modifie ma dépense de 10 000 à 12 000",
        expected_action="UPDATE",
        notes="amount_ttc dans search = 10000 (montant actuel)",
    ),
    TestCase(
        id="M03",
        agent="modify",
        message="Supprime la transaction",
        expected_needs_clarification=True,
        expected_missing_fields_contains=["search_criteria"],
    ),
]

ALL_CASES = ROUTER_CASES + ACCOUNTING_CASES + CUSTOMER_CASES + FINANCIAL_CASES + MODIFY_CASES


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def extract_json(text: str) -> Optional[dict | list]:
    """Extrait le premier objet/array JSON d'une réponse LLM."""
    text = text.strip()
    # Enlever éventuels blocs markdown
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Cherche le premier { ... } ou [ ... ]
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = text.find(start_char)
            if start == -1:
                continue
            depth = 0
            for i, c in enumerate(text[start:], start):
                if c == start_char:
                    depth += 1
                elif c == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break
        return None


def _reponse_en_texte(response: Any) -> str:
    """Texte d'une réponse LLM, que `content` soit une chaîne ou une liste de blocs (Gemini)."""
    from apps.femi_agent.agent.base_executor import _content_to_text

    contenu = response.content if hasattr(response, "content") else str(response)
    return _content_to_text(contenu)


_RE_ATTENTE = re.compile(r"retry in ([0-9.]+)s|retryDelay['\"]?: ?['\"]([0-9.]+)s", re.IGNORECASE)


def _appeler_llm(llm: Any, messages: list, essais: int = 4):
    """llm.invoke() avec reprise automatique quand le quota du fournisseur est atteint (429).

    Attend le délai indiqué par le fournisseur (« retry in 23s »), plus une marge,
    puis réessaie. Toute autre erreur est relancée telle quelle.
    """
    for tentative in range(1, essais + 1):
        try:
            return llm.invoke(messages)
        except Exception as exc:  # noqa: BLE001
            texte = str(exc)
            quota = "429" in texte or "RESOURCE_EXHAUSTED" in texte
            if not quota or tentative == essais:
                raise
            trouve = _RE_ATTENTE.search(texte)
            attente = float(next(g for g in trouve.groups() if g)) if trouve else 20.0
            attente += 2.0
            print(f"  ⏳ Quota atteint, nouvelle tentative dans {attente:.0f}s ({tentative}/{essais - 1})")
            time.sleep(attente)


def evaluate_router(result: Any, case: TestCase) -> tuple[bool, list[str]]:
    errors = []
    if not isinstance(result, dict):
        return False, ["Réponse n'est pas un dict"]
    intents = result.get("intents") or result.get("intent") or []
    if isinstance(intents, dict):
        intents = [intents]
    if not intents:
        return False, ["Aucun intent trouvé"]

    first = intents[0]
    if case.expected_agent and first.get("agent") != case.expected_agent:
        errors.append(f"agent: attendu {case.expected_agent}, reçu {first.get('agent')}")
    if case.expected_action and first.get("action_type") != case.expected_action:
        errors.append(f"action_type: attendu {case.expected_action}, reçu {first.get('action_type')}")
    if case.expected_needs_clarification is not None:
        got = first.get("needs_clarification", False)
        if got != case.expected_needs_clarification:
            errors.append(f"needs_clarification: attendu {case.expected_needs_clarification}, reçu {got}")
    return len(errors) == 0, errors


def evaluate_accounting(result: Any, case: TestCase) -> tuple[bool, list[str]]:
    errors = []
    if not isinstance(result, dict):
        return False, ["Réponse n'est pas un dict"]
    ops = result.get("operations") or []
    if not ops and case.expected_transaction_type:
        return False, ["Aucune opération extraite"]
    if ops:
        op = ops[0]
        if case.expected_transaction_type and op.get("transaction_type") != case.expected_transaction_type:
            # Pour multi-ops, vérifier si au moins une correspond
            types = [o.get("transaction_type") for o in ops]
            if case.expected_transaction_type not in types:
                errors.append(f"transaction_type: attendu {case.expected_transaction_type}, reçu {types}")
        if case.expected_statut and op.get("statut_paiement") != case.expected_statut:
            errors.append(f"statut_paiement: attendu {case.expected_statut}, reçu {op.get('statut_paiement')}")
        if case.expected_check_open_loan is not None and op.get("check_open_loan") != case.expected_check_open_loan:
            errors.append(f"check_open_loan: attendu {case.expected_check_open_loan}, reçu {op.get('check_open_loan')}")
        if case.expected_check_open_debt is not None and op.get("check_open_debt") != case.expected_check_open_debt:
            errors.append(f"check_open_debt: attendu {case.expected_check_open_debt}, reçu {op.get('check_open_debt')}")
    if case.expected_needs_clarification is not None:
        got = result.get("needs_clarification", False)
        if got != case.expected_needs_clarification:
            errors.append(f"needs_clarification: attendu {case.expected_needs_clarification}, reçu {got}")
    return len(errors) == 0, errors


def evaluate_customer(result: Any, case: TestCase) -> tuple[bool, list[str]]:
    errors = []
    if not isinstance(result, dict):
        return False, ["Réponse n'est pas un dict"]
    if case.expected_action and result.get("action_type") != case.expected_action:
        errors.append(f"action_type: attendu {case.expected_action}, reçu {result.get('action_type')}")
    if case.expected_needs_clarification is not None:
        got = result.get("needs_clarification", False)
        if got != case.expected_needs_clarification:
            errors.append(f"needs_clarification: attendu {case.expected_needs_clarification}, reçu {got}")
    if case.expected_missing_fields_contains:
        missing = result.get("missing_fields") or []
        for field in case.expected_missing_fields_contains:
            if field not in missing:
                errors.append(f"missing_fields devrait contenir '{field}', reçu {missing}")
    return len(errors) == 0, errors


def evaluate_financial(result: Any, case: TestCase) -> tuple[bool, list[str]]:
    return evaluate_customer(result, case)  # même forme générale


def evaluate_modify(result: Any, case: TestCase) -> tuple[bool, list[str]]:
    errors = []
    if not isinstance(result, dict):
        return False, ["Réponse n'est pas un dict"]
    if case.expected_action and result.get("action_type") != case.expected_action:
        errors.append(f"action_type: attendu {case.expected_action}, reçu {result.get('action_type')}")
    if case.expected_needs_clarification is not None:
        got = result.get("needs_clarification", False)
        if got != case.expected_needs_clarification:
            errors.append(f"needs_clarification: attendu {case.expected_needs_clarification}, reçu {got}")
    if case.expected_missing_fields_contains:
        missing = result.get("missing_fields") or []
        for field in case.expected_missing_fields_contains:
            if field not in missing:
                errors.append(f"missing_fields devrait contenir '{field}', reçu {missing}")
    return len(errors) == 0, errors


EVALUATORS = {
    "router": evaluate_router,
    "accounting": evaluate_accounting,
    "customer": evaluate_customer,
    "financial": evaluate_financial,
    "modify": evaluate_modify,
}


# ---------------------------------------------------------------------------
# Affichage offline
# ---------------------------------------------------------------------------

def print_offline_suite(agent_filter: Optional[str] = None):
    print("=" * 70)
    print("SUITE D'ÉVALUATION DES PROMPTS FEMI (mode offline)")
    print("=" * 70)
    cases = ALL_CASES
    if agent_filter:
        cases = [c for c in cases if c.agent == agent_filter]

    by_agent: dict[str, list[TestCase]] = {}
    for c in cases:
        by_agent.setdefault(c.agent, []).append(c)

    for agent, agent_cases in by_agent.items():
        print(f"\n### {agent.upper()} ({len(agent_cases)} cas)\n")
        for c in agent_cases:
            print(f"  [{c.id}] {c.message}")
            if c.history:
                print(f"       historique: {c.history}")
            expectations = []
            if c.expected_agent:
                expectations.append(f"agent={c.expected_agent}")
            if c.expected_action:
                expectations.append(f"action={c.expected_action}")
            if c.expected_transaction_type:
                expectations.append(f"type={c.expected_transaction_type}")
            if c.expected_statut:
                expectations.append(f"statut={c.expected_statut}")
            if c.expected_check_open_loan is not None:
                expectations.append(f"check_open_loan={c.expected_check_open_loan}")
            if c.expected_check_open_debt is not None:
                expectations.append(f"check_open_debt={c.expected_check_open_debt}")
            if c.expected_needs_clarification is not None:
                expectations.append(f"needs_clarification={c.expected_needs_clarification}")
            if c.expected_missing_fields_contains:
                expectations.append(f"missing⊇{c.expected_missing_fields_contains}")
            if expectations:
                print(f"       → attendu: {', '.join(expectations)}")
            if c.notes:
                print(f"       note: {c.notes}")
            print()

    print(f"Total: {len(cases)} cas de test")
    print("\nPour lancer avec un vrai LLM :")
    print("  python -m apps.femi_agent.agent.prompts.eval_prompts --llm")
    print("  (nécessite les clés API + environnement Django)")


# ---------------------------------------------------------------------------
# Mode LLM (optionnel)
# ---------------------------------------------------------------------------

def run_llm_evaluation(agent_filter: Optional[str] = None, delai: float = 4.5):
    """Tente d'appeler le LLM réel via le code du projet."""
    try:
        import django
        import os
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
        django.setup()
    except Exception as e:
        print(f"❌ Impossible d'initialiser Django : {e}")
        print("   Lance ce script depuis la racine du projet avec le venv activé.")
        return False

    try:
        from apps.femi_agent.agent.prompts import (
            ROUTER_PROMPT,
            ACCOUNTING_PROMPT,
            CUSTOMER_PROMPT,
            FINANCIAL_ANALYST_PROMPT,
            ACCOUNTING_MODIFY_PROMPT,
        )
        from apps.femi_agent.agent.llm import get_llm
        from langchain_core.messages import SystemMessage, HumanMessage
    except Exception as e:
        print(f"❌ Import échoué : {e}")
        return False

    prompt_map = {
        "router": ROUTER_PROMPT,
        "accounting": ACCOUNTING_PROMPT,
        "customer": CUSTOMER_PROMPT,
        "financial": FINANCIAL_ANALYST_PROMPT,
        "modify": ACCOUNTING_MODIFY_PROMPT,
    }

    cases = ALL_CASES
    if agent_filter:
        cases = [c for c in cases if c.agent == agent_filter]

    print("=" * 70)
    print(f"ÉVALUATION LLM — {len(cases)} cas (pause de {delai:.1f}s entre les appels)")
    print("=" * 70)

    try:
        llm = get_llm(temperature=0.0)
    except Exception as e:
        print(f"❌ Impossible d'instancier le LLM : {e}")
        print("   Vérifie FEMI_LLM_PROVIDER et les clés API.")
        return False

    passed = 0
    failed = 0
    errors_detail = []

    for index, case in enumerate(cases):
        if index and delai > 0:
            time.sleep(delai)  # reste sous la limite de requêtes par minute du fournisseur
        system = prompt_map.get(case.agent)
        if not system:
            print(f"  [{case.id}] ⚠️  Pas de prompt pour agent={case.agent}")
            continue

        user_content = case.message
        if case.history:
            user_content = "Historique:\n" + "\n".join(f"- {h}" for h in case.history) + f"\n\nMessage actuel: {case.message}"

        messages = [
            SystemMessage(content=system),
            HumanMessage(content=user_content),
        ]

        print(f"\n[{case.id}] {case.message[:60]}{'...' if len(case.message) > 60 else ''}")
        try:
            response = _appeler_llm(llm, messages)
            raw = _reponse_en_texte(response)
            parsed = extract_json(raw)
            if parsed is None:
                print(f"  ❌ JSON invalide")
                print(f"     brut: {raw[:200]}...")
                failed += 1
                errors_detail.append((case.id, ["JSON invalide"]))
                continue

            evaluator = EVALUATORS.get(case.agent, lambda r, c: (True, []))
            ok, errs = evaluator(parsed, case)
            if ok:
                print(f"  ✅ PASS")
                passed += 1
            else:
                print(f"  ❌ FAIL: {errs}")
                failed += 1
                errors_detail.append((case.id, errs))
        except Exception as e:
            print(f"  ❌ Exception: {e}")
            failed += 1
            errors_detail.append((case.id, [str(e)]))

    print("\n" + "=" * 70)
    print(f"Résultat: {passed} pass / {failed} fail / {passed + failed} total")
    if errors_detail:
        print("\nDétail des échecs:")
        for cid, errs in errors_detail:
            print(f"  {cid}: {errs}")
    print("=" * 70)
    return failed == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Évaluation des prompts Femi")
    parser.add_argument("--llm", action="store_true", help="Appeler le vrai LLM (nécessite clés API)")
    parser.add_argument(
        "--agent",
        choices=["router", "accounting", "customer", "financial", "modify"],
        help="Filtrer sur un agent",
    )
    parser.add_argument(
        "--delai",
        type=float,
        default=4.5,
        help="Pause en secondes entre deux appels (4.5 s = 13 appels/min, sous la limite gratuite de Gemini)",
    )
    args = parser.parse_args()

    if args.llm:
        success = run_llm_evaluation(args.agent, delai=args.delai)
        sys.exit(0 if success else 1)
    else:
        print_offline_suite(args.agent)
        sys.exit(0)


if __name__ == "__main__":
    main()
