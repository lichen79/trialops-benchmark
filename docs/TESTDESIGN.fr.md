# Document de conception des tests — la matrice d'agents Bedrock

**Ce document : [English](TESTDESIGN.md) · [中文](TESTDESIGN.zh-CN.md) · Français**

Ce document explique comment le benchmark de `demo/` est conçu, pourquoi chaque
décision a été prise, et comment continuer : valider, exécuter, étendre,
interpréter. Il complète `README.md` (résultats), `docs/USERGUIDE.md`
(opérations) et `out-live/REPORT.md` (analyse de la dernière mesure).

## Comment lire ce document

**Commencez par la partie introductive.** Elle explique l'objectif, le déroulé
d'une exécution et les six scénarios métier en langage accessible. **L'annexe**
conserve les contrats précis, les points de pression des tests, les détails du
harnais, le guide d'extension et les schémas de données.

## 1. Objectif de conception

Sur Amazon Bedrock, comparer équitablement quatre modèles sur leur capacité à
réaliser un travail d'ingénierie Python dans **le même environnement de codage
réel, avec la même invite, les mêmes outils et les mêmes limites d'exécution**.

Il ne s'agit pas simplement de répondre à des questions de programmation. Chaque
modèle agit comme un ingénieur informatique : il inspecte le dépôt, comprend la
structure et les exigences, conçoit une solution, crée ou modifie le code,
exécute les tests, analyse les erreurs et itère. Une suite pytest cachée,
indépendante et faisant autorité, décide ensuite si le travail est réellement
terminé.

> **Principe central : ne pas évaluer la qualité du discours du modèle, mais
> vérifier si le code livré passe les tests.**

Chaque exécution autorise au maximum **30 appels au modèle**, **20 minutes** et
**16 384 jetons de complétion**. Tous les modèles partagent la même invite
système, la même boucle d'agent et six outils : `list_files`, `read_file`,
`write_file`, `edit_file`, `run_tests` et `finish`.

Une mesure comprend :

```text
6 travaux × 4 modèles × 3 répétitions = 72 exécutions
```

Le dépôt contient deux mesures indépendantes : la matrice officielle
(2026-11-13) et la ré-exécution en direct (2026-11-14), soit 144 exécutions.

## 2. Que teste une exécution ?

1. **Lire** le code existant et la structure du projet.
2. **Analyser** les exigences et choisir une approche d'implémentation.
3. **Créer ou modifier** du code Python.
4. **Exécuter les tests** et inspecter les erreurs.
5. **Corriger et itérer** dans les limites fixées.
6. **Noter indépendamment** : les changements de production sont appliqués à un
   checkout vierge et évalués par les suites visible et cachée faisant autorité.

Les tests visibles aident l'agent à comprendre le comportement de base. Les
tests cachés l'empêchent de réussir avec une implémentation seulement plausible
ou optimisée pour ses propres tests. Les tests et la configuration pytest écrits
par l'agent n'entrent jamais dans le checkout de notation.

## 3. Un système, six travaux, deux liaisons d'intégration

Les six travaux sont les modules d'un même backend fictif d'**opérations
d'essais cliniques**, et non six exercices indépendants :

```text
Trial Operations Platform
├── T1 · Eligibility   — sélection des sujets et règles de consentement
├── T2 · Privacy       — export de recherche respectueux de la vie privée
├── T3 · Audit         — journal inviolable et restauration
├── T4 · Scheduling    — visites, fuseaux horaires et capacité
├── T5 · Safety        — triage des suivis d'événements de sécurité
└── T6 · Governance    — flux d'approbation de réutilisation des données

T2 Privacy → T6 Governance : l'export doit passer par le flux d'approbation.
T4 Scheduling → T1 Eligibility : toute réservation exige d'abord l'éligibilité.
```

Chaque travail commence par une fonctionnalité autonome. Les plus difficiles
ajoutent ensuite une extension d'intégration. On distingue ainsi le modèle qui
sait écrire une fonction Python de celui qui sait lire plusieurs modules,
respecter les contrats d'API et terminer un flux métier. T2 et T4 sont les cas
intermodules les plus nets ; T3 ajoute la restauration de la chaîne d'audit et
T6 fournit le point d'extension `executor` utilisé par T2.

## 4. Les six travaux en langage métier

### T1 — Eligibility : sélection des sujets

- **Scénario.** Déterminer si un sujet peut participer à un protocole à une date
  donnée, selon son consentement éclairé, son âge, ses résultats de
  laboratoire et ses critères d'exclusion.
- **Implémentation.** `evaluate_subject(subject, protocol, on)` retourne
  l'éligibilité, les raisons d'échec ordonnées et la version de consentement
  sélectionnée.
- **Difficultés.** Choisir le consentement le plus récent sans repli sur une
  version antérieure ; calculer l'âge exact au lieu de soustraire les années ;
  gérer les anniversaires bissextiles et le consentement du jour même ; inclure
  les bornes d'âge et de laboratoire ; produire les raisons dans l'ordre exigé ;
  retourner la version sélectionnée même si elle est incorrecte.
- **Capacité évaluée.** Un petit moteur de règles métier : logique de dates,
  validation des entrées et sortie déterministe.

### T2 — Privacy : export de recherche avec gouvernance

- **Scénario.** Exporter des données d'essai pour une autre étude sans divulguer
  noms, adresses e-mail, dates de naissance complètes, identifiants de sujets,
  consentements ou secrets.
- **Implémentation.** `export_subjects` filtre selon la finalité du
  consentement, le retrait et la rétention ; produit un pseudonyme de 16
  caractères hexadécimaux avec HMAC-SHA256 sur `purpose:id` ; n'exporte que les
  champs autorisés et hache un manifeste JSON canonique.
  `execute_reuse_export` doit passer exactement une fois par T6 et appeler
  l'export exactement une fois via son executor.
- **Difficultés.** Séparation de domaine par finalité, sérialisation canonique,
  IDs dupliqués ou invalides, non-mutation, et messages d'erreur ne révélant
  aucune donnée sensible. Un échec d'export ne doit pas laisser un faux état
  « exécuté ».
- **Capacité évaluée.** Minimisation des données, pseudonymisation, hygiène
  cryptographique, contrats d'API et exécution intermodules exactement une fois.

### T3 — Audit : intégrité et restauration de confiance

- **Scénario.** Enregistrer qui a fait quoi et quand, de manière à détecter
  toute falsification ultérieure.
- **Implémentation.** `AuditChain` relie les enregistrements avec
  `previous_hash` et un hachage SHA256 canonique. `verify` retourne des codes
  d'erreur déterministes ; `from_records` restaure un snapshot et poursuit les
  ajouts.
- **Difficultés.** JSON canonique, normalisation UTC sans microsecondes,
  contrôles de séquence et de liaison, détection des doublons, et indépendance
  de la restauration par copie profonde. Les ancres de confiance
  `expected_head` et `expected_length` détectent aussi les éditions re-hachées
  et les troncatures de suffixe. Les erreurs de restauration respectent le
  format exact `INVALID_CHAIN:`.
- **Capacité évaluée.** Structures append-only, intégrité cryptographique,
  discipline de sérialisation et conception de la reprise.

### T4 — Scheduling : visites et réservations atomiques

- **Scénario.** Planifier une série de visites pour un sujet éligible en tenant
  compte des fins de mois, des fermetures, des transitions horaires et d'une
  capacité quotidienne limitée.
- **Implémentation.** `plan_visits` calcule les candidats et alloue les jours ;
  `book_subject_visits` vérifie d'abord l'éligibilité T1 puis engage la
  capacité.
- **Difficultés.** Chaque candidat mensuel dérive du jour d'origine
  (31 janv. → 28 févr. → 31 mars, jamais 28 mars). Les heures inexistantes au
  changement DST avancent de la durée du trou ; les heures ambiguës utilisent
  `fold=0`. La capacité n'est engagée qu'après le succès complet, décrémentée
  seulement pour les dates déjà listées et reste inchangée en cas d'échec. Le
  message d'inéligibilité a un format exact.
- **Capacité évaluée.** Arithmétique calendaire, zoneinfo/DST, allocation de
  ressources, atomicité de type transaction et rollback.

### T5 — Safety : triage des événements de suivi

- **Scénario.** Fusionner un rapport de sécurité initial et ses suivis en un
  seul cas, plutôt que de les traiter comme des événements indépendants.
- **Implémentation.** `triage_events(events, now)` suit les liens
  `follow_up_of`, valide chaque chaîne et produit un cas consolidé.
- **Difficultés.** Rejeter cycles, racines manquantes, IDs dupliqués et chaînes
  mélangeant sujets ou codes d'événement. Utiliser l'onset le plus tôt et la
  gravité la plus élevée ; calculer les délais de 7/15/30 jours depuis cet
  onset, jamais depuis l'arrivée d'un suivi ; conserver sources et incertitude ;
  calculer le retard strictement par `report_by < now` ; produire une sortie
  indépendante de l'ordre d'entrée.
- **Capacité évaluée.** Traversal de graphes, détection de cycles, corrélation
  d'événements, agrégation déterministe et logique de SLA temporel.

### T6 — Governance : flux d'approbation de réutilisation

- **Scénario.** Un demandeur soumet une demande de réutilisation, un autre
  réviseur l'approuve ou la rejette, et seule une approbation autorisée et non
  expirée permet l'export.
- **Implémentation.** `apply_reuse_action` implémente
  `draft → submitted → approved → executed`, avec les états rejeté et expiré,
  ainsi qu'un crochet `executor` optionnel utilisé par T2.
- **Difficultés.** Séparation des tâches, contrôles de rôle, `privacy_plan`
  exact, métadonnées représentatives et complètes, `quality_score >= 0.95`, et
  deux horloges distinctes : expiration de l'approbation à 30 jours et
  expiration de la rétention. Retourner une copie profonde, ajouter
  l'historique UTC et gérer l'échec de l'executor sans enregistrer un faux
  succès.
- **Capacité évaluée.** Machines à états finis, contrôle d'accès par rôle,
  transitions immuables, gestion de l'expiration et contrats de callback.

## 5. Carte des capacités

| Travail | Capacité technique centrale | Analogue réel | Focus d'intégration |
|---|---|---|---|
| T1 | Règles métier et logique de dates exacte | Moteur de règles / service de sélection | — |
| T2 | Pseudonymisation, HMAC, sortie canonique | Vie privée et data engineering | Approbation T6 et executor |
| T3 | Chaînes de hachage, intégrité, reprise | Journal d'audit append-only | Restauration d'un snapshot fiable |
| T4 | Planification, DST, allocation atomique | Réservation et ordonnancement de ressources | Porte d'éligibilité T1 |
| T5 | Validation de graphes et agrégation | Traitement d'événements / gestion de cas | — |
| T6 | FSM, RBAC, séparation des tâches | Plateforme d'approbation d'entreprise | Exécution de l'export T2 |

Cette carte est une analyse qualitative de la complexité, pas un score officiel
de difficulté.

## 6. Pourquoi la mesure est crédible

### Tests visibles et tests cachés

| Travail | T1 | T2 | T3 | T4 | T5 | T6 | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| Cas cachés | 19 | 33 | 57 | 26 | 15 | 45 | **195** |

Le noteur applique seuls les changements `trialops/*.py` à un checkout vierge,
injecte les tests cachés et exige une sortie zéro de la suite cachée comme de la
suite complète. Les tests et la configuration pytest écrits par l'agent sont
ignorés ; les changements de fichiers non autorisés sont rejetés ; une collecte
vide, des skips, xfails ou xpasses ne comptent pas comme une réussite.

### Harnais identique et preuves auditables

Les quatre modèles utilisent les mêmes six outils et les mêmes limites. Trois
répétitions permettent de distinguer une réussite fiable d'un succès
accidentel : insuffisant pour des statistiques fines, mais suffisant pour
révéler de grands écarts de fiabilité et des échecs déterministes. Chaque
exécution conserve `transcript.json` (échanges modèle-outils), `diff.patch`
(changements réels), `results.jsonl` (résultats) et `spend.jsonl` (jetons et
coûts), rendant la mesure elle-même auditable.

## 7. Ce que montrent les deux matrices mesurées

| Modèle | Officielle (2026-11-13) | En direct (2026-11-14) |
|---|---:|---:|
| Kimi K3.1 Pro | 15/18 | 14/18 |
| GLM 5.3 | 9/18 | 10/18 |
| Claude Opus 5.5 | 15/18 | 14/18 |
| Claude Fable 5.2 | **18/18** | **18/18** |
| **Total** | **57/72** | **56/72** |

- **Fable** est le seul modèle avec deux grands chelems.
- **Les échecs de Kimi sur T4** ne signifient pas une incapacité à implémenter la
  planification : son code réussit 25/26 tests cachés, mais sa chaîne d'erreur
  `INELIGIBLE:` manque le contrat exact. Une logique correcte ne garantit pas
  une livraison conforme au contrat.
- **Les échecs d'Opus sur T2** révèlent un autre écart : la fonction d'export
  locale est terminée, mais `execute_reuse_export` n'a jamais été implémentée.
  Terminer une partie d'une exigence multi-étapes n'est pas terminer le travail.
- **Les échecs de GLM sont surtout des échecs de processus** : plafond de 30
  appels atteint, fichiers temporaires rejetés par le noteur, et absence de
  convergence même quand une grande partie du code est correcte.

La décomposition par modèle et par travail figure dans `out-live/REPORT.md`, §6.

## 8. Périmètre et parcours de lecture recommandé

Ce benchmark évalue **du travail Python bien spécifié par un agent de codage**,
et non l'ensemble de l'ingénierie logicielle : architecture de grands systèmes,
systèmes distribués, concurrence de vraies bases de données, déploiement,
maintenance longue ou collaboration d'équipe ne sont pas couverts. Trois
répétitions éclairent la fiabilité, sans permettre un classement précis.

Ordre de lecture conseillé :

1. `README.md` — objectif, modèles, résultats et carte du dépôt.
2. `tasks/tasks.json` — les invites exactes utilisées pendant la mesure.
3. `harness/agent.py` — boucle d'agent et `grade()` (il n'existe pas de
   `grader.py` séparé).
4. `harness/run_matrix.py` — couloirs, reprise et événements de cycle de vie.
5. `harness/meter.py` — prix des jetons, coûts et arrêt budgétaire dur.
6. `out-live/REPORT.md` — preuves d'échecs et analyse en cinq dimensions.

L'annexe conserve les détails : principes et architecture (§§1–3), contrats
précis et conception des tests (§4), agent et notation (§5), comptage et
exécution (§§6–7), interprétation statistique (§8), guides d'extension (§9) et
schémas de données (§10).

# Annexe — Conception technique détaillée

## 1. Objectif et non-objectifs





**Ce qui est mesuré.** La fiabilité et le coût de quatre modèles de fondation
servis sur Amazon Bedrock, agissant comme agents de codage autonomes sur six
travaux Python bien spécifiés dans un même dépôt — invite, outils et limites
identiques, la correction étant décidée uniquement par des suites pytest
cachées faisant autorité.

**Non-objectifs délibérés :**

- Pas de l'ingénierie logicielle de longue haleine — ce sont six travaux plus
  difficiles mais bien spécifiés, faisables en ≤ 30 appels.
- Pas une comparaison d'agents de fournisseurs — la boucle est la même boucle
  minimale pour tous ; l'agent de codage d'un fournisseur (avec ses propres
  outils/échafaudages) peut se comporter différemment.
- Pas un classement statistique serré — 3 répétitions par travail exposent de
  grands écarts de fiabilité, pas des modèles à une exécution d'écart.
- Pas une facture AWS — coûts = jetons mesurés × tarifs publics.

## 2. Principes de conception

| Principe | Mécanisme |
|---|---|
| Neutralité fournisseur | Une invite système, un ensemble d'outils, une série de limites pour tous (`harness/agent.py`) ; les adaptateurs ne font que traduire le format fil |
| Notation impossible à jouer | Notation sur checkout vierge ; tests et config pytest écrits par l'agent ignorés ; suites cachées faisant autorité |
| Tout est auditable | `transcript.json` verbatim + `diff.patch` final par exécution ; registres `results.jsonl` / `spend.jsonl` / `events.jsonl` append-only |
| Reproductibilité | Dépôt de départ figé (`taskrepo/`), invites figées (`tasks/tasks.json`), tests cachés figés (`tasks/*/test_hidden_*.py`), runtime épinglé (Python 3.11, `tzdata==2025.2`), miroirs de référence indépendants qui doivent réussir chaque test caché |
| Discipline de coût | Comptage par appel dans un registre ; arrêt dur à 90 $ (plafond 100 $) ; matrice reprenable (les run ID terminés ne sont jamais répétés) |
| Rapport honnête | Les échecs sont conservés et classés (y compris infra) ; les substitutions de route (Global→US CRIS) sont documentées avec leurs tarifs, pas masquées |

## 3. Architecture en un coup d'œil

```
                 ┌──────────────────────── run_matrix.py ────────────────────────┐
                 │  expected.json (manifeste)  12 couloirs = (modèle × rép.), pool │
                 └───────┬───────────────────────────────────────────────────────┘
                         │ par exécution : copie fraîche de taskrepo/
                 ┌───────▼────────┐    outils : list/read/write/edit/run_tests/finish
                 │  boucle agent  │ ─────────────────────────────────────────────┐
                 │  ≤30 appels    │    ┌─────────────┐      ┌─────────────────┐  │
                 │  ≤20 min       │──▶│ OpenAIAdapt.│─▶ proxy SigV4 :8787 ─▶ surface OpenAI Bedrock (Kimi, GLM)
                 └───────┬────────┘    ├─────────────┤      ├─────────────────┤  │
                         │             │ ConverseAdap│─▶ boto3 ───────────▶ Bedrock Converse (Opus, Fable)
                         │             └─────────────┘      └─────────────────┘  │
                         │ meter.record() par appel ─▶ spend.jsonl (arrêt 90 $) │
                 ┌───────▼────────┐                                              │
                 │    grade()     │  checkout vierge + seuls trialops/*.py       │
                 │  cachée+compl. │  + suite cachée injectée → verdict exit-code │
                 └───────┬────────┘                                              │
                         ▼
            results.jsonl ─▶ summarize.py ─▶ results.json / results.md
            events.jsonl ─▶ dashboard.py  ─▶ http://127.0.0.1:8788 (en direct)
```

Les données circulent dans un seul sens : runner → registres → lecteurs. Les
lecteurs (agrégateur, tableau de bord) n'écrivent jamais ; le tableau de bord
est en lecture seule par construction.

## 4. Conception des travaux

### 4.1 Le dépôt jetable (`taskrepo/`)

Un paquet synthétique d'opérations d'essais cliniques (`trialops`) avec six
modules et des tests visibles de base. **Aucune donnée patient réelle** — tout
est fictif. `pyproject.toml` épingle `>=3.11,<3.12` et `tzdata==2025.2` ;
`testpaths = ["tests"]`. Chaque exécution part d'une copie fraîche de ce dépôt :
les exécutions sont indépendantes et le `diff.patch` final capture l'intégralité
du travail de l'agent.

### 4.2 Les six travaux (`tasks/tasks.json`)

Chaque travail est une longue invite **en deux parties** : une fonctionnalité de
base autonome, puis une extension d'intégration réaliste qui se compose avec les
autres modules. Le dépôt de départ omet délibérément ces API intégrées.

| Travail | Fonctionnalité de base | Extension d'intégration (le discriminant) |
|---|---|---|
| t1-eligibility | `evaluate_subject` : consentement versionné + bornes exactes âge/labo + ordre de raisons déterministe | (l'invite ajoute des exigences de tests bornes/années bissextiles) |
| t2-privacy | `export_subjects` : filtrage consentement/purge, pseudonymes HMAC-SHA256, hachage de manifeste canonique | `execute_reuse_export` : délègue exactement une fois à `governance.apply_reuse_action` |
| t3-audit | `AuditChain` append + `verify` à codes d'erreur stables | `verify(expected_head/length)` + `AuditChain.from_records` restauration et poursuite |
| t4-scheduling | `plan_visits` : clamping fin de mois, zones DST via zoneinfo, capacité/fermetures | `book_subject_visits` : porte d'éligibilité + réservation de capacité persistante atomique |
| t5-safety | `triage_events` : fusion des chaînes de suivi, priorité de gravité, délais de déclaration | (l'invite ajoute des règles `now` aware/dépassé) |
| t6-governance | `apply_reuse_action` : machine à états gardée par rôles, codes d'erreur lisibles machine | `privacy_plan` remplace le booléen ; crochet `executor=` pour l'export |

Pourquoi cette structure en deux parties : elle sépare « sait écrire une
fonction » de « lit toute l'invite et compose entre modules ». Les résultats le
confirment : les échecs se concentrent exactement sur les parties intégrées
(Opus 0/6 sur t2 sur les deux sessions — n'a jamais implémenté
`execute_reuse_export` ; Kimi/GLM 0/6 sur l'intégration de réservation de t4).

Les sous-sections suivantes détaillent le contrat, l'intention de conception et
le comportement mesuré de chaque travail sur les deux sessions enregistrées
(officielle 2026-11-13 et direct 2026-11-14 ; preuves d'échecs dans
`out-live/REPORT.md`).

#### Travail t1 — Consentement versionné et éligibilité (`evaluate_subject`)

- **Contrat.** `evaluate_subject(subject, protocol, on)` retourne
  `{"eligible": bool, "reasons": list[str], "consent_version": str | None}`.
  Sujet : `birth_date` (date ISO), `consents` (`{version, signed_on}`), `labs`
  (nom → nombre), `flags`. Protocole : `min_age`, `max_age`,
  `required_consent_version`, `consent_valid_from`, `labs` (nom → `[low, high]`
  inclusif), `exclusions`.
- **Règles.** Sélectionner le consentement le plus récent par `signed_on` parmi
  *toutes* les versions au plus tard `on` — aucun repli sur un consentement
  plus ancien. Raisons dans l'ordre déterministe : `CONSENT_MISSING`,
  `CONSENT_VERSION`, `AGE_BELOW_MIN`, `AGE_ABOVE_MAX`, chaque
  `EXCLUSION_<FLAG>` dans l'ordre du protocole, puis `LAB_<NAME>_LOW/HIGH`
  triées par nom de labo. L'âge est calculé exactement au jour `on` ; limites
  d'âge et de labo inclusives. La version du consentement sélectionné est
  retournée même si c'est la mauvaise version. Toute entrée requise manquante
  ou malformée lève `ValueError` nommant le champ — jamais un `KeyError` brut
  ni une exception datetime.
- **Intention.** Logique pure de dates/intervalles à forte densité de pièges :
  anniversaires bissextiles, consentement le jour même, versions retirées,
  inclusion exacte des bornes, règle « le plus récent, sans repli », et un
  ordre de raisons entièrement figé.
- **Mesuré.** Résolu 3/3 par Kimi, Opus et Fable dans les deux sessions ; GLM
  2/3 (ses échecs étaient des échecs de processus, pas de logique).
- **Références.** [ISO 8601](https://en.wikipedia.org/wiki/ISO_8601) (entrées
  date seule) · [Python `datetime`](https://docs.python.org/3/library/datetime.html)
  (sémantique des années bissextiles).

#### Travail t2 — Export de recherche piloté par la gouvernance (`export_subjects`, `execute_reuse_export`)

- **Contrat.** `export_subjects(subjects, *, purpose, secret, on)` retourne
  `{"rows": [...], "manifest": {"algorithm": "sha256", "count": N, "sha256":
  digest}}`. N'inclure un enregistrement que s'il a un consentement pour
  `purpose` avec `signed_on <= on` et (pas de retrait ou `withdrawn_on > on`),
  et `purge_after >= on` — toutes les bornes inclusives.
- **Règles.** Pseudonyme = 16 premiers hex minuscules de
  `HMAC-SHA256(key=UTF-8(secret), msg=UTF-8(purpose + ':' + id))` — à clé et à
  domaine séparé par purpose. Chaque ligne ne porte que `pseudonym`, `country`,
  `birth_year`, `visits` triées chronologiquement ; lignes triées par
  pseudonyme. Liste blanche uniquement : `id`, `name`, `email`, date de
  naissance complète, consentements, `purge_after` et le secret ne sont jamais
  émis — y compris dans les messages d'erreur. Les IDs sont ASCII
  `[A-Za-z0-9_-]+` ; les doublons lèvent `ValueError` avec le code
  `DUPLICATE_SUBJECT_ID` ; JSON rejette NaN/Infinity (`allow_nan=False`) ;
  entrées jamais mutées ; entrée vide valide. Empreinte du manifeste = SHA256
  de `json.dumps(rows, sort_keys=True, separators=(',', ':'),
  ensure_ascii=False)` encodé UTF-8 — sérialisation canonique figée octet par
  octet.
- **Intégration.** `execute_reuse_export(request, subjects, *, actor, secret,
  now)` délègue exactement une fois à `apply_reuse_action(request, 'execute',
  actor, now, executor=...)` ; l'executor appelle `export_subjects` exactement
  une fois ; `export_subjects` reste compatible.
- **Intention.** L'ingénierie de la vie privée en code : sélection de champs en
  liste blanche, séparation de domaine des pseudonymes, hachage canonique,
  hygiène des erreurs, et un contrat de délégation unique strict avec t6. C'est
  aussi le discriminant de lecture d'invite multi-parties : Opus a implémenté
  `export_subjects` mais n'a jamais écrit `execute_reuse_export` — 0/6 sur les
  deux sessions, échec dès l'import.
- **Références.** [RFC 2104 — HMAC](https://www.rfc-editor.org/rfc/rfc2104) ·
  [RGPD Art. 4(5) pseudonymisation](https://gdpr-info.eu/art-4-gdpr/) ·
  [RFC 8785 — canonicalisation JSON](https://www.rfc-editor.org/rfc/rfc8785).

#### Travail t3 — Intégrité de chaîne d'audit et restauration (`AuditChain`)

- **Contrat.** Chaîne de hachage append-only ancrée sur `GENESIS_HASH = '0' *
  64`. `append(*, event_id, actor, action, at, payload)` retourne un
  enregistrement avec `sequence` depuis 1, `previous_hash` et `hash`. `at` doit
  être timezone-aware, normalisé en `YYYY-MM-DDTHH:MM:SSZ` (microsecondes
  rejetées). `hash` = SHA256 sur le JSON canonique de tous les champs sauf
  `hash` (`sort_keys=True, separators=(',', ':'), ensure_ascii=False,
  allow_nan=False`). Chaînes event/actor/action vides et IDs d'événement
  dupliqués rejetés.
- **Règles.** `verify(records)` retourne une liste déterministe de codes
  d'erreur, pas un booléen : séquence cassée, liaison `previous_hash` rompue,
  mauvais hachage d'enregistrement, IDs dupliqués, timestamps non croissants,
  timestamps/hachages malformés, payloads non-dict ; chaîne valide ou vide →
  `[]`. L'extension `verify(records, *, expected_head, expected_length)` ajoute
  `HEAD_MISMATCH` / `LENGTH_MISMATCH` contre des ancres de confiance et ne doit
  pas re-normaliser les représentations de timestamps stockées. Ceci détecte
  les éditions non re-hachées et, avec les ancres de confiance, les éditions
  re-hachées et les troncatures de suffixe ; le rejeu inter-chaînes est un
  non-objectif explicite.
- **Restauration.** `AuditChain.from_records(records, *, expected_head,
  expected_length)` : les deux arguments de confiance sont requis ; matérialiser
  l'entrée une fois et valider avec `verify` ; un snapshot vide n'est valide
  qu'avec longueur zéro et tête genèse. En cas d'erreurs, lever `ValueError`
  avec exactement `INVALID_CHAIN:` plus les codes triés joints par virgules. En
  cas de succès, retourner une chaîne indépendante tenant une copie profonde,
  avec `previous_hash` et l'index des doublons restaurés ; le prochain append
  utilise la séquence `expected_length + 1` et la tête restaurée.
- **Intention.** Journal inviolable en miniature : discipline de sérialisation
  canonique, énumération déterministe des erreurs, format exact du message
  `INVALID_CHAIN:` (le piège de séparateur que GLM a rencontré lors de la
  session officielle), et invariants de restauration — indépendance par copie
  profonde, index restaurés, séquence continuée. Le r3 direct de Kimi a planté
  avec un `TypeError` brut au lieu du `ValueError` contractuel sur un snapshot
  malformé (56/57 cachés).
- **Références.** [Chaîne de hachage](https://en.wikipedia.org/wiki/Hash_chain) ·
  [RFC 6962 — Certificate Transparency](https://www.rfc-editor.org/rfc/rfc6962)
  (journaux de transparence append-only en production) ·
  [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785).

#### Travail t4 — Planification DST-sûre avec réservations persistantes (`plan_visits`, `book_subject_visits`)

- **Contrat.** `plan_visits(*, first_day, offsets_months, hour, minute,
  timezone, closed_days=None, capacity=None)` : les candidats mensuels dérivent
  de `first_day` + N mois calendaires, le jour borné au dernier jour du mois
  cible — toujours depuis `first_day`, jamais depuis une visite déjà bornée ou
  replanifiée (31 janv. → 28 févr. → 31 mars, pas 28 mars). Offsets entiers non
  négatifs uniques ; datetimes locales construites avec `zoneinfo` dans la zone
  IANA donnée ; zones invalides et heures hors limites rejetées.
- **Règles.** Les jours dans `closed_days` ou à capacité nulle avancent au
  prochain jour disponible (recherche bornée à 366 jours, sinon `ValueError`
  `UNSCHEDULABLE` et aucun plan partiel) ; l'heure murale locale d'origine est
  préservée lors des déplacements de jour ; les heures locales inexistantes
  (trou du printemps) avancent de la durée du trou ; les heures ambiguës
  utilisent `fold=0` ; `capacity` associe dates → créneaux positifs restants et
  n'est jamais mutée ; allocation gloutonne au plus tôt dans l'ordre du
  protocole. Éléments : `{"offset_months", "local" (avec secondes), "utc"
  (avec Z)}`.
- **Intégration.** `book_subject_visits(subject, protocol, *, on, first_day,
  offsets_months, hour, minute, timezone, capacity, closed_days=None)` appelle
  d'abord `evaluate_subject(subject, protocol, on)` : si inéligible, lever
  `ValueError` avec `INELIGIBLE:` plus les raisons ordonnées et ne pas toucher
  la capacité. Toute la génération de candidats, fermetures, gestion DST,
  validation et allocation intra-plan délègue à `plan_visits`. Seulement après
  succès complet, décrémenter les entrées de capacité des jours de visite
  **déjà listées** ; les dates non listées gardent un comportement non contraint
  et ne sont jamais insérées ; tout échec laisse la capacité exactement
  inchangée ; les appels réussis antérieurs restent engagés (contention
  séquentielle des créneaux).
- **Intention.** Le travail le plus difficile : arithmétique calendaire (borne
  de fin de mois), réalité de la base tz (trous/folds), et sémantique de
  réservation atomique avec rollback. Il porte aussi le piège du format figé —
  la chaîne exacte `INELIGIBLE:A,B` (sans espaces) a coûté à Kimi ses 6
  exécutions sur les deux sessions malgré 25/26 tests cachés réussis. GLM a en
  outre manqué le contrôle de type vrai-dict (accepté une capacité
  `mappingproxy`) et la décrémentation du créneau.
- **Mesuré.** Fable 6/6 ; Opus 3/3 officiel et 2/3 direct (un rejet fichier
  `.tmp`) ; Kimi et GLM 0/6.
- **Références.** [PEP 615 — `zoneinfo`](https://peps.python.org/pep-0615/) ·
  [IANA Time Zone Database](https://www.iana.org/time-zones) ·
  [`datetime.fold`](https://docs.python.org/3/library/datetime.html#datetime.datetime.fold).

#### Travail t5 — Triage de suivi de sécurité (`triage_events`)

- **Contrat.** `triage_events(events, *, now)` fusionne les chaînes de suivi
  (via `follow_up_of`) en un cas par chaîne, pour des suivis d'événements de
  sécurité fictifs — explicitement pas un jugement médical. Événements :
  `event_id`, `subject_pseudonym`, `event_code`, `onset` (datetime ISO
  timezone-aware), `seriousness` (`fatal`, `life_threatening`,
  `hospitalization`, `other`), `source`, optionnels `follow_up_of` et
  `uncertain`.
- **Règles.** Rejeter les IDs d'événement dupliqués, les racines manquantes,
  les cycles, et les chaînes mélangeant pseudonymes de sujets ou codes
  d'événement. Par cas : `case_id` (ID racine), `onset` (le plus tôt, UTC
  `Z`), `seriousness` (le plus haut selon fatal > life_threatening >
  hospitalization > other), `report_by` (onset le plus tôt + 7 jours pour
  fatal/life_threatening, 15 pour hospitalization, 30 pour other — l'horloge
  court depuis l'onset le plus tôt, jamais depuis l'arrivée du suivi),
  `sources` (triées uniques), `event_ids` (triés), `uncertain` (vrai si un
  événement le porte), `overdue` quand `report_by < now` (strict ; l'égalité
  n'est pas en retard ; `now` doit être aware). Cas triés par onset puis
  `case_id` ; l'ordre d'entrée ne doit pas affecter la sortie. Gravité
  inconnue, timestamps naïfs et liens invalides lèvent `ValueError` avec codes
  stables. Pas de fuzzy matching, pas de diagnostics inventés, pas de vraies
  règles de déclaration.
- **Intention.** Validation de graphe sur une forêt de pointeurs parents
  (cycles, racines manquantes) plus agrégation déterministe et invariante à
  l'ordre ; le cadrage « triage fictif uniquement » teste la discipline des
  frontières de exigences.
- **Mesuré.** Kimi, Opus et Fable 3/3 dans les deux sessions ; GLM 1/3 officiel
  et 2/3 direct, perdant des exécutions sur `max_calls` et rejets de fichiers
  temporaires.
- **Références.** [Guide ICH E2A de pharmacovigilance](https://database.ich.org/sites/default/files/E2A_Guideline.pdf)
  (l'analogue réel que le travail simplifie délibérément) ·
  [Ensembles disjoints / détection de cycles](https://en.wikipedia.org/wiki/Disjoint-set_data_structure).

#### Travail t6 — Flux de gouvernance de réutilisation des données (`apply_reuse_action`)

- **Contrat.** `apply_reuse_action(request, action, actor, now, executor=None)`
  pilote une machine à états sur `draft → submitted → approved → executed`,
  plus `rejected` et `expired`. La requête porte `id`, `requester`, `reviewer`,
  `purpose`, `status`, `retention_until`, `privacy_plan` (exactement
  `{"algorithm": "hmac-sha256", "domain": purpose, "fields": ["pseudonym",
  "country", "birth_year", "visits"]}`), `metadata` (`representative`,
  `complete`, `quality_score`) et `history`.
- **Règles.** `submit` : requester seulement, draft → submitted. `approve` :
  reviewer seulement, submitted → approved, fixe `approval_expires_on = now +
  30 jours`. `reject` : reviewer seulement, submitted → rejected. `execute` :
  requester seulement, approved → executed. `expire` : tout acteur,
  approved/submitted → expired quand `now > approval_expires_on` (approved)
  sinon `now > retention_until`. L'approbation exige requester ≠ reviewer
  (`SELF_REVIEW`), le `privacy_plan` exact, des métadonnées representative et
  complete (`METADATA_REQUIRED`), `quality_score >= 0.95`, `now <=
  retention_until` (`RETENTION_EXPIRED`) et un purpose non vide. L'exécution
  exige en outre actor = requester, `now <= approval_expires_on`
  (`APPROVAL_EXPIRED`), les mêmes contrôles vie privée/métadonnées, et un
  `executor` appelable. Chaque action copie profondément la requête (l'entrée
  de l'appelant jamais mutée) et ajoute `{action, actor, at}` à l'historique
  avec `now` aware normalisé en UTC `Z`. Violations de rôle → `PermissionError` ;
  états invalides ou contrôles échoués → `ValueError` avec codes stables
  (`INVALID_STATE`, …).
- **Intention.** Rigueur de flux de travail : séparation des tâches, ordre des
  gardes, deux horloges d'expiration différentes (approbation vs rétention),
  discipline de copie profonde, et le crochet `executor` que
  `execute_reuse_export` de t2 doit appeler exactement une fois — la couture
  d'intégration inter-travaux.
- **Mesuré.** Le travail le plus uniformément résolu : 3/3 pour chaque modèle à
  la session officielle ; GLM a perdu une exécution directe sur un rejet de
  fichier temporaire.
- **Références.** [Séparation des tâches](https://en.wikipedia.org/wiki/Separation_of_duties) ·
  [Machine à états finis](https://en.wikipedia.org/wiki/Finite-state_machine).

### 4.3 Suites de tests cachées (`tasks/<job>/test_hidden_*.py`)

2 249 lignes au total, **195 cas paramétrés** (19/33/57/26/15/45 par travail).
Les classes de test récurrentes sont des points de pression intentionnels :

- **Exactitude des bornes** : limites inclusives âge/labo, anniversaires
  bissextiles, consentement le jour même, `report_by < now` (l'égalité n'est
  *pas* un dépassement).
- **Pièges temps/fuseau** : clamping de fin de mois (31 janv. → 28 févr. → 31
  mars), trous DST de printemps (avancer du trou), ambiguïté d'automne
  (`fold=0`), normalisation UTC `Z`, microsecondes rejetées.
- **Canonicalisation** : hachage `json.dumps(..., sort_keys=True,
  separators=(',',':'), ensure_ascii=False)` ; `allow_nan=False`.
- **Contrats d'erreur exacts** : codes stables lisibles machine et formats de
  message exacts — ex. `"INELIGIBLE:" + ",".join(reasons)`,
  `INVALID_CHAIN:<codes triés joints par virgules>`. (Ce style de séparateur
  figé est le piège de rigueur délibéré ; voir §9.4.)
- **Immutabilité et atomicité** : entrées jamais mutées, sémantique deep-copy,
  rejet des ID dupliqués, rollback à tout échec (capacité inchangée), commit
  seulement après succès complet.
- **Structures adverses** : cycles de suivis, racines manquantes, chaînes
  inter-sujets, liens d'audit falsifiés, éditions re-hachées ou non, troncature
  de suffixe, mappingproxy passé là où un vrai dict est exigé.

### 4.4 Tests visibles vs tests cachés

- **Visibles** (`taskrepo/tests/`, 39 fonctions) : couverture de base que l'agent
  peut exécuter librement avec l'outil `run_tests`. Nécessaire mais *pas
  suffisante*.
- **Cachés** (`tasks/`) : injectés seulement à la notation, dans un checkout
  vierge. L'agent ne les voit jamais. Une exécution ne passe que si la suite
  cachée et la suite complète sortent en 0, avec ≥ 1 réussi et zéro
  skip/xfail/xpass (une exécution qui ne collecte rien est un échec, pas une
  réussite).

### 4.5 Miroirs de référence (`reference/<job>/`)

Chaque travail a un miroir **indépendant** contenant une implémentation complète
et correcte. Leur rôle est la vérité terrain : chaque suite cachée doit passer
sur son miroir avant toute mesure de modèle (vérifié : 19/33/57/26/15/45 = 195
cas cachés verts ; les 477 du README comptent aussi les suites visibles des
miroirs). Note de layout : t1–t4 portent le fichier caché à la racine du miroir,
t5–t6 exigent une injection dans `tests/` — recettes exactes dans
`docs/USERGUIDE.md` §3 (ou ses versions anglaise et chinoise).


## 5. La boucle d'agent (`harness/agent.py`)

### 5.1 Échafaudage figé

- **Invite système** : volontairement courte (« agent de codage autonome …
  changements minimaux et corrects … appeler `finish` … pas d'humain dans la
  boucle ») — on mesure le modèle, pas l'ingénierie d'invite.
- **Six outils, rien de plus** : `list_files`, `read_file`, `write_file`,
  `edit_file`, `run_tests`, `finish`. `edit_file` exige un remplacement à
  occurrence exactement unique ; `run_tests` est simplement `pytest -q`
  (timeout 90 s, queue de 6 000 caractères). Les résultats d'outils sont
  tronqués (20 000 caractères vers le modèle) pour borner le contexte.
- **Bac à sable workspace** : chaque chemin est résolu à l'intérieur de la copie
  du dépôt ; les échappées sont rejetées.
- **Limites dures** : 30 appels, 20 min de mur, 16 384 jetons de complétion,
  paramètres de raisonnement par défaut du fournisseur. Identiques pour tous.
- **Résilience** : jusqu'à 4 tentatives par appel avec backoff (attente extra
  sur 429) ; 400/404/422 avortent tôt ; identifiants expirés (`401/403` +
  « expired ») arrêtent le couloir avec `credentials_expired` pour permettre une
  reprise après reconnexion.
- **Télémétrie comportementale** (par exécution) : comptages d'appels d'outils,
  `format_failures` d'arguments malformés, `no_tool_turns` inactifs (3 tours
  consécutifs sans appel terminent en `no_tool_calls`), `api_errors`, et la
  raison d'arrêt (`finish`, `max_calls`, `wall_clock`, `no_tool_calls`,
  `api_error`, `credentials_expired`, `budget_hard_stop`).

### 5.2 Deux surfaces API, une même sémantique

| | OpenAIAdapter (Kimi, GLM) | ConverseAdapter (Opus, Fable) |
|---|---|---|
| Point de terminaison | Chat Completions compatible OpenAI via proxy SigV4 local `127.0.0.1:8787` | Bedrock Converse via boto3 en us-west-2 |
| Schéma d'outils | `tools=[{type:function, function:{...}}]` | `toolConfig.tools[].toolSpec` (même JSON schema) |
| Normalisation d'usage | `prompt_tokens`, `completion_tokens`, `completion_tokens_details.reasoning_tokens`, `prompt_tokens_details.cached_tokens` (**le cache est un sous-ensemble** des prompt tokens) | `inputTokens + cacheRead + cacheWrite` (**champs cache additifs**), `outputTokens` ; pas de détail raisonnement |
| Arguments malformés dans l'historique | `_replayable()` : JSON invalide rejoué en `{}` (Bedrock revalide les appels antérieurs à chaque requête) en comptant l'échec | même helper partagé |

Asymétrie connue (conservée et divulguée) : les adaptateurs Claude ne posent
aucun point de cache, donc leurs coûts sont des bornes supérieures ; Kimi/GLM
voient 89–94 % de leurs jetons d'entrée en lecture de cache à ~1/10 du prix.

### 5.3 Notation (`grade()`)

1. Diff du dépôt de l'agent contre le départ vierge (en ignorant `__pycache__`,
   `.pytest_cache`).
2. **Liste blanche** : les changements doivent rester dans `trialops/*.py` ou
   `tests/test_*.py`. Tout le reste (fichiers temporaires, `.tmp`, config
   pytest, modules auxiliaires) → échec automatique `unauthorized file
   changes: …`. Cette seule règle a capturé 7 des 16 échecs de la dernière
   session (§3 de `out-live/REPORT.md`).
3. Appliquer seuls les changements `trialops/` à un checkout vierge dans un
   tempdir ; injecter la suite cachée autoritaire dans `tests/`.
4. Exécuter la suite cachée seule, puis la suite complète
   (`-p no:cacheprovider`, `--override-ini addopts=` pour empêcher toute config
   agent ou environnement de fuiter ; timeout 90 s).
5. **Réussite = les deux sortent en 0**, avec ≥ 1 réussi et zéro
   skip/xfail/xpass.

Les tests et la configuration pytest écrits par l'agent n'entrent jamais dans le
checkout de notation — un modèle ne peut pas gonfler son score en affaiblissant
ou supprimant des tests, en ajoutant des skips/xfails ou en réglant la collecte.


## 6. Comptage (`harness/meter.py`)

- Coût par appel = `entrée fraîche × prix entrée + lecture cache × prix lecture +
  écriture cache × prix écriture + sortie × prix sortie`, où `fraîche = entrée −
  lue − écrite` (la normalisation du §5.2 garde les deux surfaces honnêtes).
- Les tarifs sont les tarifs AWS **de la route réellement utilisée** (Global vs
  US CRIS). Exemple : Opus a été basculé sur `us.anthropic.claude-opus-5-5`
  (4,40 $/22 $/0,22 $) après des 503 Global répétés — la substitution et son
  contrefactuel sont documentés dans le README, pas absorbés en silence.
- Chaque appel ajoute une ligne à `spend.jsonl` ; le total courant est le
  **compteur**. À 90 $, plus aucun nouvel appel (`budget_hard_stop`), protégeant
  le plafond de 100 $. Le compteur est par répertoire de sortie : un nouveau
  `P117_OUT` repart de 0,00 $.

## 7. Exécution de la matrice et observabilité en direct

- **Couloirs** : un par (modèle, répétition) → 12 couloirs pour le 4×3 officiel,
  chacun parcourant les six travaux dans l'ordre ; `ThreadPoolExecutor(12)`.
- **Reprise** : tout `run_id` déjà dans `results.jsonl` est sauté avant tout
  autre contrôle ; Ctrl-C est toujours sûr ; la dépense partielle d'une
  exécution interrompue reste dans le registre par construction.
- **État en direct** (ajout d'observabilité) : le runner écrit `expected.json`
  (manifeste complet) au démarrage et ajoute des événements
  `run_start` / `run_end` / `run_skip` à `events.jsonl`.
- **Tableau de bord** (`harness/dashboard.py`, stdlib only, lecture seule) :
  reconstruit à chaque poll de 2 s attendu/terminé/en cours/en attente, stats
  par modèle, dépense vs plafond et flux d'événements. Les répertoires sans
  `events.jsonl` se rendent quand même — les exécutions en vol sont inférées des
  lignes de dépense sans ligne de résultat. Le manifeste l'emporte toujours sur
  les replis CLI, donc le tableau reflète exactement le plan du runner.

## 8. Conception statistique et interprétation

- **3 répétitions par travail** est un compromis coût/signal délibéré : assez
  pour exposer une falaise de fiabilité (0/3 vs 3/3) et le déterministe vs
  l'aléatoire, pas assez pour classer des modèles à une exécution d'écart. Deux
  sessions indépendantes de 72 exécutions (officielle 57, direct 56 réussies)
  confirment : les totaux par modèle bougent de ≤ 1 exécution, et chaque échec
  déterministe s'est reproduit exactement.
- **Lire les raisons d'arrêt avant les comptages de réussites.** Un échec
  `finish` est un échec de contenu (le modèle se croyait terminé — voir Opus t2,
  Kimi t4) ; un échec `max_calls`/`wall_clock` est un échec de processus
  (jamais convergé — le profil GLM). Ils impliquent des correctifs et des
  histoires de capacité différents.
- **$ / réussite** est la métrique d'efficience de référence ; le $ / exécution
  brut est trompeur quand les taux de réussite diffèrent. Les coûts Claude sont
  des bornes supérieures (pas de points de cache).


## 9. Comment continuer

### 9.1 Portes de validation (dans l'ordre, à chaque changement de conception)

1. **Porte référence** : chaque suite cachée verte sur son miroir (recettes en
   USERGUIDE §3 ; attendu 19/33/57/26/15/45).
2. **Porte fumée** : une exécution bon marché (`P117_OUT=out-smoke-v2 …
   --models kimi-k3.1-pro --tasks t1 --reps 1`) — prouve proxy, identifiants,
   notation, registre, tableau de bord de bout en bout pour ≈ 0,08–0,40 $.
3. **Porte matrice** : `--reps 3` complet ; suivre en direct sur le tableau de
   bord ; puis `summarize.py` (lit toujours `out/`).

### 9.2 Ajouter un travail (checklist)

1. Écrire l'invite dans `tasks/tasks.json` (`id: tN-name`, deux parties :
   fonctionnalité de base + extension d'intégration ; figer les contrats
   d'erreur exacts).
2. Étendre `taskrepo/` : stub de module dans `trialops/` + tests visibles de
   base dans `tests/test_*.py`.
3. Écrire `tasks/tN-name/test_hidden_<name>.py` — couvrir les classes de
   pression du §4.3.
4. Créer `reference/tN-name/` (implémentation complète) ; passer la porte
   §9.1.1 jusqu'à ce que la suite cachée soit toute verte. Si le miroir porte le
   fichier caché à sa racine, l'exécuter directement (copier dans `tests/`
   entre en collision de nom).
5. Fumée d'un modèle sur le nouveau travail (`--tasks tN`) avant de l'ajouter à
   une matrice.

### 9.3 Ajouter un modèle (checklist)

1. `harness/run_matrix.py:MODELS` — label → (`openai` | `converse`, ID modèle
   Bedrock). Les modèles compatibles OpenAI passent par le proxy SigV4 ; les
   modèles de classe Claude par Converse.
2. `harness/meter.py:PRICES` — le tarif **de la route que vous appellerez** (§6).
3. Confirmer l'accès au modèle en us-west-2 (Console → Bedrock) et, pour les
   modèles open-weight, que le proxy signe pour cette famille.
4. Pour le rapport officiel, mettre aussi à jour `summarize.py:ORDER/OPEN` et
   `dashboard.py:DEFAULT_MODELS/OPEN_WEIGHTS`. Fumée d'abord (§9.1.2).

### 9.4 Limites connues et questions ouvertes

- **Asymétrie de cache** : les coûts Claude sont des bornes supérieures (pas de
  points de cache) ; envisager d'activer des checkpoints de cache sur Converse
  pour une comparaison à périmètre égal.
- **Piège du séparateur figé** : les assertions de format exact
  `INELIGIBLE:`/`INVALID_CHAIN:` discriminent sur un détail que l'invite
  sous-spécifie. Deux options loyales : figer le format exact dans le texte de
  l'invite, ou garder tel quel en le déclarant piège de rigueur délibéré — mais
  ne pas mélanger les deux philosophies en silence.
- **Timeout partagé de 90 s** pour `run_tests` de l'agent et pour la notation ;
  le code pathologique (boucles infinies) est noté tel quel.
- **Biais d'auteur unique** : une personne a écrit travaux, tests cachés et
  noteur. Une revue indépendante des suites cachées est le gain de crédibilité
  le moins cher.
- **Répétitions** : 3/travail est le compromis documenté ; passer à 5 double
  environ le coût de la matrice (budgéter en conséquence — l'arrêt dur du
  compteur est par répertoire de sortie).
- Backlog d'idées : davantage de travaux du type intégration (les plus
  discriminants) ; annotations de difficulté par exécution dans
  `results.jsonl` ; push SSE dans le tableau de bord au lieu du polling 2 s ;
  porte CI qui exécute §9.1.1 à chaque changement de travail.

## 10. Schémas de données (pour les auteurs d'outils)

- `results.jsonl` : `{run_id, model, task, rep, stop, calls, tool_calls,
  format_failures[], no_tool_turns, api_errors[], usage{input, output,
  reasoning, cache_read, cache_write}, usd, wall_s, visible_tests_run,
  model_id, adapter, grade{hidden{exit, passed, failed, errors, skipped,
  xfailed, xpassed, tail}, suite{…}}, pass, finished_at}`
- `spend.jsonl` : `{t, run, model, usage{…}, usd, total_usd}` — une ligne par appel.
- `events.jsonl` : `{t, kind: run_start|run_end|run_skip, run_id, model, task,
  rep, …}` (`run_end` ajoute `passed, stop, calls, usd, wall_s`).
- `expected.json` : `{generated_at, reps, models[], tasks[], runs[{run_id,
  model, task, rep}]}` en ordre de couloir.

