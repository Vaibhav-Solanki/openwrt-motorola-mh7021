# luci-theme-aurora — vendored

Upstream: <https://github.com/eamonxg/luci-theme-aurora> (Apache-2.0)

| | |
|---|---|
| version | 1.3.1 (`PKG_RELEASE` 20260904) |
| commit | `c8e8be3a86b324e1daa44e3406b2b8561437c512` |
| commit date | 2026-09-04 |
| vendored on | 2026-09-12 |

Vendored rather than fetched at build time so a release is reproducible and
`release.sh` does not need the build host to reach GitHub. `apply.sh` copies this
directory to `$T/package/luci-theme-aurora/`; `config.seed` selects it.

**Payload only.** `Makefile`, `LICENSE`, `htdocs/`, `ucode/` and `root/` are copied
verbatim from upstream. The repo's development tooling (`.dev/`, `.github/`,
`.vscode/`, `.claude/`, `.gitignore`) is deliberately left out: none of it is part
of the package, and nothing in it should run here.

## Known issue on this LuCI (cosmetic, not patched)

LuCI 26.180 moved `String.prototype.format` from `luci.js` into `cbi.js`. Aurora's
`header.ut` loads `cbi.js` only under `{% if (!blank_page) %}`, and the **login page**
is a blank page, so there `new LuCI()` throws and `window.L` is left undefined:

    TypeError: "%s/%s.js%s".format is not a function

Confined to the login screen and harmless: nothing on that page needs `L` (its only
scripts set field focus and read a background token, and the form is a plain POST).
Authenticated pages load `cbi.js` → `luci.js` → `new LuCI()` in that order and
initialise normally — verified on neon-god, 2026-09-12.

The upstream fix is one line: move that `<script src="cbi.js">` outside the
`!blank_page` guard, where luci-theme-bootstrap has it. Left unpatched so this tree
stays byte-identical to upstream and updating is a straight re-copy. Patch it here
(or upstream) if the console noise matters.

## Updating

    git clone --depth 1 https://github.com/eamonxg/luci-theme-aurora.git
    cp -R luci-theme-aurora/{Makefile,LICENSE,htdocs,ucode,root} \
          router/owrt/mh7021/luci-theme-aurora/
    # refresh the version/commit table above, then: bash router/owrt/sync.sh
