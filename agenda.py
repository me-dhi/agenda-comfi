#!/usr/bin/env python3
import html
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# --- Réglages ---------------------------------------------------------------
FINANCES_URL = ("https://www.assemblee-nationale.fr/dyn/17/organes/"
                "commissions-permanentes/finances/agenda")
FINANCES_NB_PAGES = 4        # 10 réunions par page : ~40 réunions les plus récentes

AGENDA_JOUR_URL = "https://www2.assemblee-nationale.fr/agendas/les-agendas/{}"
SEANCE_JOURS_AVANT = 7       # relit les séances des 7 derniers jours...
SEANCE_JOURS_APRES = 28      # ...et des 4 semaines à venir

DUREE_PAR_DEFAUT = timedelta(hours=2)   # si le .ics ne donne pas d'heure de fin
PAUSE = 0.3                  # secondes entre deux requêtes, par politesse
# ---------------------------------------------------------------------------

ENTETES = {"User-Agent": "Mozilla/5.0 (agenda-an; abonnement calendrier personnel)"}
RE_LIEN_ICS = re.compile(r"agendas/ics/(\d{4}-\d{2}-\d{2})/reunion/([A-Za-z0-9_]+)")
RE_VEVENT = re.compile(r"BEGIN:VEVENT.*?END:VEVENT", re.S)
ICS_URL = "https://www2.assemblee-nationale.fr/agendas/ics/{}/reunion/{}"


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


def recuperer_ics(liens):
    """{identifiant: date} -> {UID: VEVENT nettoyé}"""
    frais = {}
    for ident, jour in liens.items():
        try:
            ics = telecharger(ICS_URL.format(jour, ident))
        except Exception as e:
            print(f"  {ident} ignoré : {e}", file=sys.stderr)
            continue
        for bloc in RE_VEVENT.findall(ics):
            frais[champ(bloc, "UID") or ident] = nettoyer(bloc)
        time.sleep(PAUSE)
    return frais


def ecrire_calendrier(fichier, nom, source, frais, debut_periode):
    """
    Fusionne avec le fichier existant puis l'écrit :
      - événements antérieurs à la période relue : conservés (historique) ;
      - événements de la période relue disparus du site : retirés (annulés).
    debut_periode : 'AAAAMMJJ'
    """
    fichier = Path(fichier)
    tous = {}
    if fichier.exists():
        for bloc in RE_VEVENT.findall(fichier.read_text(encoding="utf-8")):
            uid = champ(bloc, "UID")
            if uid and (champ(bloc, "DTSTART") or "")[:8] < debut_periode:
                tous[uid] = bloc
    tous.update(frais)

    tries = sorted(tous.values(), key=lambda b: champ(b, "DTSTART") or "")
    maintenant = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sortie = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//agenda-an//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{nom}",
        "X-WR-TIMEZONE:Europe/Paris",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
        f"X-WR-CALDESC:Généré automatiquement le {maintenant} depuis {source}",
    ] + tries + ["END:VCALENDAR"]
    fichier.write_text("\r\n".join(sortie) + "\r\n", encoding="utf-8", newline="")
    print(f"  {fichier} écrit : {len(tries)} événements ({len(frais)} mis à jour).")


# --- Commission des finances -----------------------------------------------
def commission_finances():
    print("Commission des finances")
    liens = {}
    for page in range(1, FINANCES_NB_PAGES + 1):
        url = f"{FINANCES_URL}?order=dateDebut,desc&page={page}&limit=10"
        try:
            contenu = telecharger(url)
        except Exception as e:
            print(f"  page {page} inaccessible : {e}", file=sys.stderr)
            if page == 1:
                return False
            break
        for jour, ident in RE_LIEN_ICS.findall(contenu):
            liens[ident] = jour
        time.sleep(PAUSE)

    if not liens:
        print("  aucune réunion trouvée : la page a peut-être changé.", file=sys.stderr)
        return False
    frais = recuperer_ics(liens)
    if not frais:
        return False
    ecrire_calendrier("finances.ics", "Commission des finances – AN", FINANCES_URL,
                      frais, min(liens.values()).replace("-", ""))
    return True


# --- Séance publique --------------------------------------------------------
def seance_publique():
    print("Séance publique")
    aujourdhui = date.today()
    premier = aujourdhui - timedelta(days=SEANCE_JOURS_AVANT)
    liens, pages_lues = {}, 0
    for i in range(SEANCE_JOURS_AVANT + SEANCE_JOURS_APRES + 1):
        jour = (premier + timedelta(days=i)).isoformat()
        try:
            contenu = telecharger(AGENDA_JOUR_URL.format(jour))
            pages_lues += 1
        except Exception as e:
            print(f"  agenda du {jour} inaccessible : {e}", file=sys.stderr)
            continue
        # Les séances publiques ont un identifiant en « …IDS… »
        # (les réunions de commission sont en « …IDC… »).
        for j, ident in RE_LIEN_ICS.findall(contenu):
            if re.search(r"IDS\d+$", ident):
                liens[ident] = j
        time.sleep(PAUSE)

    if pages_lues == 0:
        print("  agenda général inaccessible.", file=sys.stderr)
        return False
    frais = recuperer_ics(liens)
    # Hors session (été), il peut n'y avoir aucune séance : ce n'est pas une erreur.
    ecrire_calendrier("seance.ics", "Séance publique – AN",
                      "https://www.assemblee-nationale.fr/dyn/seance-publique",
                      frais, premier.strftime("%Y%m%d"))
    return True


if __name__ == "__main__":
    ok_finances = commission_finances()
    ok_seance = seance_publique()
    sys.exit(0 if (ok_finances and ok_seance) else 1)
