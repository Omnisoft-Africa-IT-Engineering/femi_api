#!/usr/bin/env python3
"""Tests basiques de validation des prompts optimisés Femi."""

import ast
import sys
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent

EXPECTED = {
    "router_prompt.py": "ROUTER_PROMPT",
    "accounting_prompt.py": "ACCOUNTING_PROMPT",
    "accounting_modify_prompt.py": "ACCOUNTING_MODIFY_PROMPT",
    "customer_prompt.py": "CUSTOMER_PROMPT",
    "financial_analyst_prompt.py": "FINANCIAL_ANALYST_PROMPT",
    "ocr_prompt.py": "OCR_PROMPT",
    "stt_prompt.py": "STT_PROMPT",
    "quote_prompt.py": "QUOTE_PROMPT",
}

def test_import():
    """Vérifie que tous les prompts s'importent correctement."""
    sys.path.insert(0, str(PROMPTS_DIR.parent.parent.parent.parent))  # project root-ish
    try:
        from apps.femi_agent.agent.prompts import (
            ROUTER_PROMPT,
            ACCOUNTING_PROMPT,
            ACCOUNTING_MODIFY_PROMPT,
            CUSTOMER_PROMPT,
            FINANCIAL_ANALYST_PROMPT,
            STT_PROMPT,
            OCR_PROMPT,
            QUOTE_PROMPT,
        )
        print("✅ Import de tous les prompts : OK")
        return True
    except Exception as e:
        print(f"❌ Import échoué : {e}")
        return False

def test_syntax_and_content():
    """Vérifie la syntaxe Python et la présence de contenu minimal."""
    ok = True
    for filename, var_name in EXPECTED.items():
        path = PROMPTS_DIR / filename
        if not path.exists():
            print(f"❌ Fichier manquant : {filename}")
            ok = False
            continue
        content = path.read_text(encoding="utf-8")
        try:
            ast.parse(content)
        except SyntaxError as e:
            print(f"❌ Syntaxe invalide dans {filename} : {e}")
            ok = False
            continue
        if var_name not in content:
            print(f"❌ Variable {var_name} absente de {filename}")
            ok = False
            continue
        # Vérifie qu'il y a du contenu substantiel
        if len(content) < 500:
            print(f"⚠️  {filename} semble trop court ({len(content)} chars)")
        # Vérifie présence de règles clés
        if "RAPPEL FINAL" not in content and "RÈGLES" not in content and "FORMAT" not in content:
            print(f"⚠️  {filename} : structure de prompt potentiellement incomplète")
        print(f"✅ {filename} ({len(content)} chars, {content.count(chr(10))+1} lignes)")
    return ok

def test_size_reduction():
    """Compare avec les backups si présents."""
    print("\n--- Réduction de taille ---")
    for bak in PROMPTS_DIR.glob("*.bak"):
        original = bak.read_text(encoding="utf-8")
        new_name = bak.name.replace(".bak", "")
        new_path = PROMPTS_DIR / new_name
        if new_path.exists():
            new = new_path.read_text(encoding="utf-8")
            reduction = 100 * (1 - len(new) / len(original))
            print(f"  {new_name}: {len(original)} → {len(new)} chars ({reduction:.0f}% de réduction)")
    return True

if __name__ == "__main__":
    print("=== Tests des prompts optimisés Femi ===\n")
    r1 = test_syntax_and_content()
    print()
    r2 = test_import()
    print()
    test_size_reduction()
    print()
    if r1 and r2:
        print("🎉 Tous les tests de base sont passés.")
        sys.exit(0)
    else:
        print("💥 Certains tests ont échoué.")
        sys.exit(1)
