# PrankGuard — feuille de route

État au 23/10/2026 et suite prévue, étape par étape. Chaque étape doit être mesurée en conditions
réelles (webcam, téléphone, photo imprimée, seconde personne) avant de passer à la suivante.

## Fait (v3, poste individuel)

- Reconnaissance EdgeFace-XS (+ SFace), galeries par modèle, apprentissage continu ancré sur l'enrôlement.
- Anti-photo : MiniFASNet + trame d'écran (moiré, indice d'appoint) + relief 3D (parallaxe du nez).
  Test réel : photo sur téléphone ignorée à 100 % (49/49), jamais prise pour le propriétaire.
- Périphériques : fenêtre autoriser une fois / toujours / refuser, clavier bloqué, éjection USB.
- Caméra : pas de verrouillage avant la première image d'un lancement.

## Étape 1 — Regard basé sur les yeux (prochaine)

Problème : tête tournée mais yeux sur l'écran → « ne regarde pas » (la décision vient de la tête).

- Modèle candidat n° 1 : **MobileGaze MobileOne-S0** (ONNX 4,8 Mo, code MIT, entrée visage 448 px,
  sortie lacet/tangage du regard dans le repère caméra — gère « tête tournée, yeux sur l'écran »).
  À vérifier : licence des poids (entraînés sur Gaze360, conditions du jeu de données non lues),
  vitesse réelle (estimée 15-30 ms → à lancer seulement sur les visages proches), signe du lacet.
- Candidat n° 2 (licence la plus propre, Apache-2.0) : **OpenVINO gaze-estimation-adas-0002**
  (deux crops d'yeux 60x60 + pose de tête, < 3 ms) ; nécessite une conversion ONNX.
- Limite physique honnête (webcam 640x480) : regard par les yeux fiable si le visage fait ≥ ~110 px
  (≈ < 0,7 m), dégradé entre 60 et 110 px, impossible en dessous (≈ > 1,2 m : l'iris fait 3 px).
- Décision par paliers :
  - proche : rayon du regard dans le rectangle de l'écran (+ marge 10-15°), lissé sur 5-10 images ;
  - moyen : pose de tête avec un cône large (± 35°) ;
  - lointain : ignoré tant qu'il ne s'approche pas.
- Validation : clips enregistrés tête tournée / yeux sur l'écran, et l'inverse.

## Étape 2 — Premier plan / arrière-plan / passants

- Distance estimée par la taille du visage (déjà disponible), temps de présence par piste,
  vitesse latérale (> ~1 largeur de visage/s = passant).
- Regard indiscret = non-propriétaire proche ou moyen, qui regarde, ≥ 1,5-2 s cumulées sur 4 s.
- Pas de réseau de profondeur (trop lent sur CPU, la taille du visage suffit à ± 20 %).

## Étape 3 — Contexte de la scène (anti-photo renforcé)

- **Bordure du support** : détecteur d'objets COCO léger (NanoDet-Plus ou YOLOX-Nano, Apache-2.0 —
  pas Ultralytics, AGPL) autour du visage : téléphone / écran / ordinateur qui entoure le visage →
  pénalité anti-photo. ~10-30 ms, lancé à ~2 Hz seulement quand un visage est là.
- Modèle d'arrière-plan lent (MOG2) : distinguer une personne qui « apparaît » d'un poster fixe.
- Cohérence lumière visage / pièce : expérimental, gardé seulement si les mesures séparent.
- **Écarté** (mesuré/documenté comme peu fiable ou dangereux) : apprentissage continu de « l'ambiance »
  par réseau de neurones (dérive, empoisonnement, un intrus est filmé dans le même décor),
  reconnaissance des tenues comme preuve d'identité (vêtements changent, ~24 % de réussite).
- Règle : un indice de contexte ne peut que **durcir** une décision, jamais accorder la confiance.

## Étape 4 — Version entreprise (serveur + clients)

### Contraintes légales (France / UE) — à valider avec un juriste / DPO

- Biométrie = donnée sensible (RGPD art. 9) ; **règlement type CNIL 2019-001** obligatoire :
  nécessité à justifier (contexte sensible, badge/mot de passe insuffisant), alternative non
  biométrique à proposer, AIPD (analyse d'impact) obligatoire, information des représentants du personnel.
- Stockage, par ordre de préférence imposé :
  1. gabarit sur un support détenu par l'employé ;
  2. base centrale mais gabarits illisibles sans un secret de l'employé ;
  3. base centrale lisible par l'employeur : dernier recours, à justifier lourdement.
- Conséquence : **le serveur ne doit pas stocker les images de visage**. Enrôlement sur le poste,
  gabarit scellé localement (DPAPI/TPM) ; le serveur gère identités, postes, propriétaires, droits,
  politiques, modèles signés et journal d'audit.
- AI Act : reconnaissance d'émotions interdite au travail ; rester en vérification 1:1 (personne
  attendue sur ce poste) pour sortir du « haut risque ».
- Windows Hello Entreprise fait déjà du déverrouillage facial local : la valeur ajoutée de PrankGuard
  (verrouillage sur intrus, regard indiscret, périphériques) doit être explicitée.

### Licences des modèles

- EdgeFace : poids **non commerciaux** (CC BY-NC-SA) → interdit en entreprise sans licence Idiap.
- Options commerciales : SFace (Apache-2.0, déjà intégré), AuraFace (Apache-2.0, à mesurer),
  InsightFace (licence payante). L'abstraction par modèle existe déjà dans le code.

### Architecture visée

- Serveur sur site : FastAPI + PostgreSQL, Keycloak (comptes admin, lien AD/Entra), step-ca
  (certificat mTLS par poste, clé dans le TPM), politiques JSON signées (Ed25519), modèles signés,
  journal d'audit chaîné.
- Client : service Windows (SYSTEM) qui applique la politique + interface utilisateur limitée.
  L'interface affiche uniquement les capacités signées par le serveur (`/me/capabilities`) ;
  le serveur revérifie chaque appel (masquer dans l'interface n'est pas une sécurité).
- Honnête : un administrateur local peut toujours contourner un logiciel client ; bloquer la vraie
  ouverture de session Windows nécessite un Credential Provider (chantier séparé).

### Ordre de construction

0. Juridique : AIPD, justification, choix de licence du modèle.
1. MVP serveur : utilisateurs, postes, propriétaire par poste, politique signée, audit ; inscription
   mTLS ; gabarits scellés localement ; SFace.
2. Rôles et interface client pilotée par les capacités ; distribution signée des modèles ; AD/OIDC.
3. Durcissement : service SYSTEM, détection d'altération.
4. Credential Provider, haute disponibilité, test d'intrusion.
