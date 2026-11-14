# Guide utilisateur — Reproduire le benchmark Bedrock

**Ce guide : [English](USERGUIDE.md) · [中文](USERGUIDE.zh-CN.md) · Français**

Ce guide reproduit de zéro la mesure complète : la matrice d'agents de 72
exécutions (4 modèles × 6 travaux × 3 répétitions, ≈ 39 $ au tarif affiché,
≈ 45–90 minutes), l'exécution de fumée optionnelle, le tableau de bord en temps
réel, et les artefacts finaux (`out/results.json`, `out/results.md`).

Toutes les commandes s'exécutent depuis `demo/` (racine de ce dépôt).

## Référence rapide (procédure complète)

```bash
# 0. installation unique
uv venv /tmp/p117/venv --python 3.11
uv pip install --python /tmp/p117/venv/bin/python httpx boto3 pytest "tzdata==2025.2"
aws sts get-caller-identity --profile <your-aws-profile>                      # identifiants valides ?
k3eval proxy --region us-west-2 --profile <your-aws-profile> --host 127.0.0.1 --port 8787 \
  --model us.moonshotai.kimi-k3 &                                  # proxy SigV4 pour Kimi/GLM

# 1. prévol : les miroirs de référence doivent être verts (voir §3)
cd reference/t4-scheduling && /tmp/p117/venv/bin/python -m pytest -q test_hidden_scheduling.py && cd ../..

# 2. exécution de fumée (1 exécution, ≈ 0,10–0,40 $)
P117_OUT=out-smoke-v2 /tmp/p117/venv/bin/python harness/run_matrix.py \
  --models kimi-k3.1-pro --tasks t1 --reps 1

# 3. matrice complète (72 exécutions, ≈ 39 $) — terminal 1
/tmp/p117/venv/bin/python harness/run_matrix.py --reps 3

# 4. tableau de bord temps réel — terminal 2 (optionnel mais recommandé)
python3 harness/dashboard.py --port 8788       # ouvrir http://127.0.0.1:8788

# 5. artefacts finaux
/tmp/p117/venv/bin/python harness/summarize.py
```

## 1. Prérequis

| Exigence | Détails | Vérification |
|---|---|---|
| Python 3.11 | `taskrepo/pyproject.toml` épingle `>=3.11,<3.12` ; le runtime de notation est 3.11 avec `tzdata==2025.2`. `uv` est le moyen le plus rapide. | `uv python list \| grep 3.11` |
| Identifiants AWS | Un profil AWS CLI autorisé à appeler Bedrock en **us-west-2**. Ce guide utilise l'espace réservé `<your-aws-profile>` ; remplacez-le partout par le nom de votre profil AWS CLI. | `aws sts get-caller-identity --profile <your-aws-profile>` |
| Accès modèles Bedrock | Activés en us-west-2 : `global.moonshotai.kimi-k3.1`, `us.zai.glm-5.3`, `us.anthropic.claude-opus-5-5`, `global.anthropic.claude-fable-5-2` (Console → Bedrock → Model access). GLM 5.3 a en outre une porte d'éligibilité entreprise et un routage inter-région uniquement. | Console |
| Proxy SigV4 `k3eval` | Kimi et GLM sont servis sur la surface compatible OpenAI de Bedrock, atteinte via un proxy signant local sur `127.0.0.1:8787`. Les Claude utilisent boto3 Converse directement et n'ont pas besoin du proxy (mais démarrez-le quand même — le harnais y lit le profil AWS). | `ps aux \| grep "k3eval proxy"` |
| Budget | ≈ 39 $ tarif affiché pour la matrice + ≈ 6 $ de fumée. Le harnais arrête tout nouvel appel à **90 $** (plafond **100 $**). | — |

> **Note sur l'environnement d'origine :** d'anciennes notes citaient
> `/tmp/p116/venv`. Cet environnement est cassé (l'interpréteur ne résout plus
> ses propres site-packages). Créez-en un neuf selon la référence rapide ; ne
> tentez pas de le réparer.

## 2. Installation

```bash
uv venv /tmp/p117/venv --python 3.11
uv pip install --python /tmp/p117/venv/bin/python httpx boto3 pytest "tzdata==2025.2"
```

Dépendances volontairement minimales :

| Paquet | Utilisé par |
|---|---|
| `httpx` | `OpenAIAdapter` → proxy (Kimi, GLM) |
| `boto3` | `ConverseAdapter` → Bedrock Converse (Opus, Fable) |
| `pytest` | outil `run_tests` de l'agent et noteur |
| `tzdata==2025.2` | base tz épinglée pour les tests de planification DST |

Le tableau de bord (`harness/dashboard.py`) n'utilise que la **bibliothèque
standard** — tout Python 3 convient, sans environnement virtuel.

Démarrer le proxy signant (terminal 0, le laisser tourner) :

```bash
k3eval proxy --region us-west-2 --profile <your-aws-profile> --host 127.0.0.1 --port 8787 \
  --model us.moonshotai.kimi-k3
```

`run_matrix.py` lit la valeur `--profile` dans la ligne de commande du processus
proxy à l'exécution (`_proxy_profile()`) ; le harnais n'écrit jamais
d'identifiants sur le disque.

## 3. Prévol (≈ 2 minutes)

1. **Proxy en vie :** `ps aux | grep "k3eval proxy"` montre le processus, et
   `lsof -iTCP:8787 -sTCP:LISTEN` montre l'écoute.
2. **Identifiants valides :** `aws sts get-caller-identity --profile <your-aws-profile>`.
   Profil SSO : `aws sso login --profile <your-aws-profile>` d'abord.
3. **Miroirs de référence verts** — prouve que travaux, tests cachés et noteur
   sont cohérents avant de dépenser. Chaque miroir de `reference/<job>/`
   contient une implémentation complète. Deux layouts, deux recettes :

   ```bash
   # t1–t4 : la suite cachée est déjà à la racine du miroir — l'exécuter directement
   for job in t1-eligibility t2-privacy t3-audit t4-scheduling; do
     ( cd reference/$job && /tmp/p117/venv/bin/python -m pytest -q -p no:cacheprovider \
       $(ls test_hidden_*.py) | tail -1 )
   done
   # t5–t6 : injecter la suite cachée dans un répertoire tests/
   for job in t5-safety t6-governance; do
     ( cd reference/$job && mkdir -p tests && cp ../../tasks/$job/test_hidden_*.py tests/ \
       && /tmp/p117/venv/bin/python -m pytest -q -p no:cacheprovider | tail -1; rm -rf tests )
   done
   ```

   Attendu : **19 / 33 / 57 / 26 / 15 / 45 réussis = 195 tests cachés**, comme
   le README. Ne **pas** copier un fichier caché dans `tests/` quand une copie
   est déjà à la racine du miroir : les modules de même nom entrent en collision
   (`import file mismatch`). Les miroirs sont indépendants — toujours exécuter
   depuis l'intérieur d'un seul miroir ; runtime de notation 3.11, `tzdata==2025.2`.

## 4. Exécution de fumée (≈ 0,10–0,40 $, ≈ 1 minute)

Un aller-retour complet — session d'agent, notation, registre, artefacts — vers
un répertoire jetable :

```bash
P117_OUT=out-smoke-v2 /tmp/p117/venv/bin/python harness/run_matrix.py \
  --models kimi-k3.1-pro --tasks t1 --reps 1
```

Ligne de log attendue (les temps varient) :

```
HH:MM:SS kimi-k3.1-pro__t1-eligibility__r1: PASS stop=finish calls=9 fmt=0 api_err=0 $0.151 41s hidden 19/19 | meter $0.15
```

Vérifier les artefacts : `out-smoke-v2/results.jsonl` (1 ligne), `spend.jsonl`
(une ligne par appel), `events.jsonl` (run_start + run_end), `expected.json`,
et `runs/<run_id>/{transcript.json,diff.patch}`.
`harness/summarize.py` ne lit que `out/`, jamais le répertoire de fumée.

## 5. Matrice complète (72 exécutions, ≈ 39 $)

```bash
/tmp/p117/venv/bin/python harness/run_matrix.py --reps 3
```

À quoi s'attendre :

- **Plan :** `tasks/tasks.json` × 4 modèles × répétitions 1–3. Douze couloirs
  en parallèle — un par (modèle, répétition) ; travaux séquentiels dans un couloir.
- **Durée :** ≈ 45–90 min. Les couloirs GLM sont les plus lents (≈ 323–571 s/exécution).
- **Reprise :** tout `run_id` déjà dans `out/results.jsonl` est sauté. Ctrl-C
  puis relancer la même commande à tout moment. La dépense d'une exécution
  interrompue reste dans `spend.jsonl` (append-only) — sa reprise la facture
  donc deux fois, par construction.
- **Garde-fous budget :** à 90 $, plus aucun nouvel appel (`stop=budget_hard_stop`).
- **Identifiants expirés :** le couloir concerné s'arrête avec
  `credentials_expired`, les autres continuent. Reconnexion puis reprise —
  les exécutions terminées ne sont pas répétées.
- Sous-ensembles utiles : `--models kimi-k3.1-pro,glm-5.3`, `--tasks t1,t4`, `--reps 1`.

## 6. Tableau de bord temps réel

Dans un second terminal (tout Python 3) :

```bash
python3 harness/dashboard.py --port 8788
# ouvrir http://127.0.0.1:8788
```

`--out` doit pointer le même répertoire que celui écrit par le runner
(le `P117_OUT` du runner, `demo/out` par défaut) :

```bash
python3 harness/dashboard.py --out out-smoke-v2 --port 8789   # suivre la fumée à la place
```

La page se rafraîchit toutes les 2 s et est strictement en lecture seule :

| Panneau | Contenu |
|---|---|
| Bandeau | terminées / réussies / échouées / en cours / en attente ; dépense vs barre des 90 $ |
| Cartes modèle | réussites et taux en direct, coût registre, coût/réussite, médianes appels & durée, erreurs API, tours sans outil ; badge « open weights » |
| Matrice | un point par répétition : **vert** réussite · **rouge** échec · **bleu pulsant** en cours · **gris** en attente. Survol : stop, appels, durée, coût, score caché |
| En cours | exécutions en vol : appels, dépense, secondes écoulées/inactives |
| Événements | les 15 derniers `run_start` / `run_end` / `run_skip` |

Sources (toutes dans `--out`) : `expected.json` (manifeste écrit au démarrage),
`events.jsonl`, `results.jsonl` (réussite/échec faisant foi), `spend.jsonl`
(coût en direct et appels en vol). `--models` / `--reps` ne sont que des replis
pour les répertoires sans `expected.json` ; le manifeste l'emporte toujours.

## 7. Synthèse finale

```bash
/tmp/p117/venv/bin/python harness/summarize.py
```

Écrit `out/results.json` et `out/results.md` (et imprime le tableau). Seuls les
quatre modèles officiels sont comptés. Chiffres de la mesure officielle
(2026-11-13) : Kimi 15/18 (0,257 $/réussite), GLM 9/18 (1,021 $), Opus 15/18
(0,418 $), Fable 18/18 (1,111 $) ; ré-exécution (2026-11-14) : 14/10/14/18.
De petits écarts sont normaux (température, retries, latence) ; les grands
écarts appellent une investigation avant toute comparaison.


## 8. Référence des artefacts

Dans chaque répertoire de sortie (`out/`, `out-smoke-v2/`, tout `P117_OUT`) :

| Fichier | Écrit par | Contenu |
|---|---|---|
| `expected.json` | runner, au démarrage | Exécutions planifiées en ordre de couloir (`run_id`, `model`, `task`, `rep`) |
| `events.jsonl` | runner | Un événement par ligne : `run_start`, `run_end` (avec `passed`, `stop`, `calls`, `usd`, `wall_s`), `run_skip` |
| `results.jsonl` | runner | Une ligne par exécution terminée : comptages, jetons `usage`, `usd`, `wall_s`, `stop`, `format_failures`, `api_errors`, `grade` (suite cachée + complète), `pass`, `finished_at` |
| `spend.jsonl` | compteur | Une ligne par appel : `usage` normalisé, `usd`, `total_usd` cumulé |
| `runs/<run_id>/transcript.json` | runner | Contenu verbatim par appel, appels d'outils, résultats, usage |
| `runs/<run_id>/diff.patch` | runner | `diff -ruN` du dépôt de l'agent vs le départ vierge |
| `results.json`, `results.md` | agrégateur | Tableaux agrégés des quatre modèles officiels |

Une exécution ne passe que si la suite cachée **et** la suite complète sortent
en 0, avec ≥ 1 test réussi et zéro skip/xfail/xpass. Tout changement hors
`trialops/*.py` ou `tests/test_*.py` est un échec automatique
(`unauthorized file changes` dans la queue de notation). Raisons d'arrêt :
`finish`, `max_calls` (30), `wall_clock` (20 min), `no_tool_calls` (3 tours
inactifs consécutifs), `api_error`, `credentials_expired`, `budget_hard_stop`.

## 9. Dépannage

| Symptôme | Cause → Correctif |
|---|---|
| `No module named pytest` depuis `/tmp/p116/venv` | Environnement cassé. Créez le neuf (§2) — ne pas réparer. |
| Log `credentials expired, stopping this worker` | SSO/STS expiré → `aws sso login --profile <your-aws-profile>`, relancer la même commande ; les exécutions terminées sont sautées. |
| `503` répétés sur un profil `global.*` | Étranglement de capacité Global CRIS (a frappé Opus lors de la session d'origine). Basculer l'ID modèle dans `harness/run_matrix.py:MODELS` vers le profil US (ex. `us.anthropic.claude-opus-5-5`) **et** mettre à jour le tarif dans `harness/meter.py:PRICES` (US CRIS 4,40 $/22 $/0,22 $ vs Global 4 $/20 $/0,20 $), en documentant la substitution comme le README. |
| Couloirs Kimi/GLM tous en erreur de connexion | Proxy absent → relancer `k3eval proxy …` (§2) ; vérifier `lsof -iTCP:8787 -sTCP:LISTEN`. |
| Queue de notation `unauthorized file changes: …` | Par construction : l'agent a laissé des fichiers temporaires/sondes hors `trialops/` ou non `test_*` dans `tests/`. Échec légitime. |
| Queue de notation `ImportError: cannot import name …` | L'agent a manqué une partie de l'invite multi-parties (ex. `execute_reuse_export`). Échec légitime aussi — voir `runs/<run_id>/diff.patch`. |
| `error: tests timed out after 90s` dans une transcription | Le code de l'agent boucle probablement ; noté tel quel. |
| Le tableau de bord n'affiche que du « en attente » | `--out` incorrect : le tableau doit lire le même répertoire que le `P117_OUT` du runner. |
| Port du tableau occupé | `--port 8789` (ou tout port libre). |
| Remise à zéro | `rm -rf out out-smoke out-opus-us out-smoke-v2 out-live /tmp/p117/runs` — remet à zéro résultats **et** compteur. Aucun autre état. |

## 10. Notes de loyauté

- Figer invites, outils, limites et tests cachés **avant** une série mesurée
  (comme le font `harness/agent.py` et `tasks/`) ; les modifier en cours de
  matrice invalide les comparaisons.
- Garder `harness/meter.py:PRICES` aligné sur les tarifs AWS des routes
  réellement utilisées (Global vs US CRIS), sinon les coûts/réussite dérivent.
- Trois répétitions exposent de grands écarts de fiabilité mais ne classent pas
  des modèles à une exécution d'écart — rapporter des comptages de réussites,
  pas des impressions d'une exécution.

