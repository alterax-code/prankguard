# PrankGuard — Guide de distribution

## Prérequis système (machine de build)

- Windows 10/11 64-bit
- Python 3.12 dans `venv312/` (déjà configuré)
- Inno Setup 6.x pour la version Full uniquement → https://jrsoftware.org/isdl.php

---

## Version Lite (PyInstaller one-dir)

Produit : `dist\PrankGuard\PrankGuard.exe` + dépendances (~200-350 MB)

```bat
build_lite.bat
```

Le script :
1. Active `venv312`
2. Installe PyInstaller si absent
3. Lance `pyinstaller build_lite.spec --clean`

### Ce qui est bundlé
| Composant | Détail |
|-----------|--------|
| Python 3.12 runtime | Inclus dans le bundle |
| face_recognition + dlib | Inclus + extensions .pyd |
| Modèles dlib (.dat) | ~130 MB — shape_predictor, resnet, cnn_detector |
| OpenCV | cv2.pyd + DLLs |
| customtkinter | Thèmes JSON + images |
| pywin32 + WMI | Inclus |

### Distribution de la version Lite
Zipper le dossier `dist\PrankGuard\` entier. L'utilisateur extrait et lance `PrankGuard.exe`.
Aucune installation de Python requise.

---

## Version Full (Installeur Windows)

Produit : `output\PrankGuard_Setup_1.0.0.exe` (~120-200 MB compressé LZMA2)

```bat
build_full.bat
```

Le script :
1. Vérifie que `dist\PrankGuard\` existe (lance `build_lite.bat` sinon)
2. Localise `ISCC.exe` (Inno Setup)
3. Compile `build_full.iss`

### Ce que fait l'installeur
- Installe dans `C:\Program Files\PrankGuard\`
- Crée un raccourci Bureau et Menu Démarrer
- Requiert les droits administrateur (nécessaire pour le blocage USB/Bluetooth)
- Désinstallation propre via Ajout/Suppression de programmes
- À la désinstallation : propose de supprimer les données biométriques (`data\owner_faces\`)

---

## Données utilisateur

| Données | Emplacement | Supprimé à la désinstallation |
|---------|-------------|-------------------------------|
| Encodings faciaux | `{app}\data\owner_faces\encodings.npy` | Optionnel (demande confirmation) |
| Config JSON | `%USERPROFILE%\.prankguard\config.json` | Non — à supprimer manuellement |

---

## Points d'attention

**UAC :** L'exe embarque un manifeste `requireAdministrator`. Au premier lancement,
Windows demande confirmation UAC. C'est attendu.

**Antivirus :** PyInstaller + logiciel qui modifie le registre USB = certains AV peuvent
flaguer le binaire. Solutions : signer le code (certificat EV) ou demander une exclusion.

**Migration encodings :** Si l'utilisateur avait `encodings.pkl` (version ≤ v18),
la conversion vers `.npy` est automatique au premier lancement.

**PyInstaller 6.x :** Les DLLs/PYDs sont dans `_internal\` (sous-dossier). L'installeur
Inno Setup les copie correctement via `recursesubdirs`.

---

## Rebuild après modification du code

```bat
REM Lite uniquement
build_lite.bat

REM Lite + Full (installeur)
build_full.bat
```

`build_full.bat` rebuild le lite automatiquement si `dist\PrankGuard\` est absent.
