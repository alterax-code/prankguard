# PrankGuard v3

Votre webcam veille sur votre poste Windows : si quelqu'un d'autre que vous s'installe devant
l'écran, la session se verrouille aussitôt. Tout tourne en local, sans compte, sans connexion,
100 % gratuit.

## Fonctionnalités

| Protection | Comportement par défaut |
|---|---|
| **Inconnu devant l'écran** | Verrouillage après 1,5 s si un visage non enregistré regarde l'écran en votre absence |
| **Regard indiscret** | Notification quand quelqu'un lit par-dessus votre épaule (ou verrouillage, au choix) |
| **Périphériques** | Clé USB, clavier, souris, écran, téléphone… branché en votre absence → verrouillage ; devant l'écran → notification |
| **Caméra masquée / débranchée** | Verrouillage après 6 s |
| **Absence** (optionnel) | Verrouillage si vous quittez votre poste |
| **Anti-photo** | Les posters, photos et écrans sont ignorés (anti-spoofing MiniFASNet) |

Plus : photo de l'intrus dans le journal, sirène optionnelle, pause temporisée, plusieurs profils
autorisés, approbation de vos propres périphériques, icône de zone de notification colorée selon l'état,
lancement au démarrage de Windows.

## Installation

**Installeur** : `dist\PrankGuard-Setup-3.0.0.exe` (aucun droit administrateur requis), ou
lancez directement `dist\PrankGuard\PrankGuard.exe`.

**Depuis les sources** (Python 3.11+) :

```powershell
.\setup.bat
.un.bat
```

(Dans PowerShell, le préfixe `.\` est obligatoire ; dans cmd, `run.bat` suffit.)

Options : `--background` (démarrer dans la zone de notification), `--dry-run` (simule les
verrouillages, pour tester), `--headless` (sans fenêtre), `--debug`.

## Premier lancement

1. **Enregistrer mon visage** : regardez votre écran normalement une seconde (calibration de la
   position de la caméra), puis suivez les consignes : face, gauche, droite, haut, bas. La capture est
   automatique et prend une vingtaine de secondes.
2. Tant qu'aucun profil n'est enregistré, PrankGuard ne verrouille **jamais** rien.
3. Fermer la fenêtre la range dans la zone de notification ; la surveillance continue.

## Architecture

```
prankguard/
  vision/camera.py   capture threadée (dernière image uniquement, MSMF ~0,4 s d'ouverture)
  vision/faces.py    YuNet (détection + 5 points) · SFace (empreinte 128-d) · MiniFASNet (anti-photo)
                     · orientation de tête · suivi IoU avec vote d'identité à hystérésis
  guard.py           machine de décision pure (aucune I/O, entièrement testée)
  engine.py          orchestration temps réel, cadence adaptative 8/10/15 analyses/s
  winsys.py          Win32 via ctypes : verrouillage, périphériques (WM_DEVICECHANGE, toutes classes),
                     session (WTS lock/unlock), veille, DPAPI, autostart, instance unique
  gallery.py         profils chiffrés DPAPI (liés à votre compte Windows)
  events.py          journal SQLite (WAL) + photos d'intrusion
  server.py          serveur 127.0.0.1 : API JSON, état temps réel (SSE), vidéo (MJPEG) — jeton + contrôle Host
  app.py / tray.py   fenêtre WebView2 (pywebview) + zone de notification (pystray)
  web/               interface (HTML/CSS/JS natif, aucune dépendance)
```

Choix techniques mesurés sur la machine de développement :

- **YuNet + SFace (OpenCV 4.x)** à la place de dlib/face_recognition : plus besoin de compiler dlib,
  détection ~3-5 ms, empreinte ~13-20 ms, séparation nette (même personne ≈ 0,9 ; autre ≈ 0,2).
  OpenCV 5.0 est volontairement exclu (plus lent et instable sur ces modèles).
- La reconnaissance n'est pas recalculée à chaque image : chaque visage suivi est réidentifié toutes
  les 0,25 s (inconnu) ou 0,7 s (reconnu). Résultat : **~5-7 ms par analyse, ~1-2 % de CPU**.
- **Seuils asymétriques** : reconnu ≥ 42 %, inconnu seulement sous 30 % ; entre les deux, le visage reste
  « en analyse » → on ne verrouille jamais le propriétaire sur un doute.
- **Notifications Windows natives** au lieu du polling WMI de la v2 (qui interrogeait 7 classes toutes
  les 300 ms).
- Caméra **libérée pendant que la session est verrouillée** (voyant éteint, zéro CPU).
- Aucun droit administrateur : le blocage USB par registre de la v2 a été retiré (il nécessitait l'UAC
  et pouvait laisser l'USB désactivé après un plantage) ; le branchement déclenche le verrouillage.

## Données

`%APPDATA%\PrankGuard\v3\` : `config.json`, `faces.bin` (profils chiffrés DPAPI), `events.sqlite3`,
`snapshots\`, `logs\`. Les profils de la v2 (dlib) sont incompatibles : réenregistrez-vous une fois.

## Développement

```bat
.venv\Scripts\python.exe -m pytest -q      &rem 260 tests, ~3 s
build.bat                                  &rem tests + exe PyInstaller + installeur Inno Setup
```

Modèles : YuNet et SFace (OpenCV Zoo, MIT / Apache-2.0), MiniFASNet (Silent-Face-Anti-Spoofing,
conversion ONNX yakhyo/face-anti-spoofing, Apache-2.0). Ils sont vérifiés par SHA-256 et téléchargés
automatiquement s'ils manquent.
