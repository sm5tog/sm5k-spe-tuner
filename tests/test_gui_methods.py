"""Spartest: varje `self.x` som klassen TunerGUI laser maste vara definierat.

Bakgrund: 2026-11-10 forsvann metoden `_tx_band` nar ett skript ersatte ett
helt kodblock. Anropet `self._tx_band(...)` blev kvar, men inget test tackte
GUI-klassen (pytest testar bara ren logik), och GUI:t frös vid korning.

Testet tolkar spe_tuner_klient.py och spe_tuner.py med `ast` (ingen import,
inget GUI byggs). Ett namn raknas som definierat om det ar en `def`, en
tilldelning pa klassniva, eller nagonstans i klassen skrivs/tas bort som
`self.x` (Store/Del), samt Pythons egna (`__dict__`, `__class__`).

BASELINE listar kanda undantag (dod kod). Den far bara krympa: testet faller
bade pa nya saknade namn och pa en baslinjepost som inte langre ar saknad.
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FILES = ("spe_tuner_klient.py", "spe_tuner.py")
BUILTIN_ATTRS = {"__dict__", "__class__"}

# Anvands bara i den doda metoden `_on_sweep_toggle` (anropas ingenstans).
BASELINE = {
    ("spe_tuner_klient.py", "_sweep_var"),
    ("spe_tuner_klient.py", "_sweep_chk"),
    ("spe_tuner.py", "_sweep_var"),
    ("spe_tuner.py", "_sweep_chk"),
}


def _missing(fn):
    """Lista (namn, radnummer) for `self.x`-lasningar som saknar definition."""
    with open(os.path.join(ROOT, fn), encoding="utf-8-sig") as f:
        tree = ast.parse(f.read(), filename=fn)
    out = []
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TunerGUI"):
        defined = set(BUILTIN_ATTRS)
        for n in cls.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(n.name)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        defined.add(t.id)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                defined.add(n.target.id)
        used = []
        for n in ast.walk(cls):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id == "self"):
                if isinstance(n.ctx, (ast.Store, ast.Del)):
                    defined.add(n.attr)
                else:
                    used.append((n.attr, n.lineno))
        out += [(a, l) for a, l in used if a not in defined]
    return out


def test_tunergui_self_attributes_are_defined():
    found = {fn: _missing(fn) for fn in FILES}
    new = [f"{fn}:{line} self.{name}" for fn, items in found.items()
           for name, line in items if (fn, name) not in BASELINE]
    assert not new, "TunerGUI laser self.x som inte definieras: " + "; ".join(new)

    still = {(fn, name) for fn, items in found.items() for name, _ in items}
    fixed = sorted(BASELINE - still)
    assert not fixed, ("Baslinjeposter ar inte langre saknade, ta bort dem ur BASELINE: "
                       + ", ".join(f"{fn} {name}" for fn, name in fixed))
