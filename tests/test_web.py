"""Static checks of the web page: every element the scripts look up exists, every import
names something the other module exports, and the bundled three.js is complete."""

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def scripts():
    return [WEB / "app.js", WEB / "viewer.js"] + sorted((WEB / "js").glob("*.js"))


class WebWiringTests(unittest.TestCase):
    def test_every_id_used_by_the_scripts_is_in_the_page(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        ids = set(re.findall(r'\bid="([^"]+)"', html))
        for f in scripts():  # elements the scripts create themselves
            ids |= set(re.findall(r"""\bid:\s*['"]([\w-]+)['"]""", f.read_text(encoding="utf-8")))
        missing = []
        for f in scripts():
            for ref in re.findall(r"""\$\(\s*['"`]#([A-Za-z][\w-]*)""", f.read_text(encoding="utf-8")):
                if ref not in ids:
                    missing.append("%s: #%s" % (f.name, ref))
        self.assertEqual(missing, [])

    def test_imports_match_exports(self):
        problems = []
        for f in scripts():
            text = f.read_text(encoding="utf-8")
            for names, src in re.findall(r"import\s*\{([^}]+)\}\s*from\s*'(\.[^']+)'", text):
                target = (f.parent / src).resolve()
                if not target.is_file():
                    problems.append("%s imports missing %s" % (f.name, src))
                    continue
                exported = set(re.findall(r"export\s+(?:async\s+)?(?:function|const|let|class)\s+([\w$]+)",
                                          target.read_text(encoding="utf-8")))
                for name in names.split(","):
                    name = name.split(" as ")[0].strip()
                    if name and name not in exported:
                        problems.append("%s imports %s from %s, which doesn't export it" % (f.name, name, src))
        self.assertEqual(problems, [])

    def test_bundled_three_is_complete(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('"three": "/web/vendor/three/three.module.js"', html)
        vendor = WEB / "vendor" / "three"
        self.assertTrue((vendor / "LICENSE").is_file())
        viewer = (WEB / "viewer.js").read_text(encoding="utf-8")
        for addon in re.findall(r"import\('three/addons/([^']+)'\)", viewer):
            f = vendor / "addons" / addon
            self.assertTrue(f.is_file(), addon)
            for dep in re.findall(r"from '(\.[^']+)'", f.read_text(encoding="utf-8")):
                self.assertTrue((f.parent / dep).resolve().is_file(), "%s needs %s" % (addon, dep))

    def test_every_marked_page_text_is_translated(self):
        import json
        html = (WEB / "index.html").read_text(encoding="utf-8")
        i18n = (WEB / "js" / "i18n.js").read_text(encoding="utf-8")
        texts = set(re.findall(r"data-i18n(?:-[a-z]+)?=\"([^\"]+)\"", html))
        texts |= {re.sub(r"\s+", " ", t).strip() for t in re.findall(r"data-i18n>([^<]+)<", html)}
        files = sorted((WEB / "js" / "lang").glob("*.js"))
        self.assertGreaterEqual(len(files), 12)
        missing = []
        for f in files:
            self.assertIn("import %s from './lang/%s.js';" % (f.stem, f.stem), i18n, "%s isn't loaded" % f.name)
            pairs = re.findall(r'^\s*("(?:[^"\\]|\\.)*"):\s*("(?:[^"\\]|\\.)*"),$', f.read_text(encoding="utf-8"), re.M)
            keys = {json.loads(k) for k, _ in pairs}
            self.assertTrue(all(json.loads(v).strip() for _, v in pairs), "%s has an empty translation" % f.name)
            missing += ["%s: %s" % (f.stem, t) for t in sorted(texts) if t not in keys]
        self.assertEqual(missing, [])

    def test_languages_menu_lists_every_language_file(self):
        i18n = (WEB / "js" / "i18n.js").read_text(encoding="utf-8")
        menu = re.search(r"export const LANGUAGES = \{(.*?)\};", i18n, re.S).group(1)
        codes = set(re.findall(r"\b([a-z]{2}):", menu))
        self.assertEqual(codes - {"en"}, {f.stem for f in (WEB / "js" / "lang").glob("*.js")})

    def test_no_inline_scripts_or_handlers_besides_the_import_map(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        inline = [m for m in re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", html) if "importmap" not in m]
        self.assertEqual(inline, [])
        self.assertIsNone(re.search(r"\son[a-z]+=", html))


if __name__ == "__main__":
    unittest.main()
