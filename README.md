# Agent Instagram — Céline et ses faïences

Tous les jours à **18h00 (Paris)**, l'agent :
1. choisit une pièce de celineetsesfaiences.com, jamais la même qu'avant, et ne reprend une pièce qu'une fois tout le catalogue publié ;
2. la met en scène avec une IA image (OpenAI `gpt-image-2`), en piochant dans 34 décors (studio, nature, lifestyle, atelier, éditorial, graphique, saisons) plus 2 décors propres à chaque pièce. Un décor ne revient jamais avant 25 posts, et la catégorie, l’angle de vue et la lumière changent chaque jour (voir `styles.json`) ;
3. recadre l'image au format Instagram 4:5 (1080×1350) et ajoute une signature discrète une fois sur 4 ;
4. écrit la légende, avec la voix de Céline, le prix, le lien et les hashtags ;
5. publie sur Instagram.

Tout tourne gratuitement sur GitHub. Ton ordinateur peut rester éteint.

---

## Installation (environ 30 min, une seule fois)

### 1. Instagram en compte professionnel
Dans l'app : **Paramètres → Type de compte et outils → Passer à un compte professionnel** (Créateur ou Entreprise).

### 2. Clé OpenAI (pour les images et les légendes)
- Va sur https://platform.openai.com → **API keys** → **Create new secret key**, puis copie la clé.
- Dans **Billing**, ajoute 10 € de crédit. Compte quelques centimes par visuel (soit quelques euros par mois) ; vérifie les tarifs actuels sur la page OpenAI.

### 3. Connexion Instagram via Make.com (sans compte développeur Meta)
1. **Relier Instagram à une page Facebook** (obligatoire pour Make). Dans l'app Instagram : **Paramètres → Espace Comptes → Comptes**, ajoute le Facebook. Ensuite, sur Facebook, crée la page « Céline et ses faïences » si elle n'existe pas, puis relie-la au compte Instagram dans les paramètres de la page → **Comptes liés → Instagram**.
2. Crée un compte gratuit sur https://www.make.com → **Create a new scenario**.
3. Premier module : **Webhooks → Custom webhook** → **Add** → copie l'URL du webhook, puis **Save**.
4. Clique sur le **+** à droite : **Instagram for Business (Facebook login) → Create a Photo Post**. Connecte-toi avec le Facebook de ta maman et choisis le compte `celineetsesfaiences`.
   - **Photo URL** = `image_url`, **Caption** = `caption` (champs du webhook).
5. Dernier module : **Webhooks → Webhook response** (Status 200).
6. En bas à gauche, **active le scénario (ON)** et choisis « Immediately as data arrives ».

*(Si tu as un compte Meta Developers, tu peux aussi utiliser `IG_ACCESS_TOKEN` + `IG_USER_ID` à la place de Make.)*

### 4. GitHub
1. Crée un compte sur https://github.com, puis **New repository** → nom `insta-faiences` → **Public** (obligatoire : Instagram doit pouvoir lire l'image) → Create.
2. **Add file → Upload files** → glisse tout le contenu de ce dossier, y compris le dossier `.github`. Sur Mac, fais Cmd+Maj+. pour voir les dossiers cachés.
3. **Settings → Secrets and variables → Actions → New repository secret**. Ajoute :

| Nom | Valeur |
|---|---|
| `OPENAI_API_KEY` | ta clé OpenAI |
| `MAKE_WEBHOOK_URL` | l'URL du webhook Make |

4. Toujours dans **Secrets and variables → Actions**, onglet **Variables**, crée `DRY_RUN` = `1` pour la période de test.

### 5. Tester
**Actions → Post Instagram 18h → Run workflow**, coche **force** et **dry_run**, puis lance.
Après environ 2 min, le visuel et sa légende apparaissent dans le dossier `posts/` du dépôt, sans être publiés.

Quand le rendu te plaît, passe la variable `DRY_RUN` à `0`. À partir de là, ça publie seul tous les jours à 18h.

---

## Réglages

- **Changer l'heure** : variable `POST_HOUR` (défaut 18), et décale les deux lignes `cron` dans `.github/workflows/daily.yml`.
- **Décors** : ajoute ou retire des concepts dans `styles.json` ; décors propres à une pièce : `scenes` dans `catalog.json`.
- **Nouvelle pièce** : rien à faire si elle est sur le site, elle est détectée toute seule. Pour une pièce absente du site, mets sa photo dans `assets/extra/` et ajoute une entrée avec `"image": "assets/extra/xxx.jpg"`.
- **Mettre en pause** : **Actions → Post Instagram 18h → ⋯ → Disable workflow**.
- **Historique** : `state.json`, avec la date, la pièce, le décor et l'ID de chaque post.

⚠️ Le prompt impose à l'IA de garder la pièce identique (forme, émail, motifs), mais relis les premiers visuels : un client doit recevoir ce qu'il a vu.
