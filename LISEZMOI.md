# PrankGuard — Migration v18 → src/ modulaire

## Contenu du zip

```
migrate.py                    ← Script automatique de migration
_migration_files/
├── .gitignore                ← Corrigé (ignore photos, venv*, .claude/)
├── requirements.txt          ← Corrigé (face_recognition/dlib, plus InsightFace)
├── run.bat                   ← Corrigé (python -m src.main)
├── setup.bat                 ← Corrigé (plus de duplication)
└── src/                      ← 11 nouveaux modules
    ├── __init__.py
    ├── main.py               ← FIX 1 : Auto-elevation admin (UAC)
    ├── config.py             ← FIX 4,5 : Config JSON persistante
    ├── logger.py             ← FIX 8 : Logs détaillés avec icônes
    ├── enrollment.py         ← FIX 10 : 15 photos min + tips dynamiques
    ├── face_analyzer.py      ← FIX 6 : Frame skip (vidéo fluide)
    ├── state_machine.py      ← Machine à états complète
    ├── devices/
    │   ├── watcher.py        ← FIX 2,3 : USB ctypes fix + pause
    │   ├── poller.py         ← FIX 7,8 : WMI polling + infos détaillées
    │   ├── blocker.py        ← FIX 5,7 : Blocage USB/BT/réseau
    │   └── notification.py   ← FIX 7 : Popup Autoriser/Bloquer
    ├── security/
    │   └── locker.py         ← Lock orchestration + mutex
    └── gui/
        └── app.py            ← FIX 3,4,5,6,9 : App principale responsive
```

## Instructions

### 1. Décompresser le zip dans le projet
```powershell
# Copie le contenu du zip dans :
C:\Users\lucas\Desktop\CoursEpitech\prankguard\
```

Tu dois avoir `migrate.py` et `_migration_files/` à la RACINE du projet.

### 2. S'assurer d'être sur main
```powershell
cd C:\Users\lucas\Desktop\CoursEpitech\prankguard
git checkout main
git pull origin main
```

### 3. Lancer le script de migration
```powershell
python migrate.py
```

Le script va :
- Retirer les 150 photos du tracking git
- Supprimer le doublon `legacy/prankguard.py`
- Copier les nouveaux fichiers (.gitignore, requirements, .bat, src/)
- Commit automatiquement

### 4. Pusher et tester
```powershell
git push origin main
.\venv312\Scripts\activate
python -m src.main
```

### 5. Nettoyer (optionnel)
Après vérification que tout marche, supprimer les fichiers temporaires :
```powershell
del migrate.py
rmdir /s /q _migration_files
git add -A
git commit -m "clean: supprimer fichiers de migration"
git push origin main
```
