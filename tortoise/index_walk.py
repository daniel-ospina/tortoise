"""Custom bounded walker + inode/mount reconciliation — epic #900 T3.

Orchestration-side helpers (plan §8.6 T3 canonical-pin index: W1 walker pin,
W4 hardlink row, W4 mount row). Pure-ish — no graph/SDK imports, so the
decision logic is unit-testable WITHOUT mount privilege on every platform.

- ``walk_markdown`` — the bounded walker: ``*.md`` files over a sorted list,
  FOLLOWS directory symlinks (a plain ``Path.rglob('**')`` never descends dir
  links — E2E-7(m) would silently miss every file under a linkdir), bounds
  cycles via an ANCESTOR-CHAIN realpath visited-set (true cycle detection
  ONLY — non-cyclic sibling dir aliases stay enumerated; W1 cycle-4/5 pins),
  counts non-md entries (``ignored`` — §6.4 cycle-22 visibility fix), and
  records walk-time (directory-level) OSErrors per-directory (E2E-7(y): never
  silent, never abort).
- ``compute_dispositions`` — the pre-write pass over the SORTED walk list:
  realpath dedup (symlink aliases → first sorted path indexed, the rest
  ``symlink-duplicate``), hardlink inode alias reconciliation via lstat on
  NON-SYMLINK entries only (W4 hardlink row cycle-5 pin: ``st_nlink`` vs the
  in-walk same-inode count → unreconciled-outside-alias ``failed`` [NEVER
  READ] / mount-alias dedup ``inode-duplicate`` / url-keyed safe read), and
  the mount-source escape check for descendant mount points.
- Mount machinery: ``mount_source_for`` (the INJECTABLE provider — the named
  monkeypatch target; Linux ``/proc/self/mountinfo``, macOS/BSD
  ``getmntinfo(3)`` via ctypes), ``mount_decision`` (the PURE decision
  function over (is_root, source_determinable, source_inside_root) — root
  exemption + lookup-miss/undeterminable warn-not-fail cells), and
  ``mount_source_for_file`` (the index_file parent-dir-chain consumer,
  cycle-7/8 seam).
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Skipped/failed dispositions computed pre-write (the per-file handler
# consults them; ``None`` = process normally).
DISP_SYMLINK_DUPLICATE = "symlink-duplicate"
DISP_INODE_DUPLICATE = "inode-duplicate"
DISP_ESCAPE = "escape"          # symlink target / mount source outside root


def _is_under(path: Path, root: Path) -> bool:
    """True iff ``path`` is ``root`` or under it (pure path containment)."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
DISP_UNRECONCILED = "unreconciled"  # hardlink alias cannot be proven root-local
DISP_STRUCTURAL = "structural"  # non-regular, non-symlink entry (FIFO/socket/…)


@dataclass
class WalkResult:
    """Sorted ``*.md`` walk list + the visibility counters (§3.1/§6.4)."""

    files: list[tuple[Path, os.stat_result]] = field(default_factory=list)
    dirs_rejected: int = 0  # dir symlinks whose resolved target escapes root (REVIEW-FIX P1)
    ignored: int = 0                    # non-md entries seen, never read
    dir_errors: list[dict] = field(default_factory=list)  # walk-time OSErrors
    # Realpaths of descendant mount points whose source resolves OUTSIDE the
    # corpus root — every contained file is failed-escape, NEVER READ (W4
    # mount row; realpath resolves in-root, so the mount-source check is the
    # ONLY catch).
    banned_prefixes: list[str] = field(default_factory=list)
    # warn-not-fail mount points (undeterminable host / lookup miss) —
    # descended, with an errors[] warning naming the mount point (cycle-6/7).
    mount_warnings: list[str] = field(default_factory=list)


@dataclass
class Dispositions:
    """Pre-write per-entry disposition map + mount warnings."""

    by_path: dict[str, str] = field(default_factory=dict)
    # realpath → first sorted path (for the single-file election of which
    # alias is "the" indexed path when duplicates resolve to one url).
    realpath_primary: dict[str, str] = field(default_factory=dict)


def walk_markdown(root: Path, *, base: str | None = None) -> WalkResult:
    """Bounded walk over ``root`` — sorted ``*.md`` files, dir-symlink-following.

    The output list is sorted by path (election pins + first-sorted-path
    semantics depend on it). Non-``*.md`` entries are counted (``ignored``),
    never read. Directory-symlink cycles are pruned via the ancestor-chain
    realpath set; walk-time OSErrors (unreadable subdir) are recorded
    per-directory, never an abort (E2E-7(y)).

    Mount-point detection (W4 mount row; cycle-6/7): a DESCENDANT dir whose
    ``st_dev`` differs from its parent's is a mount point — the SAME pure
    ``mount_decision`` + injectable ``mount_source_for`` provider as
    ``index_file``'s parent-dir chain. An outside-root source → the subtree
    is BANNED (every contained ``*.md`` file is dispositioned escape, never
    read); undeterminable/miss → warn-not-fail (descend + warning naming the
    mount point). The declared root is exempt (root-local BY DECLARATION).
    """
    result = WalkResult()
    resolved_root = root.resolve()  # REVIEW-FIX P1: realpath-vs-realpath escape base
    # Ancestor-chain realpath set — true cycle detection ONLY (W1 cycle-5):
    # a dir reached through two sibling aliases is NOT pruned at the
    # directory level (its files ARE enumerated; the realpath dedup in
    # compute_dispositions reports the per-file symlink-duplicate).
    root_real = os.path.realpath(str(root))

    def _is_md_name(name: str) -> bool:
        # Case-sensitive `*.md` BY DESIGN (§6.4): `.MD` is not indexed.
        return name.endswith(".md")

    def _walk(dirpath: Path, chain: frozenset[str], parent_dev: int | None) -> None:
        rp = os.path.realpath(str(dirpath))
        if rp in chain:
            return  # loopdir a→b→a — prune (bounded, no hang)
        try:
            st = os.lstat(dirpath)
        except OSError:
            return
        # ── mount-point detection (st_dev change vs the parent dir) ──
        if parent_dev is not None and st.st_dev != parent_dev:
            source = mount_source_for(dirpath)
            decision = mount_decision(
                is_root=(rp == root_real),
                source_determinable=(source is not None),
                source_inside_root=(source is not None
                                    and _path_under(source, root_real, base)),
            )
            if decision == "fail":
                result.banned_prefixes.append(rp)
            elif decision == "warn":
                result.mount_warnings.append(
                    f"mount point {dirpath}: mount source undeterminable or "
                    f"lookup miss — warn-not-fail (W4 mount row)")
        try:
            entries = sorted(os.scandir(dirpath), key=lambda e: e.name)
        except OSError as e:
            # Walk-time (directory-level) OSError — per-directory accounting,
            # NEVER silent, NEVER an abort (E2E-7(y)).
            result.dir_errors.append(
                {"dir": str(dirpath), "error": str(e), "retryable": False}
            )
            return
        child_chain = chain | {rp}
        for entry in entries:
            if entry.name.endswith(".md"):
                # `*.md`-NAMED entries are file candidates — INCLUDING a
                # directory named `x.md` (E2E-7(e): the per-file handler's
                # IsADirectoryError → failed structural), a FIFO/socket named
                # `x.md` (S_ISREG check → failed structural, zero reads —
                # E2E-7(w)), and a broken/loop symlink named `x.md`. We never
                # descend a `*.md`-named directory.
                try:
                    fst = entry.stat(follow_symlinks=False)
                except OSError:
                    fst = None
                result.files.append((Path(entry.path), fst))
                continue
            # Non-md entry: dir (real or symlinked) → descend; anything else
            # (regular file, fifo, socket, UPPER.MD, …) → counted, not read.
            try:
                if entry.is_dir(follow_symlinks=True):
                    # REVIEW-FIX P1 (cycle-26): resolved-target escape check
                    # BEFORE descending — a dir symlink whose target resolves
                    # OUTSIDE the corpus root must NOT be descended (W1's
                    # "applies the resolved-target escape check per alias";
                    # §6.4 "NEVER silent follow"; E2E-7 zero-reads-outside
                    # the base). The target's realpath must stay under the
                    # RESOLVED root (realpath-vs-realpath, per §4.1).
                    resolved = Path(entry.path).resolve()
                    if not _is_under(resolved, resolved_root):
                        result.dirs_rejected += 1
                        continue
                    _walk(Path(entry.path), child_chain, st.st_dev)
                    continue
            except OSError:
                pass  # broken/looping non-md symlink → counted ignored
            result.ignored += 1

    if root.is_dir():
        _walk(root, frozenset(), None)
    # Sorted walk list — deterministic order for election + first-sorted-path.
    result.files.sort(key=lambda pair: str(pair[0]))
    return result


def compute_dispositions(files: list[tuple[Path, os.stat_result | None]],
                         root: Path,
                         banned_prefixes: list[str] | None = None) -> Dispositions:
    """Pre-write disposition pass over the SORTED walk list (§4.1/W4).

    Runs BEFORE any write (deterministic; no check-then-act). Order of
    classification per entry:

    - under a BANNED mount prefix (outside-root mount source): ``escape``
      (never read — the mount-source check is the ONLY catch);
    - symlink (lstat mode S_IFLNK): realpath-resolved target — outside root
      → ``escape``; broken/loop → ``structural``; inside root → realpath
      dedup: first sorted path per realpath processes normally, later paths
      → ``symlink-duplicate`` (no writes).
    - non-symlink, non-regular (FIFO/socket/dir-named-.md…): ``structural``
      (S_ISREG fails — zero reads).
    - regular file: inode alias reconciliation via lstat (st_dev, st_ino) on
      NON-SYMLINK entries only (cycle-5 pin — counting symlink entries would
      inflate the in-walk count and let a symlink+hardlink combo bypass the
      stat-only rejection, E2E-7(t)):
        - in-walk same-inode count > st_nlink → mount/firmlink alias
          signature → first sorted path processes, the rest
          ``inode-duplicate`` (one Source per physical file; never silent
          double-index; hardlink+mount combo: mount signature dominates);
        - in-walk same-inode count < st_nlink → an out-of-walk alias exists
          and cannot be proven root-local → ``unreconciled`` (failed,
          NEVER READ — the #329 file-read-oracle class);
        - count == st_nlink → all aliases provably in-walk → url-keyed
          processing for every entry (each derives its OWN url → distinct
          Sources with identical contentHash — documented url-keyed
          behavior).
    """
    disp = Dispositions()
    by_realpath: dict[str, list[str]] = {}
    # (st_dev, st_ino) → sorted list of NON-SYMLINK paths (cycle-5 lstat pin).
    by_inode: dict[tuple[int, int], list[str]] = {}
    root_real = os.path.realpath(str(root))
    banned = [os.path.realpath(b) for b in (banned_prefixes or [])]

    for path, st in files:
        spath = str(path)
        if banned:
            real = os.path.realpath(spath)
            if any(real == b or real.startswith(b + os.sep) for b in banned):
                disp.by_path[spath] = DISP_ESCAPE
                continue
        if st is None or not stat.S_ISLNK(st.st_mode):
            if st is not None and stat.S_ISREG(st.st_mode):
                by_inode.setdefault((st.st_dev, st.st_ino), []).append(spath)
            continue
        # ── symlink entry ──
        try:
            target = os.path.realpath(spath)
        except OSError:
            disp.by_path[spath] = DISP_STRUCTURAL  # broken/loop (ELOOP)
            continue
        try:
            Path(target).relative_to(Path(root_real))
        except ValueError:
            disp.by_path[spath] = DISP_ESCAPE
            continue
        by_realpath.setdefault(target, []).append(spath)

    # Realpath dedup — first sorted path per realpath processes normally.
    for target, paths in by_realpath.items():
        primary = paths[0]
        disp.realpath_primary[target] = primary
        for later in paths[1:]:
            disp.by_path[later] = DISP_SYMLINK_DUPLICATE

    # Inode alias reconciliation (regular files, non-symlink only).
    for (dev, ino), paths in by_inode.items():
        first_st = next(
            (st for p, st in files if str(p) == paths[0] and st is not None), None
        )
        nlink = int(first_st.st_nlink) if first_st is not None else 0
        in_walk = len(paths)
        if in_walk > nlink:
            # Mount/firmlink alias signature (impossible for pure hardlinks):
            # dedup to ONE Source per physical file.
            for later in paths[1:]:
                disp.by_path[later] = DISP_INODE_DUPLICATE
        elif in_walk < nlink:
            # Out-of-walk alias exists — cannot be proven root-local.
            for p in paths:
                disp.by_path[p] = DISP_UNRECONCILED
        # else: count == nlink → all aliases in-walk → url-keyed (process all).

    # ── Combo-link inode check (W4 hardlink row, E2E-7(t), cycle-5 P1 pin):
    # the inode check applies to a symlink's RESOLVED target too. A symlink
    # whose resolved target is an UNRECONCILED inode group (in-walk count <
    # st_nlink — an out-of-walk alias exists) must ALSO be dispositioned
    # unreconciled: reading through it would stat/read the outside-root file
    # via the in-root hardlink entry (the #329 file-read-oracle bypass the
    # stat-only rejection exists to close). Realpath escape cannot catch it
    # (both entries resolve in-root). The symlink itself is NOT inode-counted
    # (lstat pin — it never joined by_inode); this pass marks it from its
    # RESOLVED target's group instead.
    for spath, st in files:
        sp = str(spath)
        if st is None or not stat.S_ISLNK(st.st_mode):
            continue
        if disp.by_path.get(sp) is not None:
            continue  # already dispositioned (escape/structural/dup)
        try:
            target = os.path.realpath(sp)
        except OSError:
            continue
        # realpath-vs-realpath comparison (macOS /var → /private/var: the
        # walked path and the resolved target can differ lexically — map via
        # the realpath form, not raw string equality).
        for opath, ost in files:
            if ost is None or stat.S_ISLNK(ost.st_mode):
                continue
            if (disp.by_path.get(str(opath)) == DISP_UNRECONCILED
                    and os.path.realpath(str(opath)) == target):
                disp.by_path[sp] = DISP_UNRECONCILED
                break
    return disp


# ── Mount machinery (W4 mount row; cycle-6/7/8 pins) ─────────────────────

def mount_source_for(directory: str | Path) -> str | None:
    """Resolve the mount SOURCE for a mount point — the INJECTABLE provider.

    Named monkeypatch target (cycle-7 detection seam): both the walk and
    ``index_file``'s parent-dir chain consume this function, and the tests
    patch it to fabricate outside-root/inside-root sources without mount
    privilege. Returns ``None`` when the host has no mount table or the
    entry is MISSING (lookup miss — warn-not-fail cell) or the platform is
    undeterminable (Windows/stripped containers).
    """
    try:
        entries = _mount_table()
    except Exception:  # noqa: BLE001 — undeterminable host ⇒ None (warn-not-fail)
        return None
    real = os.path.realpath(str(directory))
    best: tuple[int, str] | None = None
    for mp, source in entries:
        try:
            rel = Path(real).relative_to(Path(mp))
        except ValueError:
            continue
        depth = len(rel.parts)
        if best is None or depth < best[0]:
            best = (depth, source)
    return best[1] if best is not None else None


def _mount_table() -> list[tuple[str, str]]:
    """(mount_point, source) list — Linux mountinfo or macOS getmntinfo."""
    if sys_platform_linux():
        return _mountinfo_table()
    if sys_platform_darwin():
        return _getmntinfo_table()
    return []


def sys_platform_linux() -> bool:
    return os.name == "posix" and _platform_name() == "linux"


def sys_platform_darwin() -> bool:
    return _platform_name() == "darwin"


def _platform_name() -> str:
    import sys
    return sys.platform


def _mountinfo_table() -> list[tuple[str, str]]:
    """Parse ``/proc/self/mountinfo`` — (mount_point, source) pairs.

    Format: ``mount_id parent_id major:minor root mount_point opts … - fstype source superopts``
    — the source is two fields after the ``-`` separator.
    """
    out: list[tuple[str, str]] = []
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if "-" not in parts:
                    continue
                sep = parts.index("-")
                if sep < 5 or len(parts) < sep + 3:
                    continue
                mount_point = _unquote_mountinfo(parts[4])
                source = _unquote_mountinfo(parts[sep + 2])
                out.append((mount_point, source))
    except OSError:
        return []
    return out


def _unquote_mountinfo(value: str) -> str:
    # mountinfo escapes spaces/octal — decode for path prefix matching.
    try:
        return bytes(value, "utf-8").decode("unicode_escape")
    except Exception:  # noqa: BLE001
        return value


def _getmntinfo_table() -> list[tuple[str, str]]:
    """macOS/BSD ``getmntinfo(3)`` via ctypes — (mount_point, source) pairs.

    Correlation contract (cycle-7): the provider NEVER correlates via raw
    ``f_fsid == st_dev`` equality (verified divergent on darwin) — the
    caller detects mount points via ``st_dev`` change and this provider
    resolves the SOURCE by mount-point path prefix (a tested mapping
    helper, per the cycle-7 pin's parenthetical).
    """
    try:
        import ctypes
        import ctypes.util
    except Exception:  # noqa: BLE001
        return []
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc", use_errno=True)
    if not hasattr(libc, "getmntinfo"):
        return []

    MAXPATHLEN = 1024
    MFSNAMELEN = 15

    class _StatFS(ctypes.Structure):
        # macOS statfs(2) layout (sys/mount.h) — exercised by the
        # darwin-gated + synthesized-buffer tests.
        _fields_ = [
            ("f_bsize", ctypes.c_uint32),
            ("f_iosize", ctypes.c_int32),
            ("f_blocks", ctypes.c_uint64),
            ("f_bfree", ctypes.c_uint64),
            ("f_bavail", ctypes.c_uint64),
            ("f_files", ctypes.c_uint64),
            ("f_ffree", ctypes.c_uint64),
            ("f_fsid", ctypes.c_uint64),
            ("f_owner", ctypes.c_uint32),
            ("f_type", ctypes.c_uint32),
            ("f_flags", ctypes.c_uint32),
            ("f_fssubtype", ctypes.c_uint32),
            ("f_fstypename", ctypes.c_char * MFSNAMELEN),
            ("f_mntonname", ctypes.c_char * MAXPATHLEN),
            ("f_mntfromname", ctypes.c_char * MAXPATHLEN),
            ("f_flags_ext", ctypes.c_uint32),
            ("f_reserved", ctypes.c_uint32 * 3),
        ]

    getmntinfo = libc.getmntinfo
    getmntinfo.restype = ctypes.POINTER(_StatFS)
    getmntinfo.argtypes = [ctypes.POINTER(ctypes.POINTER(_StatFS)), ctypes.c_int]
    ptr = ctypes.POINTER(_StatFS)()
    count = getmntinfo(ctypes.byref(ptr), 2)  # MNT_NOWAIT
    out: list[tuple[str, str]] = []
    for i in range(max(count, 0)):
        entry = ptr[i]
        mp = entry.f_mntonname.decode("utf-8", "replace")
        src = entry.f_mntfromname.decode("utf-8", "replace")
        out.append((mp, src))
    return out


def mount_decision(*, is_root: bool, source_determinable: bool,
                   source_inside_root: bool) -> str:
    """PURE mount decision — the cycle-6/7 decision table, unit-tested over ALL
    cells without mount privilege (T3/S11):

    root × anything            → ``ok`` (root-local BY DECLARATION — external-
                                 volume / bind-mounted roots index; E2E-7(s3));
    descendant, determinable, source inside root → ``ok``;
    descendant, determinable, source OUTSIDE root → ``fail`` (escape class —
                                 every contained file failed, NEVER READ);
    descendant, undeterminable (no table / lookup MISS) → ``warn`` (warn-not-
                                 fail, descend + errors[] warning naming the
                                 mount point — cycle-6/7 pins incl. the
                                 cycle-7 lookup-miss fifth cell).
    """
    if is_root:
        return "ok"
    if not source_determinable:
        return "warn"
    return "ok" if source_inside_root else "fail"


def mount_source_inside_root(mount_point: str | Path, root: str | Path,
                             base: str | None = None) -> bool:
    """True when ``mount_point``'s source resolves under the corpus root.

    ``base`` (TORTOISE_INGEST_BASE_DIR) narrows the check when set (the
    source must also resolve under the base — the sandbox the walk is
    bound to).
    """
    source = mount_source_for(mount_point)
    if source is None:
        return True  # undeterminable/miss — caller applies warn-not-fail
    try:
        src_real = os.path.realpath(source)
        Path(src_real).relative_to(Path(os.path.realpath(str(root))))
        if base is not None:
            Path(src_real).relative_to(Path(os.path.realpath(base)))
        return True
    except ValueError:
        return False


def mount_source_for_file(file_path: str | Path, root: str | Path,
                          base: str | None = None) -> list[dict]:
    """index_file parent-dir-chain mount check (cycle-7/8 pin).

    Walks the RESOLVED file's ancestor chain (``realpath(file)``'s parents up
    to the resolved corpus root) and applies the mount-source check to every
    dir whose ``st_dev`` differs from its parent's (stat-only — needs NO walk
    context). Returns a list of warnings/errors; an entry with
    ``{"fail": True}`` means an OUTSIDE-root mount source was found on the
    chain — the caller must fail the file closed, NEVER READ.

    The SAME pure ``mount_decision`` + the SAME injectable ``mount_source_for``
    as the walk (``index_file`` is the third seam consumer, cycle-7 pin).
    """
    file_real = os.path.realpath(str(file_path))
    root_real = os.path.realpath(str(root))
    try:
        Path(file_real).relative_to(Path(root_real))
    except ValueError:
        return [{"fail": True, "error": f"{file_path!r} resolves outside "
                                         f"corpus root {root_real!r} — escape rejected"}]
    notes: list[dict] = []
    # REVIEW-FIX P2 (cycle-26): off-by-one — start the chain at the file's
    # parent with parent_dev from the GRANDPARENT (or the parent itself when
    # there is no grandparent), so the immediate parent's mount point is
    # actually checked (the old code compared the parent against itself).
    current = Path(file_real).parent
    parent_dev = None
    try:
        parent_dev = os.lstat(str(current.parent)).st_dev
    except OSError:
        try:
            parent_dev = os.lstat(str(current)).st_dev
        except OSError:
            pass
    while True:
        if current == Path(root_real):
            break  # the declared root is root-local BY DECLARATION
        try:
            st = os.lstat(str(current))
        except OSError:
            notes.append({"warn": True, "error": f"cannot lstat {current}"})
            break
        if parent_dev is not None and st.st_dev != parent_dev:
            # ── mount point detected (st_dev change) ──
            source = mount_source_for(current)
            decision = mount_decision(
                is_root=(current == Path(root_real)),
                source_determinable=(source is not None),
                source_inside_root=(source is not None and _path_under(source, root_real, base)),
            )
            if decision == "fail":
                return [{"fail": True,
                         "error": f"mount point {current} has a source "
                                  f"outside the corpus root — fail closed, NEVER READ"}]
            if decision == "warn":
                notes.append({"warn": True,
                              "error": f"mount point {current}: mount source "
                                       f"undeterminable or lookup miss — "
                                       f"warn-not-fail (W4 mount row)"})
        parent_dev = st.st_dev
        parent = current.parent
        if parent == current:
            break
        current = parent
    return notes


def _path_under(candidate: str, root_real: str, base: str | None) -> bool:
    try:
        Path(os.path.realpath(candidate)).relative_to(Path(root_real))
        if base is not None:
            Path(os.path.realpath(candidate)).relative_to(Path(os.path.realpath(base)))
        return True
    except ValueError:
        return False
