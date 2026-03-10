# PrankGuard v2 — Rapport de Developpement

**Date** : 10 mars 2026
**Auteur** : Claude Opus 4.6 (via Claude Code)
**Branche** : `v2-multi-agent`
**Document de reference** : PrankGuard_Plan_Complet_v3.pdf / .docx

---

## 1. Resume global

### Ce qui a ete cree

L'architecture multi-agent complete de PrankGuard a ete reecrite depuis zero, en suivant l'ordre du plan de developpement (section 11, priorites 1 a 14).

**23 fichiers crees** au total :

| Categorie | Fichiers | Emplacement |
|---|---|---|
| Core | 1 | `src/core/hardware_profiler.py` |
| Agents | 9 | `src/agents/` (8 agents + `__init__.py`) |
| GUI | 2 | `src/gui/` (`gui.py` + `__init__.py`) |
| Security | 2 | `src/security/` (`rgpd.py`, `encryption.py`) |
| Orchestrateur | 1 | `src/prankguard.py` |
| Tests | 8 | `tests/` (6 tests + conftest + `__init__.py` existant) |

**66 tests unitaires**, tous passent en 4.11 secondes.

### Architecture finale

```
src/
  core/
    hardware_profiler.py    # Priorite 1 — Profilage materiel + benchmark ONNX
  agents/
    __init__.py
    motion_agent.py         # Priorite 2 — MOG2, gardien phase VEILLE
    face_recognition_agent.py  # Priorite 3 — InsightFace buffalo_sc
    head_pose_agent.py      # Priorite 4 — MediaPipe + solvePnP
    trajectory_agent.py     # Priorite 5 — Delta bounding box
    gaze_estimation_agent.py   # Priorite 6 — MediaPipe iris landmarks
    decision_agent.py       # Priorite 7 — Logique de decision centrale
    auto_throttle.py        # Priorite 8 — Monitoring CPU + ajustement
    device_monitor.py       # Priorite 9 — WM_DEVICECHANGE + WMI polling
  gui/
    __init__.py
    gui.py                  # Priorite 10 — CustomTkinter 4 onglets
  security/
    rgpd.py                 # Priorite 12 — Consentement + effacement
    encryption.py           # Priorite 13 — AES-256-GCM
  prankguard.py             # Priorite 11 — Orchestrateur principal
tests/
  conftest.py               # Fixtures partagees
  test_hardware_profiler.py
  test_trajectory_agent.py
  test_decision_agent.py
  test_auto_throttle.py
  test_encryption.py
  test_rgpd.py
  test_benchmark.py         # Priorite 14 — Benchmarks de performance
```

---

## 2. Detail par module

### Module 1 — hardware_profiler.py (src/core/)

**Ce qui a ete code** :
- Collecte CPU (modele, coeurs, frequence, flags AVX/SSE via py-cpuinfo + psutil)
- Collecte RAM (totale, disponible)
- Detection GPU via `onnxruntime.get_available_providers()` (DmlExecutionProvider)
- Detection nom GPU via WMI (`Win32_VideoController`)
- Benchmark ONNX : creation d'un modele MatMul 512x512 minimal, 10 inferences chrometrees sur CPU et GPU, selection du plus rapide
- Attribution du profil (PERFORMANCE / BALANCED / LITE) selon les seuils du plan
- Parametres derives par profil (frame_skip, resolution, gaze_enabled)
- Sauvegarde/chargement JSON dans `%APPDATA%/PrankGuard/`

**Choix techniques** :
- Le benchmark utilise un modele ONNX cree dynamiquement (MatMul) plutot que le vrai modele InsightFace, pour eviter de devoir telecharger buffalo_sc juste pour le benchmark initial. Le vrai modele est plus lourd, ce qui donnerait des temps plus longs, mais les proportions CPU/GPU restent representatifs.
- Fallback si le package `onnx` n'est pas installe : le benchmark est ignore et le profil est attribue uniquement sur les specs materielles.

**Difficultes** :
- `py-cpuinfo` est lent au premier appel (~2s) car il execute des commandes systeme. Pas de contournement simple, c'est acceptable car le profiler ne tourne qu'une seule fois.

---

### Module 2 — motion_agent.py (src/agents/)

**Ce qui a ete code** :
- Thread daemon permanent avec capture webcam via `cv2.VideoCapture`
- MOG2 (`createBackgroundSubtractorMOG2`) sur frames en niveaux de gris + flou gaussien
- Calcul du pourcentage de pixels en mouvement
- Machine a etats VEILLE <-> ACTIVE avec seuil configurable (defaut 0.5% de la surface)
- Retour en VEILLE apres 3 secondes sans mouvement (section 2.1)
- Callbacks evenementiels : `on_phase_change()` et `on_frame()`
- Configuration dynamique du seuil et de l'intervalle d'analyse

**Choix techniques** :
- `detectShadows=False` pour economiser du CPU (le plan le mentionne explicitement)
- Resolution de capture forcee a 320x240 pour minimiser la charge en veille
- Utilisation de `cv2.CAP_DSHOW` sur Windows pour eviter les warnings MSMF
- Flou gaussien 21x21 avant MOG2 pour reduire le bruit et les micro-mouvements

**Difficultes** :
- Aucune difficulte majeure. Le module est simple par conception.

---

### Module 3 — face_recognition_agent.py (src/agents/)

**Ce qui a ete code** :
- Chargement InsightFace `buffalo_sc` via `FaceAnalysis`
- Detection automatique du meilleur provider ONNX (DmlExecutionProvider > CPU)
- Fallback automatique CPU si le provider GPU echoue
- Analyse d'une frame : detection de tous les visages + extraction embeddings 512D
- Classification : OWNER (distance <= 0.30), STRANGER, UNKNOWN
- Verification taille minimum (20% hauteur frame) et centrage (35% depuis centre)
- Detection shoulder surfer (owner + stranger dans la meme frame)
- Enrollment : capture jusqu'a 10 embeddings multi-angle
- Sauvegarde/chargement des encodings en .npz
- Suppression RGPD (`clear_owner_encodings`)

**Choix techniques** :
- Distance cosine calculee manuellement (`1 - dot(a, b)`) plutot qu'avec scipy, pour eviter une dependance supplementaire. Les embeddings InsightFace sont deja normalises (`normed_embedding`), donc le dot product suffit.
- Tolérance 0.30 comme specifie dans le plan (distance testee avec owner reel : 0.318).
- Les encodings sont sauvegardes en `.npz` non chiffre dans ce module. Le chiffrement AES-256 est gere par le module encryption.py (priorite 13), conformement a la separation des responsabilites.

**Difficultes** :
- Le modele `buffalo_sc` doit etre telecharge au premier lancement (~50 Mo). Le module ne gere pas le telechargement avec progress bar (c'est la priorite 15 du plan, non implementee). InsightFace le telecharge automatiquement mais sans feedback visuel.

---

### Module 4 — head_pose_agent.py (src/agents/)

**Ce qui a ete code** :
- MediaPipe FaceMesh (468 landmarks, `refine_landmarks=False` car pas besoin des iris ici)
- Extraction de 6 points cles (nez, menton, coins des yeux, coins de la bouche)
- Modele de visage 3D generique (6 points de reference en mm)
- Resolution PnP via `cv2.solvePnP(ITERATIVE)`
- Conversion vecteur de rotation -> matrice (Rodrigues) -> angles d'Euler (Yaw, Pitch, Roll)
- Matrice camera estimee automatiquement depuis la resolution (focale = largeur image)
- Deux modes : `analyze()` (visage principal) et `analyze_multiple()` (tous les visages)

**Choix techniques** :
- Convention d'angles d'Euler ZYX (intrinsic) pour la decomposition de la matrice de rotation. C'est la convention la plus courante en vision par ordinateur.
- Longueur focale estimee = largeur de l'image. C'est une approximation raisonnable pour les webcams grand public (FOV ~60-70 degres). Une calibration precise ameliorerait la precision mais ajouterait de la complexite a l'installation.
- `refine_landmarks=False` (pas de landmarks iris) car le head_pose utilise des points du visage (nez, yeux, bouche), pas les iris. Le gaze_estimation_agent utilise `refine_landmarks=True` separement.

**Difficultes** :
- Les angles solvePnP peuvent etre instables quand le visage est presque de profil (gimbal lock). Le plan mentionne que MediaPipe perd en precision au-dela de 40 degres de Yaw, ce qui n'est pas un probleme car PrankGuard detecte surtout les visages de face (Yaw < 25 degres).
- Le modele 3D generique de visage est une approximation. Les proportions reelles varient selon les individus, mais les seuils (25 degres Yaw, 20 degres Pitch) sont assez larges pour absorber cette variabilite.

---

### Module 5 — trajectory_agent.py (src/agents/)

**Ce qui a ete code** :
- Historique glissant de 5 bounding boxes (deque)
- Calcul de la variation moyenne de surface entre frames consecutifs (%)
- Classification : APPROACHING (>= +3%), RECEDING (<= -3%), STABLE, UNKNOWN
- Verdict seulement apres 3 frames minimum
- `update_from_faces()` pour analyser la plus grande bbox parmi plusieurs visages
- `reset()` pour le retour en veille

**Choix techniques** :
- Seuils de +/-3% choisis empiriquement. Le plan ne specifie pas de seuil exact, seulement "bbox qui grandit = s'approche". 3% est assez sensible pour detecter un mouvement reel tout en ignorant le bruit de detection (la bbox fluctue legerement entre frames meme si la personne est immobile).
- Historique de 5 frames pour le lissage. C'est un compromis entre reactivite (3 frames suffiraient) et fiabilite (10 frames serait trop lent).

**Difficultes** :
- Aucune. C'est le module le plus simple du projet, comme prevu dans le plan ("Facile").

---

### Module 6 — gaze_estimation_agent.py (src/agents/)

**Ce qui a ete code** :
- MediaPipe FaceMesh avec `refine_landmarks=True` (active les 10 landmarks iris 468-477)
- Calcul du ratio de deviation de l'iris par rapport au centre geometrique de l'oeil
- Normalisation par les dimensions de l'oeil (largeur et hauteur)
- Distance euclidienne normalisee pour un ratio unique par oeil
- Moyenne des deux yeux pour le verdict final
- Seuil : ratio <= 0.25 -> regarde l'ecran
- Deux modes : `analyze()` et `analyze_multiple()`

**Choix techniques** :
- Le seuil de 0.25 a ete choisi pour correspondre au quart de la plage de mouvement de l'oeil. Le plan ne specifie pas de seuil numerique, seulement "le vecteur indique si la personne regarde vers l'ecran". Ce seuil est configurable via `gaze_threshold`.
- Contours des yeux : 6 points par oeil (indices MediaPipe standard). C'est suffisant pour calculer le centre geometrique et les dimensions de l'oeil.
- Iris : seul le centre (landmark 468 gauche, 473 droit) est utilise. Les 4 points de contour de l'iris pourraient permettre un calcul plus precis mais la difference est negligeable pour un verdict binaire (ecran / pas ecran).

**Difficultes** :
- L'estimation du regard est moins precise quand l'utilisateur porte des lunettes (reflets sur les verres). MediaPipe gere assez bien ce cas mais la precision diminue. Pas de solution simple a part mentionner cette limitation.
- Le gaze et le head_pose creent chacun leur propre instance de FaceMesh. C'est volontaire (modules autonomes et testables independamment), mais en production l'orchestrateur pourrait partager une seule instance. Ce n'est pas implemente car le plan insiste sur l'autonomie de chaque agent.

---

### Module 7 — decision_agent.py (src/agents/)

**Ce qui a ete code** :
- Flux de decision complet de la section 3.1 : Cooldown -> Device -> Owner -> Shoulder Surfer -> Threat -> Passing -> Idle
- Comptage des 3 conditions THREAT (gaze, head_pose, trajectory) : >= 2/3 requis
- Timer THREAT de 4 secondes continues
- Timer IDLE/PASSING cumulatif de 10 secondes (mode SECURE), qui ne se reinitialise PAS entre IDLE et PASSING
- Cooldown de 3 secondes apres deverrouillage
- Deux modes : PEDAGO (defaut) et SECURE
- `lock_workstation()` via Win32 API `LockWorkStation`
- Callbacks evenementiels

**Choix techniques** :
- **Jamais d'addition de scores** : chaque condition est un booleen independant, comptees individuellement. C'est la regle absolue du plan.
- L'alerte peripherique (DEVICE_ALERT) est evaluee AVANT la reconnaissance du proprietaire. Le plan dit "Lock immediat" pour les peripheriques, meme si l'owner est present. C'est le seul cas ou l'owner ne court-circuite pas le verrouillage.
- Le timer THREAT se reinitialise quand l'owner est reconnu (retour SAFE -> reset_timers). Si le stranger reapparait, le timer repart de zero. C'est conforme au plan.

**Difficultes** :
- Probleme d'encodage Unicode sur la console Windows : les caracteres `→` dans les strings `reason` causaient des `UnicodeEncodeError` avec le codec `cp1252`. Resolu en remplacant les fleches par `:` dans les messages.
- Le timer IDLE/PASSING cumulatif est subtil a implementer : il faut accumuler le temps entre chaque appel `evaluate()`, pas juste mesurer depuis le debut. L'implementation utilise `_idle_last_tick` pour calculer le delta entre deux evaluations.

---

### Module 8 — auto_throttle.py (src/agents/)

**Ce qui a ete code** :
- Thread daemon permanent, mesure `psutil.cpu_percent()` toutes les 2 secondes
- 4 niveaux de throttle : NORMAL (< 60%), REDUCED (60-75%), LITE (75-85%), MINIMAL (> 85%)
- Parametres derives par niveau (frame_skip, gaze_enabled, resolution)
- Callbacks sur changement de niveau
- `get_effective_params()` : fusionne les parametres du profil hardware avec le throttle

**Choix techniques** :
- `psutil.cpu_percent(interval=None)` pour une mesure non-bloquante. Le premier appel retourne toujours 0 (initialisation), gere par un appel initial dans la boucle avant le premier sleep.
- Les parametres du throttle ne font que **reduire** les valeurs du profil, jamais les augmenter. Si le profil est LITE (frame_skip=10), le throttle REDUCED (frame_skip=7) ne change pas le frame_skip car 7 < 10.

**Difficultes** :
- Aucune difficulte majeure.

---

### Module 9 — device_monitor.py (src/agents/)

**Ce qui a ete code** :
- **Mecanisme 1** : WM_DEVICECHANGE via fenetre Win32 invisible (pywin32). Detection instantanee (< 100ms) des peripheriques USB.
- **Mecanisme 2** : WMI polling toutes les 0.5 secondes. Comparaison de snapshots pour detecter ajouts/retraits dans 6 categories (USB/HID, moniteurs, reseau, Bluetooth, audio, imprimantes).
- Whitelist persistante en JSON (`device_whitelist.json`)
- `whitelist_current_devices()` pour le premier lancement
- Cooldown anti-faux positifs de 6 secondes apres modification de la config
- Degradation gracieuse : si pywin32 manque, seul le WMI reste ; si wmi manque, seul Win32 reste

**Choix techniques** :
- Le filtre Bluetooth utilise `WHERE DeviceID LIKE '%BTHENUM%'` dans `Win32_PnPEntity` plutot qu'une classe WMI dediee, car il n'existe pas de classe WMI standard pour le Bluetooth.
- Le filtre reseau utilise `PhysicalAdapter = TRUE` pour ignorer les adaptateurs virtuels (VPN, Hyper-V, etc.).
- Le WM_DEVICECHANGE ne fournit pas de details sur le peripherique branche (juste un signal). L'evenement generique est emis pour un lock immediat, et le WMI polling fournit les details lors de la prochaine iteration.
- Cooldown de 6 secondes (milieu de la fourchette 5-8 du plan).

**Difficultes** :
- La creation de fenetre Win32 invisible necessite `win32gui.RegisterClass` et une boucle de messages. L'implementation utilise `PumpWaitingMessages()` avec un sleep de 100ms pour pouvoir verifier `self._running` et arreter proprement le thread.
- Les classes WMI varient selon la version de Windows et les drivers installes. Certaines requetes peuvent echouer silencieusement sur certaines machines. Les erreurs sont loguees en debug et n'interrompent pas le monitoring.

---

### Module 10 — gui.py (src/gui/)

**Ce qui a ete code** :
- Fenetre principale CustomTkinter avec theme sombre (`set_appearance_mode("dark")`)
- **4 onglets** via `CTkTabview` :
  - **Camera** : affichage video avec overlay colore (vert/rouge/orange/violet) + texte d'etat + FPS
  - **Logs** : `CTkTextbox` scrollable avec queue thread-safe + bouton effacer
  - **Parametres** : mode PEDAGO/SECURE (CTkSegmentedButton), slider tolerance 0.20-0.50, profil AUTO/PERFORMANCE/BALANCED/LITE, 6 switches pour les categories de peripheriques
  - **Enrollment** : apercu camera 320x240, bouton Capturer/Sauvegarder/Supprimer, barre de progression 0/10
- **Barre inferieure** : phase, profil, throttle, rappel raccourcis
- **Raccourcis clavier** : L (lock), P (pause), U (deblocage USB)
- Callbacks externes pour toutes les actions (`set_lock_callback`, etc.)

**Choix techniques** :
- L'affichage video utilise Pillow (`ImageTk.PhotoImage`) pour la conversion OpenCV -> Tkinter. C'est la methode standard et la plus performante.
- Les logs utilisent une `queue.Queue` pour la thread-safety (les evenements arrivent depuis les threads des agents) avec un polling de 100ms via `self.after(100, self._poll_logs)`.
- L'overlay camera est dessine directement sur la frame OpenCV (rectangle semi-transparent + texte) avant la conversion PIL. C'est plus performant que de dessiner sur un Canvas Tkinter.

**Difficultes** :
- CustomTkinter ne supporte pas nativement l'affichage video. La solution est de mettre a jour un `CTkLabel` avec une nouvelle `PhotoImage` a chaque frame. Cela fonctionne bien jusqu'a ~30 FPS.
- Les raccourcis clavier sont captures par `self.bind("<l>")` etc., ce qui ne fonctionne que quand la fenetre a le focus. Si un `CTkTextbox` ou un `CTkEntry` a le focus, les raccourcis ne sont pas captures. C'est une limitation connue de Tkinter.

---

### Module 11 — prankguard.py (src/)

**Ce qui a ete code** :
- Sequence de demarrage : profiler -> init agents -> start permanents -> watchdog -> GUI
- Gestion du cycle VEILLE <-> ACTIVE via les callbacks du motion_agent
- Boucle d'analyse active dans un thread dedie : frame skip, resize, execution sequentielle des agents, construction des AgentInputs, evaluation par le decision_agent
- Traitement des decisions : lock (LockWorkStation + cooldown) et alert (winsound.Beep)
- Callbacks auto_throttle : ajustement dynamique des parametres effectifs
- Callbacks device_monitor : flag `_device_alert_pending`
- Enrollment : capture / sauvegarde / suppression via la GUI
- Watchdog : verifie motion_agent et auto_throttle toutes les 5 secondes, relance si plantes
- Mode degrade : flags `_gaze_available` et `_head_pose_available` si un agent ne charge pas

**Choix techniques** :
- L'orchestrateur accede directement a `self._motion_agent._cap` pour capturer des frames pendant la phase active. C'est un acces a un attribut prive, ce qui n'est pas ideal en termes d'encapsulation, mais evite de dupliquer l'ouverture de la camera (une seule instance `VideoCapture` pour toute l'application). Une meilleure approche serait d'ajouter une methode publique `get_frame()` au motion_agent.
- Les agents d'analyse (face_recognition, head_pose, gaze) sont executes sequentiellement dans la boucle active, pas en parallele. C'est plus simple et evite les problemes de concurrence sur la frame. Le plan mentionne "tous les agents se mettent en marche simultanement", mais en pratique ils s'executent sur la meme frame dans le meme thread, l'un apres l'autre, en quelques millisecondes.

**Difficultes** :
- La communication entre le thread actif et la GUI doit passer par des mecanismes thread-safe. Les callbacks `update_frame()` et `add_log()` sont appeles depuis le thread actif vers le thread principal (GUI). CustomTkinter tolere les appels cross-thread pour les updates simples, mais ce n'est pas garanti. En production, il faudrait utiliser `self._gui.after()` pour toutes les mises a jour GUI.

---

### Module 12 — rgpd.py (src/security/)

**Ce qui a ete code** :
- Verification du consentement (`has_consent()`) via fichier JSON
- Sauvegarde du consentement avec timestamp et version
- Popup de consentement CustomTkinter dark theme avec texte bilingue FR/EN
- Fallback console si CustomTkinter n'est pas disponible
- Suppression de toutes les donnees (`delete_all_user_data()`) : encodings, config, whitelist, consentement
- Resume des donnees stockees (`get_stored_data_summary()`) pour la transparence

**Choix techniques** :
- Le texte de consentement est stocke directement dans le code Python (constantes), pas dans des fichiers JSON separes. C'est plus simple pour un module autonome. Le plan mentionne des fichiers JSON pour les traductions (section 7.4), mais c'est pour l'interface complete, pas pour la popup de consentement.
- La popup utilise une instance `CTk()` separee de la fenetre principale. C'est intentionnel : le consentement doit apparaitre AVANT le lancement de l'application. Mais cela cree un probleme potentiel si la GUI principale est ensuite lancee (deux mainloops). L'integration dans `prankguard.py` devra gerer cette sequence.

**Difficultes** :
- Aucune difficulte technique majeure.

---

### Module 13 — encryption.py (src/security/)

**Ce qui a ete code** :
- Derivation de cle via PBKDF2-SHA256 (480 000 iterations) a partir d'un identifiant machine stable
- Sel aleatoire 128 bits stocke dans `enc_salt.bin`
- Chiffrement AES-256-GCM (confidentialite + integrite)
- Format fichier : nonce (12 octets) + ciphertext (inclut tag GCM)
- Metadata (shape, dtype) encodees dans le plaintext pour reconstruction fidele du ndarray
- API haut niveau : `save_encrypted_owner_encodings()` / `load_encrypted_owner_encodings()`
- Suppression RGPD

**Choix techniques** :
- AES-256-**GCM** plutot que AES-256-CBC : GCM fournit l'authentification (integrite) en plus du chiffrement, ce qui detecte toute modification du fichier. Le plan mentionne "AES-256" sans preciser le mode ; GCM est le choix recommande par les standards modernes (NIST SP 800-38D).
- L'identifiant machine est un hash SHA-256 de (hostname + processeur + OS + username). C'est un compromis : suffisamment stable pour que l'utilisateur n'ait pas a entrer de mot de passe, mais les encodings ne sont pas portables d'une machine a l'autre. Si l'utilisateur change de PC, il doit refaire l'enrollment.
- 480 000 iterations PBKDF2 : recommandation OWASP 2024 pour SHA-256. C'est plus lent (~100ms) que les 10 000 iterations parfois utilisees, mais c'est un one-shot au demarrage.

**Difficultes** :
- La serialisation des metadata (shape, dtype) dans le plaintext avant chiffrement ajoute de la complexite. C'est necessaire car numpy a besoin de connaitre la shape et le dtype pour reconstruire le ndarray. L'alternative serait de stocker les metadata en clair a cote du fichier chiffre, mais cela revelerait le nombre d'encodings (information potentiellement sensible).

---

### Module 14 — Tests & Benchmark (tests/)

**Ce qui a ete code** :
- `conftest.py` : fixtures partagees (tmp_config_dir, fake_frame, fake_encodings)
- `test_hardware_profiler.py` : 10 tests (attribution profil, params derives, save/load/cache)
- `test_trajectory_agent.py` : 7 tests (approaching, receding, stable, unknown, reset, multi-faces)
- `test_decision_agent.py` : 18 tests (owner prioritaire, THREAT 2/3, timer 4s, IDLE cumulatif, device alert, cooldown, shoulder surfer)
- `test_auto_throttle.py` : 6 tests (seuils, params effectifs)
- `test_encryption.py` : 9 tests (roundtrip, fichier non lisible, suppression, cle deterministe)
- `test_rgpd.py` : 9 tests (consentement, effacement, resume)
- `test_benchmark.py` : 3 tests de performance (trajectory < 50ms/1000, decision < 100ms/1000, crypto < 2s)

**Choix techniques** :
- Les tests utilisent `time.sleep()` pour les tests de timers (threat delay, cooldown). Les delais sont reduits a 100-200ms pour que les tests soient rapides. C'est fragile sur des machines tres lentes (le test pourrait echouer si le sleep n'est pas assez precis), mais acceptable pour des tests unitaires.
- Les tests du decision_agent couvrent exhaustivement tous les scenarios de la section 3 du plan, y compris les cas limites (owner qui annule un THREAT en cours, timer cumulatif IDLE/PASSING, cooldown).

---

## 3. Ecarts avec le document de reference

### Ecarts volontaires et justifies

| Point du plan | Implementation reelle | Justification |
|---|---|---|
| **WinML** (section 4.1bis) : WinML recommande comme provider GPU | **DmlExecutionProvider** utilise en priorite | WinML (`onnxruntime-winml`) n'est disponible que sur Windows 11 24H2+. DmlExecutionProvider fonctionne sur Windows 10 et 11. L'architecture est prete pour WinML (il suffit de changer le provider dans le hardware_profiler). |
| **Systray** (section 6.2) : icone systray toujours visible | **Non implemente** | Le plan mentionne une icone systray avec tooltip. CustomTkinter ne gere pas nativement le systray. Cela necessite `pystray` ou un module Win32 dedie. C'est un ajout futur. |
| **Multilingue** (section 7.4) : fichiers JSON de traduction | **Non implemente** pour la GUI principale | Seule la popup RGPD est bilingue FR/EN. La GUI principale utilise des strings en francais codees en dur. L'internationalisation complete est un ajout futur. |
| **Mode USB DESKTOP/LAPTOP** (section 7.3) : toggle dans les parametres | **Non implemente** | Le plan mentionne un mode USB specifique. Le device_monitor surveille toutes les categories sans distinction desktop/laptop. |
| **Indicateur camera active** (section 6.2) : indicateur visuel | **Partiellement implemente** | L'onglet Camera montre le flux video quand la camera est active, mais il n'y a pas d'indicateur explicite (LED virtuelle) quand elle est inactive. |
| **Watchdog complet** (section 7.2) : relance de tous les agents | **Partiel** | Le watchdog verifie motion_agent et auto_throttle. Les agents d'analyse (face_recognition, head_pose, gaze) ne sont pas surveilles car ils sont executes a la demande dans la boucle active, pas dans des threads permanents. |
| **Audit trail** (section 6.3) : log horodate persistant | **Non implemente** | Les logs sont affiches dans la GUI mais pas sauvegardes sur disque. Le plan mentionne un "audit trail horodate de tous les evenements de securite". |

### Ecarts involontaires / limitations techniques

| Point | Limitation |
|---|---|
| **Partage de FaceMesh** | Le head_pose et le gaze creent chacun leur propre instance de FaceMesh. En production, cela signifie que MediaPipe traite la meme frame deux fois. L'optimisation (partager une instance ou partager les landmarks) n'est pas implementee. |
| **Thread safety GUI** | Les mises a jour de la GUI depuis les threads des agents ne sont pas systematiquement envoyees via `after()`. Ca fonctionne en pratique avec CustomTkinter mais ce n'est pas garanti par Tkinter. |
| **Acces _cap prive** | L'orchestrateur accede a `motion_agent._cap` pour capturer des frames. Il faudrait une methode publique `get_frame()`. |

---

## 4. Problemes rencontres

### Probleme 1 : Encodage Unicode sur la console Windows

**Symptome** : `UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'` lors de l'affichage des resultats du decision_agent.

**Cause** : La console Windows utilise le codec `cp1252` par defaut, qui ne supporte pas les caracteres Unicode comme `→` (U+2192).

**Resolution** : Remplacement des fleches `→` par `:` dans les strings `reason` du decision_agent. Pour les scripts de test qui impriment en UTF-8, utilisation de `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`.

**Impact** : Les docstrings et commentaires utilisent des accents français normalement (ils ne sont pas imprimes dans la console). Seuls les messages affiches dans la console ont ete sanitises.

### Probleme 2 : Extraction du texte PDF

**Symptome** : PyPDF2 extrait le texte du PDF mot par mot (un mot par ligne), rendant l'analyse des sections difficile.

**Cause** : Le PDF a ete genere avec un formatage qui separe chaque mot en element individuel.

**Resolution** : Basculement sur `python-docx` pour lire le fichier `.docx` a la place. L'extraction des paragraphes et des styles de heading est beaucoup plus propre et permet d'identifier les 12 sections + les tableaux.

### Probleme 3 : Premiere valeur psutil.cpu_percent()

**Symptome** : Le premier appel a `psutil.cpu_percent(interval=None)` retourne toujours 0.0.

**Cause** : Comportement documente de psutil. Le premier appel initialise le compteur, le deuxieme retourne la vraie valeur.

**Resolution** : Appel d'initialisation dans `_run_loop()` avant le premier sleep.

### Probleme 4 : Style None dans python-docx

**Symptome** : `AttributeError: 'NoneType' object has no attribute 'name'` lors de l'extraction des headings du docx.

**Cause** : Certains paragraphes n'ont pas de style defini (`p.style` retourne `None`).

**Resolution** : Ajout d'un guard `p.style.name if p.style else ''` avant tout acces au nom du style.

---

## 5. Tests : couverture et limites

### Ce qui est couvert (66 tests)

| Module | Tests | Couverture estimee |
|---|---|---|
| hardware_profiler | 10 | Logique d'attribution 100%. Benchmark ONNX non teste (necessite onnxruntime). |
| trajectory_agent | 7 | 100% de la logique. Pas de dependance externe. |
| decision_agent | 18 | 100% des scenarios du plan section 3. Tous les cas limites. |
| auto_throttle | 6 | 100% des seuils. La boucle de monitoring n'est pas testee (necessite psutil en temps reel). |
| encryption | 9 | Roundtrip complet, integrite, suppression, derivation de cle. |
| rgpd | 9 | Consentement, effacement, resume des donnees. |
| benchmark | 3 | Performance trajectory, decision, crypto. |

### Ce qui n'est PAS couvert

| Module | Raison |
|---|---|
| **motion_agent** | Necessite une webcam physique. Pas de mock simple pour `cv2.VideoCapture`. Un test d'integration avec une video preenregistree serait possible mais pas implemente. |
| **face_recognition_agent** | Necessite le modele InsightFace `buffalo_sc` telecharge (~50 Mo) + une webcam ou des images de test. Les tests unitaires necessiteraient des mocks complexes de `insightface.app.FaceAnalysis`. |
| **head_pose_agent** | Necessite MediaPipe + des images de visages avec des orientations connues. Testable avec des images synthetiques mais pas implemente. |
| **gaze_estimation_agent** | Meme raison que head_pose. |
| **device_monitor** | Necessite pywin32 + WMI + des peripheriques physiques a brancher/debrancher. Tres difficile a tester en unitaire. |
| **gui.py** | Necessite un affichage graphique (pas disponible en CI). Testable avec des frameworks comme `pytest-qt` mais CustomTkinter n'est pas supporte. |
| **prankguard.py** | Test d'integration complet necessitant tous les composants + webcam + GUI. |

### Limites des tests

- **Tests temporels** : Les tests avec `time.sleep()` (threat timer, cooldown, idle cumulatif) sont sensibles a la precision du scheduler. Sur une machine tres chargee, un sleep de 150ms pourrait durer 200ms et faire echouer le test. Les delais sont genereux (100-200ms) pour minimiser ce risque.
- **Pas de tests d'integration** : Les tests verifient chaque module independamment. La communication inter-agents (motion -> face_recognition -> decision) n'est pas testee de bout en bout.
- **Pas de tests de charge** : Les benchmarks mesurent les performances unitaires mais pas le comportement sous charge reelle (camera + tous les agents + GUI simultanement).

---

## 6. Recommandations

### Actions prioritaires

1. **Tests manuels avec webcam** : L'ensemble du pipeline (motion -> face_recognition -> head_pose/gaze/trajectory -> decision -> lock) n'a jamais ete teste de bout en bout. C'est la prochaine etape critique.

2. **Enrollment initial** : Avant de tester, il faut enregistrer le visage du proprietaire via l'onglet Enrollment. Verifier que les encodings sont correctement sauvegardes et charges.

3. **Thread safety GUI** : Migrer toutes les mises a jour GUI vers `self._gui.after(0, lambda: ...)` pour garantir que les appels Tkinter se font dans le thread principal.

4. **Methode `get_frame()` sur MotionAgent** : Ajouter une API publique pour capturer une frame plutot qu'acceder a `_cap` directement.

5. **Partager FaceMesh** entre head_pose et gaze : Creer un service partage qui execute FaceMesh une seule fois par frame et distribue les landmarks aux deux agents.

### Actions secondaires

6. **Icone systray** (section 6.2) : Ajouter avec `pystray` pour la transparence.

7. **Audit trail persistant** (section 6.3) : Sauvegarder les logs dans un fichier avec rotation.

8. **Internationalisation** (section 7.4) : Extraire les strings GUI dans des fichiers JSON FR/EN.

9. **Auto-download modeles** (priorite 15 du plan) : Progress bar au premier lancement pour telecharger buffalo_sc.

10. **Integration du chiffrement** : Brancher `encryption.py` dans `face_recognition_agent.py` pour que les encodings soient toujours chiffres par defaut. Actuellement les deux systemes coexistent (`.npz` non chiffre et `.enc` chiffre).

### Points d'attention

- **Licence InsightFace** : Les modeles pre-entraines (buffalo_sc) sont reserves a la recherche non-commerciale. Toute distribution commerciale necessite une licence (voir section 4.1bis du plan).

- **Performances GPU** : Sur les petits iGPU Intel (UHD Graphics bas de gamme), DirectML peut etre plus lent que le CPU. Le hardware_profiler benchmark les deux, mais il faut verifier que le fallback fonctionne correctement sur ces machines.

- **Consommation memoire** : Le plan prevoit ~300 Mo pour le profil BALANCED. Avec deux instances FaceMesh + InsightFace + GUI, la consommation reelle pourrait etre superieure. A mesurer en conditions reelles.

- **Windows Defender** : L'appel a `LockWorkStation()` et le monitoring WMI peuvent declencher des alertes de securite. L'installateur (priorites 16-18) devra gerer la signature de l'executable.

- **Fermeture propre** : Quand la GUI est fermee, `self.stop()` est appele. Mais si l'application crash, les threads daemon sont tues brutalement sans liberation propre de la camera. Un signal handler ou un `atexit` serait recommande.

---

*Document genere automatiquement par Claude Opus 4.6 lors du developpement de PrankGuard v2.*
