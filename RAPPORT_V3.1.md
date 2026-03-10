# PrankGuard v3.1 — Rapport de développement

## Vue d'ensemble

Ce document résume les modifications apportées à PrankGuard v3.1 sur la branche `v2-multi-agent`. Trois tâches ont été réalisées : un bugfix critique, cinq nouvelles fonctionnalités, et la mise en place de la distribution.

**Base de départ** : 66 tests passants, architecture multi-agent avec escalade progressive (VEILLE → SOFT → ALERTE → ACTIF).

**Résultat final** : 70 tests passants (+4 nouveaux), 5 nouveaux modules, pipeline de distribution complet.

---

## Tâche 1 — Bugfix critique : oscillation "owner / no face"

### Le problème

Quand le propriétaire cache partiellement son visage (main devant le visage, tasse, mouvement rapide), l'application oscillait rapidement entre "propriétaire reconnu" et "aucun visage détecté". Chaque frame était évaluée indépendamment sans mémoire temporelle.

**Conséquences concrètes :**
- En mode SECURE, le timer IDLE cumulatif s'accumulait entre les oscillations et finissait par verrouiller le PC alors que l'owner était présent
- En mode PÉDAGO, l'affichage sautait constamment entre les états (inconfort visuel)
- Dans le système d'escalade, les checks faciaux périodiques en SOFT/ALERTE pouvaient déclencher des escalades injustifiées

### Cause racine

1. **Aucun lissage temporel** : le `decision_agent` ne savait pas que l'owner était là 200ms plus tôt
2. **`_reset_timers()` appelé à chaque frame SAFE** : le timer IDLE était remis à zéro quand l'owner était reconnu, mais redémarrait immédiatement quand le visage était perdu à la frame suivante
3. **Pas de grace period dans l'escalade** : le cooldown de 10s après reconnaissance owner (ACTIF → SOFT) ne s'appliquait pas dans les niveaux SOFT/VEILLE

### Solution implémentée

**Concept : "Owner Grace Period" de 2 secondes.**

L'idée est simple : si l'owner a été vu dans les 2 dernières secondes et qu'aucun stranger n'est présent, on considère la situation comme SAFE. Cela absorbe les micro-coupures de détection sans compromettre la sécurité (un stranger déclenche toujours immédiatement le flux THREAT).

#### Fichiers modifiés

**`src/agents/decision_agent.py`** :
- Ajout de la constante `OWNER_GRACE_PERIOD_S = 2.0`
- Ajout de l'attribut `self._owner_last_seen: float = 0.0`
- Mise à jour de `_owner_last_seen` à chaque frame où l'owner est détecté (étapes 1 et 2 du flux de décision)
- Ajout d'une vérification grace period avant les étapes IDLE (ligne ~310) et PASSING (ligne ~300) : si `(now - _owner_last_seen) < 2.0` ET pas de stranger → situation = SAFE, pas d'accumulation du timer IDLE

**`src/prankguard.py`** (orchestrateur) :
- Import de `OWNER_GRACE_PERIOD_S`
- Ajout de `self._owner_last_seen_escalation: float = 0.0`
- `_do_soft_face_check()` : si aucun visage mais owner vu récemment → ne pas escalader vers ALERTE
- `_do_alerte_check()` : si aucun visage mais owner vu récemment → reset du timer de confirmation 3s (ne pas compter les frames sans visage comme "non-owner")

#### Décision de design

La grace period ne protège **PAS** contre les strangers. Si un stranger apparaît, même pendant la grace period, le flux THREAT s'active normalement. Seules les situations "aucun visage" et "visage trop petit" bénéficient de la grace period. C'est un choix délibéré : la sécurité prime sur le confort.

### Tests ajoutés

4 tests dans `tests/test_decision_agent.py` :

| Test | Scénario | Résultat attendu |
|------|----------|------------------|
| `test_owner_grace_period_idle` | Owner → no face (< 2s) | SAFE (grace period) |
| `test_owner_grace_period_expires` | Owner → sleep 2.5s → no face | IDLE (grace period expirée) |
| `test_owner_grace_period_no_effect_on_stranger` | Owner → stranger (< 2s) | THREAT (grace period ignorée) |
| `test_oscillation_scenario` | 10 frames alternant owner/no_face | Jamais IDLE, toujours SAFE |

### Problème rencontré

Le test existant `test_cooldown_expires` a échoué de manière intermittente (flaky test). Il utilisait un cooldown de 50ms avec un sleep de 60ms — marge de 10ms trop serrée pour Windows. Corrigé en augmentant le sleep à 100ms. Ce n'était pas lié aux changements de la grace period, mais un problème préexistant de timing.

---

## Tâche 2 — Fonctionnalités manquantes

### 2.1 Icône systray (obligation RGPD)

**Fichier créé** : `src/gui/systray.py`

**Pourquoi** : la section 6.2 du plan v3 impose que l'utilisateur soit toujours informé que PrankGuard tourne. Une icône dans la zone de notification est la solution standard sous Windows.

**Comment** :
- Utilisation de `pystray` (ajouté à `requirements.txt`)
- Icône ronde générée dynamiquement avec Pillow (lettre "P" blanche sur fond coloré)
- Couleur selon le niveau : vert (VEILLE/SOFT), orange (ALERTE), rouge (ACTIF), gris (PAUSE)
- Tooltip : "PrankGuard — NIVEAU"
- Menu clic droit : Afficher/Masquer la fenêtre, Pause/Reprendre, Quitter
- Thread daemon séparé pour ne pas bloquer la GUI

**Intégration** : le systray est démarré dans `prankguard.py` (orchestrateur) après la GUI, et mis à jour à chaque changement de niveau d'escalade via `_escalate_to()`.

### 2.2 Mode USB Desktop/Laptop

**Fichiers modifiés** : `src/agents/device_monitor.py`, `src/gui/gui.py`

**Pourquoi** : un laptop a son clavier/souris intégrés (pas USB), donc on peut bloquer tous les ports USB. Un desktop a besoin du clavier/souris USB pour fonctionner.

**Comment** :
- Ajout de l'enum `UsbBlockMode` (DESKTOP / LAPTOP) dans `device_monitor.py`
- Méthodes `block_usb()` et `unblock_usb()` qui modifient le type de démarrage des services Windows via le registre :
  - DESKTOP : bloque `USBSTOR` uniquement (clés USB de stockage)
  - LAPTOP : bloque `USBSTOR` + `USBHUB3` (tous les USB externes)
- Toggle Desktop/Laptop ajouté dans l'onglet Paramètres de la GUI (widget `CTkSegmentedButton`)
- Description explicative sous le toggle pour l'utilisateur

**Note** : le blocage USB nécessite des privilèges administrateur. En cas de `PermissionError`, un message d'erreur est loggé.

### 2.3 Audit trail persistant

**Fichier créé** : `src/security/audit_trail.py`

**Pourquoi** : pour la traçabilité et la conformité RGPD, il faut un journal d'audit persistant des événements de sécurité.

**Comment** :
- Logs sauvegardés dans `%APPDATA%/PrankGuard/logs/`
- Un fichier par jour : `audit_YYYY-MM-DD.log`
- Format : `[HH:MM:SS] [LEVEL] [ESCALADE] message | details`
- Rotation automatique au démarrage : suppression des fichiers > 30 jours
- Thread-safe via `threading.Lock`
- API : `start()`, `stop()`, `log()`, `get_today_logs()`, `get_log_files()`

**Intégration** dans `prankguard.py` :
- Démarré au lancement, arrêté à la fermeture
- Logge les changements d'escalade (`_escalate_to()`)
- Logge les verrouillages (`_execute_lock()`)
- Logge les pauses/reprises (`_toggle_pause()`)

### 2.4 Chiffrement AES-256 des encodings

**Fichier modifié** : `src/agents/face_recognition_agent.py`

**Pourquoi** : le module `encryption.py` existait déjà mais n'était pas branché. Les encodings faciaux étaient stockés en clair dans un `.npz`.

**Comment** :
- `save_owner_encodings()` appelle maintenant `save_encrypted_owner_encodings()` du module encryption
- `load_owner_encodings()` essaie d'abord le fichier chiffré `.enc`, puis le `.npz` legacy
- **Migration automatique** : si un `.npz` non chiffré est trouvé, il est automatiquement chiffré en `.enc` puis supprimé
- `clear_owner_encodings()` supprime les deux formats (`.enc` et `.npz`)
- Fallback : si le module `cryptography` n'est pas installé, le format `.npz` est utilisé (avec warning)

**Problème anticipé** : si l'utilisateur change de machine, la clé dérivée (basée sur l'identifiant machine) sera différente et les encodings ne pourront pas être déchiffrés. C'est voulu : les données biométriques sont liées à la machine (sécurité).

### 2.5 Auto-download des modèles

**Fichier créé** : `src/core/model_downloader.py`

**Pourquoi** : le modèle InsightFace buffalo_sc (~300 MB) ne peut pas être inclus dans le dépôt git. Il doit être téléchargé au premier lancement.

**Comment** :
- Classe `ModelDownloader` avec :
  - `is_model_available()` : vérifie la présence des fichiers `.onnx`
  - `download(progress_callback)` : télécharge le zip depuis le CDN InsightFace, avec callback de progression pour la GUI
  - Vérification d'intégrité SHA256 après téléchargement
  - Extraction automatique du zip
- En cas d'échec : message d'erreur clair avec instructions de téléchargement manuel
- Stockage dans `%APPDATA%/PrankGuard/models/`

---

## Tâche 3 — Distribution

### 3.1 Build PyInstaller (Version Lite)

**Fichiers créés** : `build_lite.py`, `build_lite.spec`

**Pourquoi** : permettre la distribution d'un exe standalone sans nécessiter Python sur la machine cible.

**Comment** :
- `build_lite.spec` : configuration PyInstaller one-file, exclut les modèles IA lourds (téléchargés au premier lancement via 2.5)
- `build_lite.py` : script wrapper qui installe PyInstaller si nécessaire et lance le build
- Taille cible : 50-80 MB
- Mode GUI (pas de console)

### 3.2 Installateur Inno Setup (Version Full)

**Fichier créé** : `installer/prankguard.iss`

**Pourquoi** : pour une installation classique "Suivant > Suivant > Installer" avec modèles pré-inclus (~900 MB).

**Comment** :
- Script Inno Setup 6+ avec :
  - Raccourci bureau + menu démarrer
  - Désinstallateur propre (nettoie logs et modèles, garde les encodings par choix RGPD)
  - Compression LZMA2
  - Lancement post-installation
  - Interface en français

### 3.3 Script de release

**Fichier créé** : `scripts/prepare_release.py`

**Pourquoi** : automatiser les étapes répétitives de publication.

**Comment** :
- Bump de version dans `pyproject.toml` (major/minor/patch ou version explicite)
- Génération du changelog depuis les commits git
- Création du tag git
- Instructions claires pour l'upload sur GitHub Releases via `gh release create`

---

## Fichiers modifiés / créés — Récapitulatif

### Fichiers modifiés (6)
| Fichier | Changements |
|---------|-------------|
| `src/agents/decision_agent.py` | Grace period owner (constante, attribut, logique IDLE/PASSING) |
| `src/agents/face_recognition_agent.py` | Intégration chiffrement AES-256, migration auto .npz → .enc |
| `src/agents/device_monitor.py` | Enum UsbBlockMode, méthodes block/unblock USB |
| `src/prankguard.py` | Import systray/audit/grace period, intégration dans start/stop/escalade |
| `src/gui/gui.py` | Toggle Desktop/Laptop dans l'onglet Paramètres |
| `tests/test_decision_agent.py` | 4 tests grace period + fix flaky test cooldown |
| `requirements.txt` | Ajout pystray, customtkinter, insightface, cryptography, etc. |

### Fichiers créés (6)
| Fichier | Rôle |
|---------|------|
| `src/gui/systray.py` | Icône zone de notification Windows |
| `src/security/audit_trail.py` | Journal d'audit persistant sur disque |
| `src/core/model_downloader.py` | Téléchargement auto des modèles IA |
| `build_lite.py` + `build_lite.spec` | Build PyInstaller exe standalone |
| `installer/prankguard.iss` | Script Inno Setup installateur complet |
| `scripts/prepare_release.py` | Automatisation de release GitHub |

---

## Tests

**Avant** : 66 tests passants
**Après** : 70 tests passants (+4 tests grace period)
**Aucune régression** sur les tests existants.

Le seul problème rencontré est le test flaky `test_cooldown_expires` qui utilisait une marge de timing trop serrée (10ms) pour Windows. Corrigé en augmentant le sleep de 60ms à 100ms.

---

## Dépendances ajoutées

| Package | Version | Raison |
|---------|---------|--------|
| `pystray` | >= 0.19.0 | Icône systray Windows (tâche 2.1) |
| `customtkinter` | >= 5.2.0 | GUI (déjà utilisé, formalisé) |
| `insightface` | >= 0.7.0 | Face recognition (déjà utilisé, formalisé) |
| `onnxruntime` | >= 1.16.0 | Backend IA (déjà utilisé, formalisé) |
| `cryptography` | >= 41.0.0 | Chiffrement AES-256 (déjà utilisé, formalisé) |
| `pywin32` | >= 306 | Messages Windows WM_DEVICECHANGE (déjà utilisé, formalisé) |
| `wmi` | >= 1.5.1 | Inventaire périphériques (déjà utilisé, formalisé) |
| `psutil` | >= 5.9.0 | Monitoring CPU pour auto-throttle (déjà utilisé, formalisé) |
