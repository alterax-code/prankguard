# Review PrankGuard v3.1 — Bugfixes post-revue

**Date** : 2026-03-10
**Branche** : `v2-multi-agent`
**Commit parent** : `7b49f58`
**Tests** : 70/70 passent apres chaque fix, 0 regression

---

## BUG 1 — CRITIQUE : Grace period reset le timer d'alerte

**Fichier** : `src/prankguard.py` (lignes 545-551)

**Probleme** :
Dans `_do_alerte_check()`, quand aucun visage n'est detecte mais que l'owner a ete vu recemment (grace period), le code faisait `self._alerte_start = now`. Cela remettait le timer de confirmation de 3 secondes a zero a chaque iteration (toutes les 300ms). Le timer ne pouvait jamais expirer tant que la grace period etait active, retardant l'escalade vers ACTIF de 2 secondes supplementaires (5s au lieu de 3s).

**Avant** :
```python
elif not recog.faces:
    if (now - self._owner_last_seen_escalation) < OWNER_GRACE_PERIOD_S:
        # Owner vu recemment, ne pas compter cette frame
        # comme confirmation non-owner → reset le timer alerte
        self._alerte_start = now  # ← BUG
        logger.debug("ALERTE : aucun visage mais owner vu recemment (grace period)")
        return
```

**Apres** :
```python
elif not recog.faces:
    if (now - self._owner_last_seen_escalation) < OWNER_GRACE_PERIOD_S:
        # Owner vu recemment, ignorer cette frame sans toucher _alerte_start
        logger.debug("ALERTE : aucun visage mais owner vu recemment (grace period)")
        return
```

**Pourquoi** : La grace period doit juste ignorer la frame (return sans rien modifier), pas remettre le timer a zero. Le timer `_alerte_start` ne doit etre modifie que lors du passage en ALERTE, pas a chaque frame ignoree.

---

## BUG 2 — CRITIQUE : Inno Setup recursion infinie dans DirExists

**Fichier** : `installer/prankguard.iss` (lignes 48 + 68-72)

**Probleme** :
Le bloc `[Code]` redefinissait la fonction native `DirExists` d'Inno Setup en s'appelant elle-meme recursivement. Resultat : stack overflow a la compilation ou a l'execution de l'installateur.

**Avant** :
```pascal
; Dans [Files]
Source: "..\data\*"; DestDir: "{app}\data"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: DirExists(ExpandConstant('{src}\..\data'))

; ...

[Code]
function DirExists(DirName: string): Boolean;
begin
  Result := DirExists(DirName);  // ← RECURSION INFINIE
end;
```

**Apres** :
```pascal
; Dans [Files] — utilise le flag natif skipifsourcedoesntexist
Source: "..\data\*"; DestDir: "{app}\data"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Bloc [Code] supprime entierement
```

**Pourquoi** : `DirExists` est une fonction native d'Inno Setup, pas besoin de la redefinir. Le flag `skipifsourcedoesntexist` remplace le `Check:` de facon plus propre et sans risque de recursion.

---

## BUG 3 — MOYEN : Systray callbacks executes depuis le mauvais thread

**Fichiers** : `src/gui/systray.py` + `src/prankguard.py` (ligne 237)

**Probleme** :
`pystray` tourne dans un thread daemon separe. Les callbacks `_on_show`, `_on_pause`, `_on_quit` etaient appeles depuis ce thread. Si ces callbacks touchent des methodes Tkinter (`deiconify`, `withdraw`, `configure`...), Tkinter crash ou freeze car il n'est pas thread-safe.

**Modifications dans `src/gui/systray.py`** :

1. Ajout d'un attribut `_schedule_fn` dans `__init__` :
```python
self._schedule_fn: Optional[Callable[[Callable], None]] = None
```

2. Ajout de la methode `set_main_thread_scheduler` :
```python
def set_main_thread_scheduler(self, fn: Callable[[Callable], None]) -> None:
    """Definit la fonction pour poster des callbacks sur le thread principal.
    Typiquement : lambda cb: gui.after(0, cb)
    """
    self._schedule_fn = fn
```

3. Modification des 3 callbacks pour utiliser le scheduler si disponible :
```python
def _on_show(self, icon=None, item=None) -> None:
    if self._show_callback:
        if self._schedule_fn:
            self._schedule_fn(self._show_callback)
        else:
            self._show_callback()

def _on_pause(self, icon=None, item=None) -> None:
    self._is_paused = not self._is_paused
    self._refresh_icon()
    if self._pause_callback:
        if self._schedule_fn:
            self._schedule_fn(self._pause_callback)
        else:
            self._pause_callback()

def _on_quit(self, icon=None, item=None) -> None:
    if self._quit_callback:
        if self._schedule_fn:
            self._schedule_fn(self._quit_callback)
        else:
            self._quit_callback()
    self.stop()
```

**Modification dans `src/prankguard.py`** (ligne 237) :
```python
self._systray.set_main_thread_scheduler(lambda cb: self._gui.after(0, cb))
```

**Pourquoi** : `gui.after(0, cb)` poste le callback sur la queue d'evenements Tkinter, qui l'executera dans le thread principal lors du prochain cycle du mainloop. Le fallback (`else`) garantit la retrocompatibilite si le scheduler n'est pas configure.

---

## Resume des fichiers modifies

| Fichier | Lignes modifiees | Severite |
|---|---|---|
| `src/prankguard.py` | 545-551 (suppression `_alerte_start = now`) | CRITIQUE |
| `src/prankguard.py` | 237 (ajout `set_main_thread_scheduler`) | MOYEN |
| `installer/prankguard.iss` | 48 (flag `skipifsourcedoesntexist`) + 68-72 (suppression `[Code]`) | CRITIQUE |
| `src/gui/systray.py` | 95-106 (`__init__`), 115-120 (nouvelle methode), 203-216 (callbacks wrapes) | MOYEN |

## Verification

- `pytest tests/ -v` : **70/70 tests passent** apres chaque fix
- Aucune regression detectee
- Les fixes ne changent pas la logique metier testee, uniquement le timing (BUG 1), le build installateur (BUG 2), et le threading GUI (BUG 3)
