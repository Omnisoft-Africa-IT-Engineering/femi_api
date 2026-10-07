"""
Messages « sociaux » de Femi : salutations, remerciements, au revoir,
« qui es-tu / que sais-tu faire », et réponse humaine aux messages hors
sujet.

Module volontairement isolé (Python pur, aucun appel LLM, aucune
dépendance Django) : classification instantanée et testable. Les textes
`build_*` ci-dessous servent de réponse de SECOURS ; la réponse réellement
envoyée est rédigée de façon naturelle par response_writer.py à partir des
faits de `social_reply_context()`.

Principe de sécurité : un message n'est « social » que si TOUS ses mots
appartiennent à un vocabulaire fermé de politesse, et s'il ne contient
aucun chiffre. « Bonjour, j'ai vendu 5000 » ou « merci, combien j'ai
vendu ? » ne sont donc JAMAIS interceptés : ils partent au Router.
De même « ok », « oui », « d'accord » ne déclenchent rien seuls : ce sont
des confirmations à laisser au flux normal (« Confirmes-tu ? »).
"""

import random
import re
import unicodedata
from typing import Optional

GREETING = "GREETING"
IDENTITY = "IDENTITY"
THANKS = "THANKS"
BYE = "BYE"

MAX_SOCIAL_TOKENS = 8

_GREETING_WORDS = {
    "salut", "bonjour", "bonjours", "bonsoir", "hello", "hey", "hi",
    "coucou", "cc", "slt", "bjr", "bsr", "yo", "wesh", "allo", "hola",
}
# Mots de politesse autorisés autour d'une salutation (vocabulaire fermé).
_FILLER_WORDS = {
    "femi", "mon", "ma", "cher", "chere", "ami", "amie", "frere", "boss",
    "patron", "monsieur", "madame", "la", "le", "l", "j", "espere", "que",
    "tu", "vas", "va", "vous", "allez", "comment", "ca", "sa", "cava",
    "sava", "bien", "et", "toi", "forme", "a", "tous", "toute", "tout",
    "monde", "svp", "stp", "s", "il", "plait", "te", "aujourd", "hui",
}
_WELLBEING_TOKENS = {"va", "vas", "cava", "sava", "forme", "allez"}

_THANKS_TRIGGERS = {"merci", "thanks", "thank", "bravo"}
_THANKS_FILLER = {
    "beaucoup", "mille", "super", "parfait", "genial", "nickel", "top",
    "cool", "ok", "d", "accord", "tres", "vraiment", "bien", "c", "est",
    "bon", "you", "femi", "excellent", "gentil", "a", "toi", "vous",
}

_BYE_PHRASES = (
    "au revoir", "aurevoir", "bye", "ciao", "a bientot", "a demain",
    "a plus", "a plus tard", "bonne journee", "bonne soiree", "bonne nuit",
    "bonne semaine", "bon week end", "bon weekend",
)
_BYE_FILLER = {
    "au", "revoir", "aurevoir", "bye", "ciao", "a", "bientot", "demain",
    "plus", "tard", "bonne", "bon", "journee", "soiree", "nuit", "semaine",
    "week", "end", "weekend", "femi", "merci", "salut", "et", "toi",
    "vous", "aussi",
}

# Questions d'identité : comparées EXACTEMENT (après retrait des
# salutations en début et de « femi »), pour ne jamais intercepter une vraie
# demande comme « aide-moi à enregistrer une vente ».
_IDENTITY_PHRASES = {
    "qui es tu", "tu es qui", "qui etes vous", "vous etes qui",
    "tu es quoi", "tu fais quoi", "que fais tu", "que sais tu faire",
    "tu sais faire quoi", "tu peux faire quoi", "que peux tu faire",
    "tu sers a quoi", "a quoi tu sers", "a quoi sers tu", "presente toi",
    "peux tu te presenter", "c est quoi", "qui est", "comment ca marche",
    "comment tu marches", "aide", "aide moi", "aidez moi", "help", "menu",
    "au secours", "que puis je faire", "je peux faire quoi",
    "tu peux m aider", "peux tu m aider", "tu peux maider",
}

_ROLE_LIST = (
    "• Enregistrer tes ventes, dépenses et prêts — par message, par vocal "
    "ou avec une photo de facture (ex : *J'ai vendu 2 sacs à 15 000 FCFA*) ;\n"
    "• Suivre les clients qui te doivent de l'argent ;\n"
    "• Te donner ton chiffre d'affaires, tes dépenses, ton bénéfice ou ta "
    "trésorerie ;\n"
    "• Modifier ou annuler une opération déjà enregistrée."
)

_OFF_TOPIC_REPLIES = (
    "Là, tu m'as un peu perdu 😅 Moi, ce que je sais faire, c'est la "
    "comptabilité et le suivi de ton argent : enregistrer une vente ou une "
    "dépense, suivre ce que tes clients te doivent, ou te donner ton chiffre "
    "d'affaires et ton bénéfice. Dis-moi ce que tu veux faire, ou reformule "
    "si je n'ai pas bien compris.",
    "Sur ce sujet, je ne pourrai pas t'aider 🙂 Je suis *Femi*, ton "
    "assistant pour tes comptes : ventes, dépenses, prêts, clients qui te "
    "doivent de l'argent, bénéfice… Si tu voulais me demander quelque chose "
    "de ce genre, reformule et je m'en occupe.",
    "Hmm, je n'ai pas bien saisi 🤔 Je suis là pour t'aider avec tes "
    "comptes : enregistrer une opération, voir ce que te doivent tes "
    "clients, ou suivre ton bénéfice. Tu peux me l'écrire autrement, ou "
    "m'envoyer un vocal ou une photo de facture.",
)


def _normalize(text: str) -> list[str]:
    """Minuscules, sans accents ni ponctuation/emoji → liste de mots."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", no_accents)


def classify_social_message(text: Optional[str]) -> Optional[str]:
    """Retourne GREETING / IDENTITY / THANKS / BYE, ou None si le message
    doit suivre le flux normal (Router)."""
    if not text or not text.strip():
        return None

    tokens = _normalize(text)
    if not tokens or len(tokens) > MAX_SOCIAL_TOKENS:
        return None
    if any(t.isdigit() for t in tokens):
        return None  # un montant ou un nombre = vraie demande

    token_set = set(tokens)
    joined = " ".join(tokens)

    # 1. Au revoir
    if token_set <= _BYE_FILLER and any(p in joined for p in _BYE_PHRASES):
        return BYE

    # 2. Remerciement (mot de remerciement obligatoire : « ok » seul est
    #    une confirmation, pas un remerciement)
    if token_set <= (_THANKS_TRIGGERS | _THANKS_FILLER) and token_set & _THANKS_TRIGGERS:
        return THANKS

    # 3. Question d'identité / d'aide (après retrait des salutations en
    #    début de message et de « femi »)
    core = [t for t in tokens if t not in _GREETING_WORDS and t != "femi"]
    if " ".join(core) in _IDENTITY_PHRASES:
        return IDENTITY

    # 4. Salutation : vocabulaire fermé, avec une salutation ou une
    #    formule « ça va ? / comment vas-tu ? »
    if token_set <= (_GREETING_WORDS | _FILLER_WORDS):
        if token_set & _GREETING_WORDS or _asks_wellbeing(tokens):
            return GREETING

    return None


def _asks_wellbeing(tokens: list[str]) -> bool:
    return bool(set(tokens) & _WELLBEING_TOKENS)


def _salutation(hour: int) -> str:
    return "Bonjour" if hour < 18 else "Bonsoir"


def build_social_reply(text: str, hour: int) -> str:
    """Réponse humaine à un message social. `hour` : heure locale (0-23)
    pour choisir Bonjour/Bonsoir."""
    kind = classify_social_message(text)
    tokens = _normalize(text or "")

    if kind == THANKS:
        return (
            "Avec plaisir ! 😊 N'hésite pas si tu as une opération à "
            "enregistrer ou une question sur tes chiffres."
        )

    if kind == BYE:
        return (
            "À bientôt ! 👋 Je reste disponible dès que tu as une opération "
            "à enregistrer ou une question sur tes chiffres."
        )

    if kind == IDENTITY:
        intro = "Je suis *Femi*, ton assistant comptable et financier."
        if set(tokens) & _GREETING_WORDS:
            intro = f"{_salutation(hour)} 👋 {intro}"
        return (
            f"{intro}\n\nVoici ce que je peux faire pour toi :\n{_ROLE_LIST}\n\n"
            "Tu peux m'écrire comme tu parlerais à un collègue, je m'adapte 🙂"
        )

    # GREETING (et repli par défaut)
    bien = " Je vais bien, merci !" if _asks_wellbeing(tokens) else ""
    return (
        f"{_salutation(hour)} 👋 Je suis *Femi*, ton assistant comptable et "
        f"financier.{bien}\n\nVoici ce que je peux faire pour toi :\n"
        f"{_ROLE_LIST}\n\nQu'est-ce que je peux faire pour toi ?"
    )


_REPLY_TYPE_BY_KIND = {
    GREETING: "social_greeting",
    IDENTITY: "social_identity",
    THANKS: "social_thanks",
    BYE: "social_bye",
}

CAPABILITIES_FACTS = (
    "Ce que Femi sait faire : enregistrer ventes, dépenses et prêts (par "
    "message, par vocal, ou avec une photo ou un PDF de facture) ; suivre les "
    "clients qui doivent de l'argent ; donner le chiffre d'affaires, les "
    "dépenses, le bénéfice ou la trésorerie ; modifier ou annuler une "
    "opération déjà enregistrée."
)


def social_reply_context(text: str, hour: int) -> tuple[str, str]:
    """(reply_type, facts) à donner à response_writer.write_natural_reply()
    pour rédiger une réponse naturelle à un message social."""
    kind = classify_social_message(text) or GREETING
    tokens = _normalize(text or "")

    facts = [
        f"Moment de la journée : {'matin / après-midi' if hour < 18 else 'soir'} "
        f"(salutation adaptée : {_salutation(hour)}).",
        f"Message de l'utilisateur : {(text or '').strip()[:200]}",
    ]
    if kind in (GREETING, IDENTITY):
        facts.append(CAPABILITIES_FACTS)
    if kind == GREETING and _asks_wellbeing(tokens):
        facts.append(
            "L'utilisateur demande de tes nouvelles : réponds brièvement que "
            "tu vas bien."
        )
    return _REPLY_TYPE_BY_KIND[kind], "\n".join(facts)


def build_off_topic_reply(rng: Optional[random.Random] = None) -> str:
    """Réponse chaleureuse à un message hors périmètre ou incompris."""
    chooser = rng or random
    return chooser.choice(_OFF_TOPIC_REPLIES)