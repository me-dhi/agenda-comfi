#!/usr/bin/env python3
import html
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


AGENDA_URL = ("https://www.assemblee-nationale.fr/dyn/17/organes/"
              "commissions-permanentes/finances/agenda")
NB_PAGES = 4               # 10 réunions par page : ~40 réunions les plus récentes
FICHIER = Path("finances.ics")
NOM_CALENDRIER = "Commission des finances – AN"
DUREE_PAR_DEFAUT = timedelta(hours=2)   # si le .ics ne donne pas d'heure de fin
PAUSE = 0.5                # secondes entre deux requêtes, par politesse


ENTETES = {"User-Agent": "Mozilla/5.0 (agenda-cofi; abonnement calendrier personnel)"}
RE_LIEN_ICS = re.compile(r"agendas/ics/(\d{4}-\d{2}-\d{2})/reunion/([A-Za-z0-9_]+)")
RE_VEVENT = re.compile(r"BEGIN:VEVENT.*?END:VEVENT", re.S)


def telecharger(url):
    req = urllib.request.Request(url, headers=ENTETES)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def champ(bloc, nom):
    """Valeur d'une propriété iCal (ex. DTSTART) dans un bloc VEVENT."""
    m = re.search(rf"^{nom}(?:;[^:\r\n]*)?:(.*)$", bloc, re.M)
    return m.group(1).strip() if m else None


def lire_date(valeur):
    """'20261202T100000Z' -> datetime UTC (None si format inattendu)."""
    if not valeur:
        return None
    try:
        return datetime.strptime(valeur.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def nettoyer(bloc):
    """Normalise un VEVENT : entités HTML, fins de ligne, heure de fin."""
    bloc = html.unescape(bloc)
    lignes = [l for l in bloc.replace("\r\n", "\n").split("\n") if l.strip()]
    texte = "\n".join(lignes)
    if champ(texte, "DTEND") is None:
        debut = lire_date(champ(texte, "DTSTART"))
        if debut:
            fin = (debut + DUREE_PAR_DEFAUT).strftime("%Y%m%dT%H%M%SZ")
            lignes.insert(-1, f"DTEND:{fin}")
    return "\r\n".join(lignes)


def evenements_existants():
    """Réunions déjà présentes dans finances.ics (pour garder l'historique)."""
    if not FICHIER.exists():
        return {}
    contenu = FICHIER.read_text(encoding="utf-8")
    return {champ(b, "UID"): b for b in RE_VEVENT.findall(contenu) if champ(b, "UID")}


def main():
    # 1. Relever les liens .ics sur les pages récentes de l'agenda
    liens = {}
    for page in range(1, NB_PAGES + 1):
        url = f"{AGENDA_URL}?order=dateDebut,desc&page={page}&limit=10"
        try:
            contenu = telecharger(url)
        except Exception as e:
            print(f"Page {page} inaccessible : {e}", file=sys.stderr)
            if page == 1:
                sys.exit(1)
            break
        for date, ident in RE_LIEN_ICS.findall(contenu):
            liens[ident] = date
        time.sleep(PAUSE)

    if not liens:
        print("Aucune réunion trouvée : la structure de la page a peut-être changé.",
              file=sys.stderr)
        sys.exit(1)
    print(f"{len(liens)} réunions trouvées sur l'agenda.")

    # 2. Télécharger chaque .ics de réunion
    frais = {}
    for ident, date in liens.items():
        url = f"https://www2.assemblee-nationale.fr/agendas/ics/{date}/reunion/{ident}"
        try:
            ics = telecharger(url)
        except Exception as e:
            print(f"Réunion {ident} ignorée : {e}", file=sys.stderr)
            continue
        for bloc in RE_VEVENT.findall(ics):
            frais[champ(bloc, "UID") or ident] = nettoyer(bloc)
        time.sleep(PAUSE)

    if not frais:
        print("Aucun fichier .ics récupéré.", file=sys.stderr)
        sys.exit(1)

    # 3. Fusionner avec l'historique :
    #    - réunions antérieures à la période relue : conservées telles quelles ;
    #    - réunions dans la période relue mais disparues du site : retirées
    #      (réunion annulée).
    debut_periode = min(liens.values()).replace("-", "")
    tous = {}
    for uid, bloc in evenements_existants().items():
        dt = champ(bloc, "DTSTART") or ""
        if dt[:8] < debut_periode:
            tous[uid] = bloc
    tous.update(frais)

    # 4. Écrire le calendrier, trié par date
    tries = sorted(tous.values(), key=lambda b: champ(b, "DTSTART") or "")
    maintenant = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sortie = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//agenda-cofi//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{NOM_CALENDRIER}",
        "X-WR-TIMEZONE:Europe/Paris",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
        f"X-WR-CALDESC:Généré automatiquement le {maintenant} depuis {AGENDA_URL}",
    ]
    sortie += tries
    sortie.append("END:VCALENDAR")
    FICHIER.write_text("\r\n".join(sortie) + "\r\n", encoding="utf-8", newline="")
    print(f"{FICHIER} écrit : {len(tries)} réunions ({len(frais)} mises à jour).")


if __name__ == "__main__":
    main()
