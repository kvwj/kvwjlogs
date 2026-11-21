import os
from pathlib import Path


MUSIC_LIBRARY_FOLDERS = (
    "ye olde new gold/playback/clockwheel rotation/A power oldie",
    "ye olde new gold/playback/clockwheel rotation/B medium oldie",
    "ye olde new gold/playback/clockwheel rotation/C slow oldie",
    "ye olde new gold/playback/clockwheel rotation/D extra slow oldie",
    "ye olde new gold/playback/clockwheel rotation/E beatles-beachboys-elvis",
    "ye olde new gold/playback/clockwheel rotation/F image track",
    "ye olde new gold/playback/clockwheel rotation/G golden oldie",
    "ye olde new gold/playback/clockwheel rotation/J psychedelia",
    "ye olde new gold/playback/clockwheel rotation/K christmas-holiday",
    "pls/Day Only",
    "pls/Night only",
    "pls/That 80s Show",
)


def _walk_music_library(root):
    root = Path(root)
    if not root.is_dir():
        raise OSError(f"Music library is unavailable: {root}. Check the music share mount.")
    resolved_root = root.resolve(strict=True)
    for relative in MUSIC_LIBRARY_FOLDERS:
        folder = root / relative
        if not folder.is_dir():
            raise FileNotFoundError(f"Expected music folder is unavailable: {folder}")
        if not folder.resolve(strict=True).is_relative_to(resolved_root):
            raise PermissionError(f"Music folder is outside the mounted library: {folder}")
        for directory, folders, filenames in os.walk(folder, onerror=_raise_scan_error):
            folders[:] = sorted(name for name in folders if (
                "sunday" not in name.casefold() and "special liners" not in name.casefold()
            ))
            yield directory, filenames


def find_track_audio(root, filename, category):
    if (
        not filename or not category or "/" in filename or "\\" in filename
        or Path(filename).suffix.casefold() != ".mp3"
    ):
        raise ValueError("The track needs a stored MP3 filename and category.")
    root = Path(root).resolve(strict=True)
    matches = []
    for directory, filenames in _walk_music_library(root):
        if Path(directory).name.casefold() != category.casefold():
            continue
        for name in filenames:
            if name.casefold() != filename.casefold():
                continue
            path = (Path(directory) / name).resolve(strict=True)
            if not path.is_relative_to(root):
                raise PermissionError("The music file is outside the mounted library.")
            if path.is_file():
                matches.append(path)
    if not matches:
        raise FileNotFoundError("No matching MP3 was found in the track's current category.")
    if len(matches) > 1:
        raise ValueError("Multiple MP3 files match this filename and category; cannot choose a recording.")
    return matches[0]


def _raise_scan_error(error):
    raise error


def _filename_song(path, is_eighties):
    if is_eighties:
        parts = path.stem.split(" - ", 1)
        if len(parts) != 2:
            return None
        artist, title = (part.strip() for part in parts)
    else:
        parts = path.stem.rsplit(" - ", 2)
        if len(parts) != 3:
            return None
        title, artist, _ = (part.strip() for part in parts)
    return (artist, title) if artist and title else None


def scan_music_overlap(root, tracks):
    catalog = {}
    catalog_names = {}
    for track in tracks:
        if not track.categoryid_id or not track.artistid_id:
            continue
        title = track.display_name.strip()
        artist = track.artistid.display_name.strip()
        category = (track.categoryid.category or "").strip()
        if not title or not artist or not category:
            continue
        identity = (artist.casefold(), title.casefold())
        if track.filename:
            file_key = (category.casefold(), track.filename.casefold())
            catalog.setdefault(file_key, {})[identity] = (artist, title)
        original_title = (track.track_name or "").strip()
        original_artist = (track.artistid.artist_name or "").strip()
        if original_title and original_artist:
            name_key = (category.casefold(), original_artist.casefold(), original_title.casefold())
            catalog_names.setdefault(name_key, {})[identity] = (artist, title)
    songs = {}
    errors = []
    scanned = 0

    for directory, filenames in _walk_music_library(root):
        category = Path(directory).name
        is_eighties = "80s" in category.casefold()
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.suffix.casefold() != ".mp3":
                continue
            scanned += 1
            identities = catalog.get((category.strip().casefold(), filename.casefold()), {})
            if not identities:
                parsed = _filename_song(path, is_eighties)
                if parsed:
                    artist, title = parsed
                    key = (artist.casefold(), title.casefold())
                    identities = catalog_names.get((category.strip().casefold(), *key), {key: parsed})
            if len(identities) != 1:
                errors.append(str(path))
                continue
            key, (artist, title) = next(iter(identities.items()))
            song = songs.setdefault(key, {
                "artist": artist, "title": title, "eighties_paths": [], "other_paths": [],
            })
            song["eighties_paths" if is_eighties else "other_paths"].append(str(path))
    return {
        "songs": [
            songs[key] for key in sorted(songs)
            if songs[key]["eighties_paths"] and songs[key]["other_paths"]
        ],
        "errors": errors,
        "scanned": scanned,
    }
