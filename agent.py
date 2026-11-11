"""Agent Instagram quotidien — Céline et ses faïences.

Chaque jour : choisit une pièce (jamais la même que les jours précédents),
la met en scène avec une IA image (OpenAI), écrit la légende, publie à 18h (Paris).

Variables d'environnement (secrets GitHub) :
  OPENAI_API_KEY      clé API OpenAI (images + légendes)
  MAKE_WEBHOOK_URL    (option simple) webhook Make.com qui publie sur Instagram
  ou IG_USER_ID + IG_ACCESS_TOKEN  (option API Meta directe)
  GITHUB_REPOSITORY   fourni par GitHub Actions (owner/repo), sert à l'URL publique de l'image
Options :
  DRY_RUN=1           génère l'image et la légende, ne publie pas
  FORCE=1             ignore l'heure et le « déjà publié aujourd'hui »
  IMAGE_MODEL         défaut gpt-image-2
  TEXT_MODEL          défaut gpt-5-mini
  POST_HOUR           défaut 18
"""
import base64
import datetime as dt
import io
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).parent
STATE = ROOT / "state.json"
POSTS = ROOT / "posts"
CATALOG = json.loads((ROOT / "catalog.json").read_text())
SITE = "https://celineetsesfaiences.com"
PARIS = ZoneInfo("Europe/Paris")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-2")
TEXT_MODEL = os.getenv("TEXT_MODEL", "gpt-5-mini")
POST_HOUR = int(os.getenv("POST_HOUR", "18"))
DRY = os.getenv("DRY_RUN") == "1"
FORCE = os.getenv("FORCE") == "1"
UA = {"User-Agent": "Mozilla/5.0 (celineetsesfaiences-insta-agent)"}
OA = "https://api.openai.com/v1"


def log(*a):
    print(dt.datetime.now(PARIS).strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- état
def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"history": []}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- produits
def scrape_site():
    """Lit le site pour récupérer les produits en ligne (best effort)."""
    found = {}
    try:
        for page in ["/", "/boutique-de-poterie-ceramique"]:
            html = requests.get(SITE + page, headers=UA, timeout=30).text
            for slug in set(re.findall(r'href="/([a-z0-9][a-z0-9\-]+)"', html)):
                if slug in {"galerie", "contact", "personnalisation", "boutique-de-poterie-ceramique"}:
                    continue
                found.setdefault(slug, {"slug": slug})
        # détails produit via les balises og:
        for slug in list(found)[:80]:
            try:
                h = requests.get(f"{SITE}/{slug}", headers=UA, timeout=30).text
            except requests.RequestException:
                continue
            img = re.search(r'property="og:image"\s+content="([^"]+)"', h) or \
                re.search(r'content="([^"]+)"\s+property="og:image"', h)
            title = re.search(r'property="og:title"\s+content="([^"]+)"', h)
            ids = re.findall(r'cdn-ecommerce/store_[A-Z0-9]+/assets/([0-9a-f\-]{36})\.', h)
            if not (img or ids):
                found.pop(slug)
                continue
            found[slug]["name"] = (title.group(1).split("|")[0].strip() if title else slug.replace("-", " ").title())
            if ids:
                found[slug]["image_id"] = ids[0]
            elif img:
                found[slug]["image"] = img.group(1)
            price = re.search(r'(\d+[.,]\d{2})\s*€|€\s*(\d+[.,]\d{2})', h)
            if price:
                found[slug]["price"] = (price.group(1) or price.group(2)).replace(".", ",") + " €"
    except requests.RequestException as e:
        log("site inaccessible:", e)
    return found


def products():
    """Catalogue fusionné : infos riches du catalogue + nouveautés du site."""
    cat = {p["slug"]: dict(p) for p in CATALOG["products"]}
    live = scrape_site()
    for slug, p in live.items():
        if slug in cat:
            for k in ("image_id", "price"):
                if p.get(k):
                    cat[slug][k] = p[k]
        elif p.get("image_id") or p.get("image"):
            p.setdefault("look", p["name"])
            p.setdefault("palette", "")
            p.setdefault("scenes", [])
            cat[slug] = p
            log("nouveau produit trouvé sur le site:", slug)
    return list(cat.values())


QUEUE = ROOT / "prochain.txt"


def _norm(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def match_product(text, items):
    """Trouve la pièce correspondant au texte écrit à la main (nom ou slug, accents/majuscules ignorés)."""
    q = _norm(text)
    if not q:
        return None
    words = set(q.split())
    best, score = None, 0
    for p in items:
        cand = _norm(p["name"]) + " " + _norm(p["slug"].replace("-", " "))
        if q == _norm(p["name"]) or q == _norm(p["slug"].replace("-", " ")):
            return p
        sc = len(words & set(cand.split())) / len(words)
        if sc > score:
            best, score = p, sc
    return best if score >= 0.99 else None


def pick_from_queue(items):
    """Lit prochain.txt : la 1re ligne utile est consommée (retirée du fichier).
    En test (DRY_RUN), la ligne est seulement lue : la liste ne bouge pas."""
    if not QUEUE.exists():
        return None
    lines = QUEUE.read_text(encoding="utf-8").splitlines()
    header = [l for l in lines if l.strip().startswith("#")]
    todo = [l for l in lines if l.strip() and not l.strip().startswith("#")]
    while todo:
        line = todo.pop(0)
        piece, _, decor = line.partition("|")
        p = match_product(piece, items)
        if not DRY:
            QUEUE.write_text("\n".join(header + todo) + "\n", encoding="utf-8")
        if p:
            p = dict(p)
            if decor.strip():
                p["_decor"] = decor.strip()
            log("pièce choisie dans prochain.txt:", line.strip(), "->", p["name"])
            return p
        log("prochain.txt: pièce introuvable, ligne ignorée:", line.strip())
    return None


def pick_product(items, state):
    chosen = pick_from_queue(items)
    if chosen:
        return chosen
    recent = [h["slug"] for h in state["history"] if not h.get("test")]
    never = [p for p in items if p["slug"] not in recent]
    if never:
        return random.choice(never)
    # tout a déjà été publié : reprend la pièce publiée il y a le plus longtemps
    last_seen = {s: i for i, s in enumerate(recent)}
    items = sorted(items, key=lambda p: last_seen[p["slug"]])
    return items[0] if items[0]["slug"] != recent[-1] or len(items) == 1 else items[1]


def fetch_image(p):
    if p.get("asset") or p.get("image_id"):
        url = CATALOG["image_base"] + (p.get("asset") or p["image_id"] + ".jpg")
        r = requests.get(url, headers=UA, timeout=60)
        r.raise_for_status()
        data = r.content
    elif str(p.get("image", "")).startswith("http"):
        r = requests.get(p["image"], headers=UA, timeout=60)
        r.raise_for_status()
        data = r.content
    else:
        data = (ROOT / p["image"]).read_bytes()
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    im.thumbnail((1536, 1536))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- direction artistique
STYLES = json.loads((ROOT / "styles.json").read_text())
NO_REPEAT = 25   # un concept ne revient pas avant 25 posts


def art_direction(p, state):
    """Choisit une mise en scène jamais vue récemment, différente de la veille
    par la catégorie, l'angle et la lumière."""
    hist = state["history"]
    recent_ids = {h.get("concept") for h in hist[-NO_REPEAT:]}
    last = hist[-1] if hist else {}
    month = dt.datetime.now(PARIS).month
    pool = [c for c in STYLES["concepts"] if month in c.get("months", range(1, 13))]
    # décors imaginés spécialement pour cette pièce (catégorie « assorti »)
    for i, sc in enumerate(p.get("scenes", [])):
        pool.append({"id": f"{p['slug'].strip('-')}-{i}", "cat": "assorti", "desc": sc[2:].strip(" :") if sc[:2] in ("A:", "B:") else sc})
    ok = [c for c in pool if c["id"] not in recent_ids and c["cat"] != last.get("cat")]
    if not ok:
        ok = [c for c in pool if c["cat"] != last.get("cat")] or pool
    if last.get("angle") == "vue de dessus à la verticale":
        ok = [c for c in ok if c["id"] != "flatlay"] or ok
    # légère préférence pour les décors assortis à la pièce et les saisons
    weights = [3 if c["cat"] in ("assorti", "saison") else 1 for c in ok]
    concept = random.choices(ok, weights=weights)[0]
    p["_candidates"] = ok
    angle = random.choice([a for a in STYLES["angles"] if a != last.get("angle")])
    light = random.choice([l for l in STYLES["lights"] if l != last.get("light")])
    if concept["id"] == "flatlay":
        angle = "vue de dessus à la verticale"
    return {"concept": concept["id"], "cat": concept["cat"], "scene": concept["desc"], "angle": angle, "light": light}


def ai_art_direction(p, ad, img_png):
    """Regarde la vraie photo de la pièce et choisit, parmi les décors autorisés,
    celui qui va avec ses couleurs et ses motifs, puis l'adapte à la pièce."""
    cands = p.get("_candidates") or []
    if not os.getenv("OPENAI_API_KEY") or not cands:
        return ad
    lst = "\n".join(f"- {c['id']} : {c['desc']}" for c in cands)
    instr = f"""Tu es directeur artistique pour une céramiste artisanale. Voici la photo de la pièce « {p['name']} ».
1. Décris précisément la pièce (forme, couleur d'émail, motifs peints et leurs couleurs) en 1 phrase.
2. Donne sa palette (3 à 4 couleurs).
3. Choisis dans la liste ci-dessous LE décor qui met le mieux en valeur CETTE pièce. RÈGLE : décor TON SUR TON — le fond et les accessoires reprennent la couleur dominante de la pièce (pièce rose -> fond rose poudré ; pièce bleue -> fond bleu), en nuances un peu plus claires ou plus foncées. Pas de couleur complémentaire ni contrastée en fond. JAMAIS un simple fond uni vide : toujours un vrai décor riche et vivant (matières, fleurs, fruits, objets, végétaux) dans ces mêmes tons.
4. Réécris ce décor en 1 à 2 phrases, adapté à la pièce, en REPRENANT SES MOTIFS dans le décor (pièce à fleurs -> vraies fleurs de la même couleur autour ; pièce à pois -> petits fruits ronds ; pièce à cœurs -> pétales, cœurs...). Exemple : bol rose à fleurs rouges -> fond rose poudré, pétales et fleurs roses et rouges éparpillés autour.
Décors autorisés :
{lst}
Réponds uniquement en JSON : {{"look": "...", "palette": "...", "id": "...", "scene": "..."}}"""
    try:
        b64 = base64.b64encode(img_png).decode()
        r = requests.post(f"{OA}/responses", headers=openai_headers(), timeout=120, json={
            "model": TEXT_MODEL,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": instr},
                {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"}]}]})
        r.raise_for_status()
        j = r.json()
        txt = j.get("output_text") or "".join(
            c.get("text", "") for o in j.get("output", []) for c in o.get("content", []) or [] if c.get("type") == "output_text")
        data = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        # l'IA renvoie parfois des listes : on remet tout en texte
        data = {k: (", ".join(map(str, v)) if isinstance(v, list) else str(v)) for k, v in data.items()}
        chosen = next((c for c in cands if c["id"] == data.get("id")), None)
        if chosen:
            ad.update(concept=chosen["id"], cat=chosen["cat"])
        if data.get("scene"):
            ad["scene"] = data["scene"]
        if data.get("look"):
            p["look"] = data["look"]
        if data.get("palette"):
            p["palette"] = data["palette"]
        log("direction artistique IA:", ad["concept"], "|", ad["scene"][:120])
    except Exception as e:  # noqa
        log("direction artistique IA indisponible, décor tiré au hasard:", e)
    return ad


def image_prompt(p, ad):
    return f"""Photographie produit professionnelle, hyperréaliste, pour Instagram d'une marque de céramique artisanale haut de gamme.

PIÈCE (référence = photo fournie) : {p.get('look', p['name'])}.
RÈGLE ABSOLUE : la pièce doit rester strictement identique à la photo — même forme, mêmes proportions, même couleur d'émail, même motif peint (nombre, taille et position des motifs), même texture artisanale. Tu peux seulement changer l'angle de vue, la lumière et l'environnement. Ne rajoute aucun logo ni texte sur la pièce.

DÉCOR : {ad['scene']}. Le décor est riche et vivant (plusieurs éléments, matières, végétaux), jamais un simple fond uni. Les couleurs du décor reprennent et mettent en valeur celles de la pièce{(' (' + p['palette'] + ')') if p.get('palette') else ''}.
CADRAGE : {ad['angle']}. La pièce est le sujet principal, nette, composition éditoriale (règle des tiers), arrière-plan en faible profondeur de champ.
LUMIÈRE : {ad['light']}. Ombres de contact et occlusion ambiante réalistes, reflets naturels sur l'émail, la pièce parfaitement intégrée à la scène (même lumière, même température de couleur).
RENDU : vraie photo (pas d'aspect 3D ni illustration), étalonnage élégant et artisanal. Format vertical, aucun texte, aucun filigrane."""


def openai_headers():
    return {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}


def generate_image(png_bytes, prompt):
    files = [("image[]", ("piece.png", png_bytes, "image/png"))]
    data = {"model": IMAGE_MODEL, "prompt": prompt, "size": os.getenv("IMAGE_SIZE", "1024x1536"),
            "quality": os.getenv("IMAGE_QUALITY", "high"), "n": "1"}
    for attempt in range(3):
        r = requests.post(f"{OA}/images/edits", headers=openai_headers(), files=files, data=data, timeout=300)
        if r.ok:
            return base64.b64decode(r.json()["data"][0]["b64_json"])
        log("erreur image", r.status_code, r.text[:400])
        time.sleep(10 * (attempt + 1))
    raise RuntimeError("génération d'image impossible")


def finish_image(raw, signature):
    """Recadre en 4:5 (1080x1350) et ajoute parfois une signature discrète."""
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im = ImageOps.fit(im, (1080, 1350), Image.LANCZOS, centering=(0.5, 0.5))
    if signature:
        d = ImageDraw.Draw(im)
        f = ImageFont.truetype(str(ROOT / "assets/fonts/PT_Serif-Web-Italic.ttf"), 30)
        t = "Céline et ses faïences"
        w = d.textlength(t, font=f)
        x, y = (1080 - w) / 2, 1350 - 70
        # couleur adaptée à la luminosité de la zone
        zone = im.crop((int(x), y - 10, int(x + w), y + 40)).convert("L")
        lum = sum(zone.getdata()) / (zone.width * zone.height)
        d.text((x, y), t, font=f, fill=(40, 34, 30) if lum > 140 else (245, 240, 232))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=95)
    return buf.getvalue()


HASHTAGS = ("#faience #ceramique #ceramiqueartisanale #faitmain #poterie #artisanatfrancais #alsace "
            "#handmadeceramics #pottery #ceramics #artdelatable #madeinfrance #slowliving #decorationinterieur")


def caption_fallback(p):
    price = f"\n💰 {p['price']}" if p.get("price") else ""
    return (f"{p['name']} ✨\n\nFaçonnée, tournée et émaillée à la main dans mon petit atelier de "
            f"Steinbrunn-le-Bas, en Alsace. Chaque pièce est unique : un petit fragment de poésie en terre "
            f"pour accompagner vos pauses.{price}\n\n🛒 Disponible sur celineetsesfaiences.com (lien en bio)\n"
            f"✉️ Personnalisation possible sur demande\n\n{HASHTAGS}")


def write_caption(p, scene, final_jpg):
    if not os.getenv("OPENAI_API_KEY"):
        return caption_fallback(p)
    b64 = base64.b64encode(final_jpg).decode()
    instr = f"""Tu es la community manager de « Céline et ses faïences », céramiste artisanale indépendante à Steinbrunn-le-Bas (Alsace) qui façonne, tourne et émaille chaque pièce à la main. Ton : chaleureux, poétique, sincère, à la première personne (c'est Céline qui parle), jamais commercial agressif.
Écris la légende Instagram (français) pour ce visuel : {p['name']}{(' — ' + p['price']) if p.get('price') else ''}. Décor : {scene}.
Structure : une accroche courte et sensorielle (1 ligne, 1 emoji max), 2-3 phrases sur la pièce et le geste artisanal (n'invente aucun détail technique non visible), {'le prix, ' if p.get('price') else ''}puis « Disponible sur celineetsesfaiences.com (lien en bio) », puis une question ouverte pour faire réagir, puis 12 à 15 hashtags pertinents (FR + EN) dont #faitmain #ceramiqueartisanale #alsace.
Réponds uniquement avec la légende."""
    try:
        r = requests.post(f"{OA}/responses", headers=openai_headers(), timeout=120, json={
            "model": TEXT_MODEL,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": instr},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}]}]})
        r.raise_for_status()
        j = r.json()
        txt = j.get("output_text") or "".join(
            c.get("text", "") for o in j.get("output", []) for c in o.get("content", []) or [] if c.get("type") == "output_text")
        txt = txt.strip()
        if 80 < len(txt) < 2200:
            return txt
    except Exception as e:  # noqa
        log("légende IA indisponible, modèle de secours:", e)
    return caption_fallback(p)


# ---------------------------------------------------------------- Instagram
def ig_base():
    """Jeton « connexion Instagram » (IGAA…) -> graph.instagram.com ;
    jeton utilisateur système / Facebook Login for Business (EAA…) -> graph.facebook.com."""
    host = os.getenv("IG_API_HOST") or ("graph.instagram.com" if os.getenv("IG_ACCESS_TOKEN", "").startswith("IG")
                                        else "graph.facebook.com")
    return f"https://{host}/v24.0"


def publish(image_url, caption):
    hook = os.getenv("MAKE_WEBHOOK_URL")
    if hook:   # publication via Make.com (pas besoin de compte Meta Developers)
        r = requests.post(hook, json={"image_url": image_url, "caption": caption}, timeout=180)
        if not r.ok:
            raise RuntimeError(f"Make a refusé la publication: {r.status_code} {r.text[:300]}")
        return f"make:{r.text.strip()[:60] or 'ok'}"
    tok, uid = os.environ["IG_ACCESS_TOKEN"], os.environ["IG_USER_ID"]
    IG = ig_base()
    r = requests.post(f"{IG}/{uid}/media", data={"image_url": image_url, "caption": caption, "access_token": tok}, timeout=120)
    if not r.ok:
        raise RuntimeError(f"création du média refusée: {r.text}")
    cid = r.json()["id"]
    for _ in range(30):
        s = requests.get(f"{IG}/{cid}", params={"fields": "status_code", "access_token": tok}, timeout=60).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram n'a pas pu traiter l'image: {s}")
        time.sleep(5)
    r = requests.post(f"{IG}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok}, timeout=120)
    if not r.ok:
        raise RuntimeError(f"publication refusée: {r.text}")
    return r.json()["id"]


FB = "https://graph.facebook.com/v24.0"


def fb_page():
    """Trouve la Page Facebook liée au compte Instagram et son jeton de Page."""
    pid, ptok = os.getenv("FB_PAGE_ID"), os.getenv("FB_PAGE_TOKEN")
    if pid and ptok:
        return pid, ptok, "secret"
    r = requests.get(f"{FB}/me/accounts", timeout=60, params={
        "fields": "id,name,access_token,instagram_business_account", "limit": 100,
        "access_token": os.environ["IG_ACCESS_TOKEN"]})
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"accès aux Pages refusé: {data['error'].get('message')}")
    for pg in data.get("data", []):
        if (pg.get("instagram_business_account") or {}).get("id") == os.getenv("IG_USER_ID") or pg["id"] == pid:
            return pg["id"], pg["access_token"], pg.get("name")
    raise RuntimeError(f"Page liée introuvable parmi: {[p.get('name') for p in data.get('data', [])]}")


def fb_caption(caption):
    """Même texte qu'Instagram, sans le bloc de hashtags (inutile sur Facebook)."""
    keep = [l for l in caption.splitlines() if not (l.strip().startswith("#") and l.count("#") >= 3)]
    txt = "\n".join(keep).strip()
    return txt.replace("celineetsesfaiences.com (lien en bio)", "https://celineetsesfaiences.com")


def fb_publish(image_url, caption):
    pid, ptok, _ = fb_page()
    r = requests.post(f"{FB}/{pid}/photos", timeout=180, data={
        "url": image_url, "message": fb_caption(caption), "access_token": ptok})
    if not r.ok:
        raise RuntimeError(r.text[:400])
    return r.json().get("post_id") or r.json().get("id")


def fb_check():
    """Test : vérifie l'accès à la Page sans rien publier."""
    lines = []
    try:
        perms = requests.get(f"{FB}/me/permissions", timeout=60,
                             params={"access_token": os.environ["IG_ACCESS_TOKEN"]}).json()
        granted = [p["permission"] for p in perms.get("data", []) if p.get("status") == "granted"]
        lines.append("permissions: " + (", ".join(granted) or str(perms)[:300]))
    except Exception as e:  # noqa
        lines.append(f"permissions: erreur {e}")
    try:
        pid, _, name = fb_page()
        lines.append(f"page: OK {name} ({pid})")
    except Exception as e:  # noqa
        lines.append(f"page: ECHEC {e}")
    return "\n".join(lines)


def refresh_token():
    """Prolonge le jeton (valable 60 jours). Renvoie le nouveau jeton ou None."""
    tok = os.getenv("IG_ACCESS_TOKEN")
    if not tok or not tok.startswith("IG"):   # jeton système : n'expire jamais
        return None
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": tok}, timeout=60)
    if r.ok and r.json().get("access_token"):
        return r.json()["access_token"]
    log("rafraîchissement du jeton impossible:", r.text[:300])
    return None


# ---------------------------------------------------------------- main
def wait_until_post_time():
    now = dt.datetime.now(PARIS)
    target = now.replace(hour=POST_HOUR, minute=0, second=0, microsecond=0)
    if now.hour < POST_HOUR - 1 or now.hour > POST_HOUR:
        return False      # ce déclenchement n'est pas le bon (heure d'été/hiver)
    delay = (target - now).total_seconds()
    if delay > 0:
        log(f"attente de {POST_HOUR}h00 ({int(delay)} s)")
        time.sleep(delay)
    return True


def main(stage):
    state = load_state()
    today = dt.datetime.now(PARIS).date().isoformat()

    if stage == "prepare":
        if not FORCE:
            if dt.datetime.now(PARIS).hour not in (POST_HOUR - 1, POST_HOUR):
                log("pas l'heure (autre fuseau saisonnier), rien à faire")
                return
            # seule la publication automatique de 18h compte (une publication manuelle "force" n'empêche pas celle du soir)
            if any(h["date"] == today and h.get("published") and not h.get("manual") for h in state["history"]):
                log("déjà publié aujourd'hui")
                return
        # "publier le test" : si un test a été fait aujourd'hui, on publie CETTE image telle quelle
        last = state["history"][-1] if state["history"] else {}
        if FORCE and not DRY and last.get("date") == today and last.get("test") and not last.get("published"):
            last.pop("test", None)
            last["manual"] = True
            if QUEUE.exists():   # retire la pièce de la liste si elle y est en 1re position
                lines = QUEUE.read_text(encoding="utf-8").splitlines()
                for i, l in enumerate(lines):
                    if l.strip() and not l.strip().startswith("#"):
                        if (match_product(l.partition("|")[0], products()) or {}).get("slug") == last["slug"]:
                            lines.pop(i)
                            QUEUE.write_text("\n".join(lines) + "\n", encoding="utf-8")
                        break
            save_state(state)
            log("publication de l'image du test:", last["file"])
            return
        items = products()
        p = pick_product(items, state)
        ad = art_direction(p, state)
        img = fetch_image(p)
        if p.get("_decor"):
            ad.update(concept="perso", cat="perso", scene=p["_decor"])
        else:
            ad = ai_art_direction(p, ad, img)
        p.pop("_candidates", None)
        log("pièce:", p["name"], "|", ad["concept"], "|", ad["angle"], "|", ad["light"])
        prompt = image_prompt(p, ad)
        if os.getenv("OPENAI_API_KEY"):
            raw = generate_image(img, prompt)
        else:
            log("pas de clé OpenAI : image produit brute (test)")
            raw = img
        n = len(state["history"])
        final = finish_image(raw, signature=False)
        POSTS.mkdir(exist_ok=True)
        name = f"{today}-{p['slug'].strip('-')}.jpg"
        (POSTS / name).write_bytes(final)
        caption = write_caption(p, ad['scene'], final)
        (POSTS / name.replace(".jpg", ".txt")).write_text(caption)
        entry = {"date": today, "slug": p["slug"], "name": p["name"], "file": f"posts/{name}",
                 **ad, "published": False}
        if FORCE:
            entry["manual"] = True
        if DRY:
            entry["test"] = True   # un test ne compte jamais comme une publication
        # on ne supprime jamais un post réellement publié
        state["history"] = [h for h in state["history"]
                            if not (h["date"] == today and not h.get("published"))] + [entry]
        state["history"] = state["history"][-400:]
        save_state(state)
        if DRY and os.getenv("IG_ACCESS_TOKEN"):
            (POSTS / "facebook-test.txt").write_text(fb_check() + "\n", encoding="utf-8")
        log("prêt:", name)

    elif stage == "publish":
        entry = next((h for h in reversed(state["history"]) if h["date"] == today), None)
        if not entry or entry.get("published"):
            log("rien à publier")
            return
        if DRY:
            log("DRY_RUN : visuel et légende prêts dans", entry["file"], "— non publié")
            return
        if not FORCE and not wait_until_post_time():
            log("hors créneau, publication annulée")
            return
        repo = os.environ["GITHUB_REPOSITORY"]
        branch = os.getenv("GITHUB_REF_NAME", "main")
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/{entry['file']}"
        caption = (ROOT / entry["file"].replace(".jpg", ".txt")).read_text()
        media_id = publish(url, caption)
        entry["published"] = True
        entry["media_id"] = media_id
        save_state(state)
        log("publié sur Instagram ✔", media_id)
        if os.getenv("FACEBOOK", "1") != "0":
            try:
                entry["fb_post_id"] = fb_publish(url, caption)
                log("publié sur Facebook ✔", entry["fb_post_id"])
            except Exception as e:  # noqa — Facebook ne doit jamais bloquer Instagram
                entry["fb_error"] = str(e)[:300]
                log("Facebook: échec —", e)
            save_state(state)

    elif stage == "refresh":
        new = refresh_token()
        if new:
            Path(os.getenv("TOKEN_OUT", "/tmp/new_token")).write_text(new)
            log("jeton prolongé")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "prepare")
