"""
Test manuel isolé de l'agent OCR (OcrExecutor / Gemini vision).

Objectif : vérifier que l'appel vision fonctionne correctement (sans
timeout, avec une extraction correcte) EN DEHORS de toute la chaîne
HTTP/Gunicorn/router — ça isole complètement le composant OCR du reste
du pipeline (persistance, Supabase Storage, etc.).

Prérequis :
- À lancer depuis la racine du projet (là où se trouve manage.py et
  image.png), avec le même environnement/venv que d'habitude.
- Les mêmes variables d'environnement que celles utilisées en
  production doivent être présentes localement (FEMI_VISION_PROVIDER,
  FEMI_VISION_MODEL, la clé API Gemini, DATABASE_URL, SECRET_KEY...) —
  sinon Django refusera de démarrer (DATABASE_URL est obligatoire
  depuis le fix de sécurité sur settings.py).

Usage :
    python test_ocr_manual.py
"""

import base64
import os
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from apps.femi_agent.agent.ocr_executor import (  # noqa: E402
    OcrExecutor,
    OcrExecutionError,
)

IMAGE_PATH = "image.png"


def main():
    print("=" * 70)
    print("TEST MANUEL — OcrExecutor.execute()")
    print("=" * 70)

    if not os.path.exists(IMAGE_PATH):
        print(f"❌ Fichier introuvable : {IMAGE_PATH}")
        print("   Lance ce script depuis la racine du projet.")
        return

    with open(IMAGE_PATH, "rb") as f:
        image_bytes = f.read()

    print(f"📄 Image chargée : {IMAGE_PATH} ({len(image_bytes)} octets)")
    print(f"🔢 Taille en base64 : {len(base64.b64encode(image_bytes))} caractères")
    print()

    print("⏳ Appel de OcrExecutor.execute() en cours...")
    debut = time.monotonic()

    try:
        result = OcrExecutor.execute(image_bytes)
    except OcrExecutionError as exc:
        duree = time.monotonic() - debut
        print(f"❌ OcrExecutionError après {duree:.1f}s : {exc}")
        return
    except Exception:
        duree = time.monotonic() - debut
        print(f"❌ Erreur inattendue après {duree:.1f}s :")
        raise

    duree = time.monotonic() - debut
    print(f"✅ Réponse reçue en {duree:.1f}s")
    print()

    print("-" * 70)
    print("EN-TÊTE")
    print("-" * 70)
    print(f"Commerçant       : {result.en_tete.nom_commercant}")
    print(f"Référence        : {result.en_tete.numero_facture_recu}")
    print(f"Date             : {result.en_tete.date}")

    print()
    print("-" * 70)
    print(f"LIGNES ARTICLES ({len(result.lignes_articles)})")
    print("-" * 70)
    for i, ligne in enumerate(result.lignes_articles, start=1):
        print(
            f"{i}. {ligne.designation} | qte={ligne.quantite} | "
            f"prix_unitaire={ligne.prix_unitaire} | "
            f"prix_total={ligne.prix_total}"
        )

    print()
    print("-" * 70)
    print("TOTAUX")
    print("-" * 70)
    print(f"Total HT         : {result.totaux.total_ht}")
    print(f"TVA              : {result.totaux.tva}")
    print(f"Total TTC        : {result.totaux.total_ttc}")
    print(f"Moyen paiement   : {result.totaux.moyen_de_paiement}")

    print()
    print("-" * 70)
    print("TEXTE BRUT COMPLET")
    print("-" * 70)
    print(result.texte_brut_complet)

    print()
    print("=" * 70)
    print("Terminé.")
    print("=" * 70)


if __name__ == "__main__":
    main()