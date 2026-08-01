#!/usr/bin/env python3
"""SM5K_SPE_TunerUpdater — fristående installer/updater för SM5K SPE Tuner.

Samma exe-fil hanterar både förstagångsinstallation (ingen
SM5K_SPE_Tuner.exe hittas i mappen) och uppdatering av en befintlig
installation (SM5K_SPE_Tuner.exe finns redan, eller anropas med
--update). Byggs till SM5K_SPE_TunerUpdater.exe och paketeras i SAMMA
release-zip som SM5K_SPE_Tuner.exe (inte som en separat nedladdning) —
det är det som gör att updatern kan uppdatera SIG SJÄLV, som en ren
bieffekt av att den uppdaterar resten av programmet, se
_finish_self_update() nedan. Direkt kopia av mönstret i
jtspots_updater.py (repo jtspots), bara namn/inställningar bytta.

Ren standardbibliotek — inga extra beroenden, inga registerposter, ingen
"Appar och funktioner"-integration (medvetet val, samma som jtspots).
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

RELEASE_REPO = 'sm5tog/sm5k-spe-tuner'
API_URL      = f'https://api.github.com/repos/{RELEASE_REPO}/releases/latest'
UPDATER_NAME = 'SM5K_SPE_TunerUpdater.exe'
MAIN_EXE     = 'SM5K_SPE_Tuner.exe'
SETTINGS_NAME = 'settings.json'
_UA = {'User-Agent': 'SM5K-SPE-TunerUpdater'}


def is_frozen() -> bool:
    return getattr(sys, 'frozen', False)


def own_dir() -> Path:
    return Path(sys.executable).parent if is_frozen() else Path(__file__).parent


def own_path() -> Path:
    return Path(sys.executable) if is_frozen() else Path(__file__)


def fetch_latest_release_info():
    """-> (tag_name, zip_download_url). Kastar vid nätverksfel/saknad zip —
    anroparen fångar och skriver ett läsbart felmeddelande."""
    req = urllib.request.Request(API_URL, headers=_UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode('utf-8'))
    tag = data['tag_name']
    zip_url = None
    for asset in data.get('assets', []):
        if asset.get('name', '').lower().endswith('.zip'):
            zip_url = asset['browser_download_url']
            break
    if not zip_url:
        raise RuntimeError('Ingen zip-fil hittades i senaste GitHub-releasen')
    return tag, zip_url


def download_file(url: str, dest: Path):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, 'wb') as f:
        total = int(r.headers.get('Content-Length', 0) or 0)
        read = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            read += len(chunk)
            if total:
                print(f'\r  {read * 100 // total}%', end='', flush=True)
        print()


def _write_with_retry(out_path: Path, src_fileobj, timeout=10.0):
    """Extraherar en enskild fil med kort retry vid PermissionError —
    SM5K_SPE_Tuner.exe kan precis ha avslutat sig (spawnat updatern och
    stängt), Windows hinner ibland inte släppa filhandtaget direkt."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    while True:
        try:
            with open(out_path, 'wb') as dst:
                shutil.copyfileobj(src_fileobj, dst)
            return
        except PermissionError:
            if time.time() >= deadline:
                raise
            time.sleep(0.3)
            src_fileobj.seek(0)


def extract_release(zip_path: Path, target_dir: Path, self_exe_name=None):
    """Packar upp hela zippen i target_dir. Om self_exe_name anges (bara vid
    uppdateringsläge, körd som frozen exe) hamnar just DEN filen under ett
    tillfälligt namn (<namn>.new) istället för att skriva över sig själv
    medan den kör — se _finish_self_update(). Rör ALDRIG SETTINGS_NAME
    (settings.json) härifrån om den redan finns lokalt, de befintliga
    inställningarna (COM-port, radiokonfiguration, ui_mode) ska bevaras."""
    staged_updater = None
    settings_exists = (target_dir / SETTINGS_NAME).exists()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = Path(info.filename)
            if rel.name == SETTINGS_NAME and settings_exists:
                continue
            if self_exe_name and rel.name == self_exe_name:
                out_path = target_dir / (self_exe_name + '.new')
                staged_updater = out_path
            else:
                out_path = target_dir / rel
            with zf.open(info) as src:
                _write_with_retry(out_path, src)
    return staged_updater


def launch_app(install_dir: Path):
    exe = install_dir / MAIN_EXE
    if exe.exists():
        subprocess.Popen([str(exe)], cwd=str(install_dir))
    else:
        print(f'VARNING: hittade ingen {MAIN_EXE} i {install_dir}')


def spawn_detached(args):
    """Startar en process som fortsätter leva oberoende av oss — används
    bara för självuppdaterings-hjälpprocessen (se _finish_self_update)."""
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(args, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                      close_fds=True)


def _finish_self_update(final_path: Path, staged_path: Path):
    """Körs som en KORT separat process, spawnad av den gamla updatern precis
    innan den avslutar sig. Windows kan inte skriva över en körande exe-fil,
    så den nya versionen packades upp under ett tillfälligt namn — här byter
    vi bara namn på den till det riktiga, med retry tills den gamla
    processen verkligen har stängt sitt filhandtag."""
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            os.replace(staged_path, final_path)
            return
        except OSError:
            time.sleep(0.3)


def run_install(default_dir: Path):
    print('=== SM5K SPE Tuner installation ===')
    answer = input(f'Installera i "{default_dir}"? [ENTER=ja, eller ange annan sokvag]: ').strip()
    target_dir = Path(answer) if answer else default_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    print('Hamtar senaste versionsinfo...')
    tag, zip_url = fetch_latest_release_info()
    print(f'Laddar ner SM5K SPE Tuner {tag}...')
    zip_path = target_dir / '_spe_tuner_download.zip'
    download_file(zip_url, zip_path)

    print('Packar upp...')
    extract_release(zip_path, target_dir)
    zip_path.unlink(missing_ok=True)

    print('Klart. Startar SM5K SPE Tuner...')
    launch_app(target_dir)


def run_update(install_dir: Path):
    print('=== SM5K SPE Tuner uppdatering ===')
    print('Hamtar senaste versionsinfo...')
    tag, zip_url = fetch_latest_release_info()
    print(f'Laddar ner SM5K SPE Tuner {tag}...')
    zip_path = install_dir / '_spe_tuner_download.zip'
    download_file(zip_url, zip_path)

    print('Packar upp...')
    self_name = UPDATER_NAME if is_frozen() else None
    staged_updater = extract_release(zip_path, install_dir, self_exe_name=self_name)
    zip_path.unlink(missing_ok=True)

    print('Klart. Startar SM5K SPE Tuner...')
    launch_app(install_dir)

    if staged_updater is not None:
        spawn_detached([str(own_path()), '--finish-self-update',
                        str(own_path()), str(staged_updater)])


def main():
    args = sys.argv[1:]
    if args and args[0] == '--finish-self-update':
        _finish_self_update(Path(args[1]), Path(args[2]))
        return

    install_dir = own_dir()
    update_mode = ('--update' in args) or (install_dir / MAIN_EXE).exists()
    try:
        if update_mode:
            run_update(install_dir)
        else:
            run_install(install_dir)
    except Exception as e:
        print(f'FEL: {e}')
        input('Tryck ENTER for att stanga...')
        return
    time.sleep(1.5)


if __name__ == '__main__':
    main()
