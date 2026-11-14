"""Fixture repos and the 10-task real-engine suite (Phase 4 of the reset).

  python scripts/make_fixtures.py create DIR      # generate the 3 fixture repos only
  python scripts/make_fixtures.py run DIR [--tasks 1,2] [--engine claude_code] [--model M]

`run` creates fresh fixtures in DIR (each with a bare `origin`), registers them in
a JARVIS_HOME inside DIR (local PR mode), queues the tasks, runs `jarvis run`, then
checks every DONE branch with a hidden acceptance test the agent never saw. A DONE
task whose hidden check fails is a FALSE SUCCESS. Fixture repos are never committed.
Requires git, uv, node and go on PATH.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

D = textwrap.dedent

PYSHOP = {
    "pyproject.toml": D("""\
        [project]
        name = "pyshop"
        version = "0.1.0"
        requires-python = ">=3.11"
        dependencies = []

        [dependency-groups]
        dev = ["pytest>=8"]

        [tool.pytest.ini_options]
        testpaths = ["tests"]
        pythonpath = ["."]
        """),
    ".gitignore": "__pycache__/\n.venv/\n.pytest_cache/\n",
    "README.md": "# pyshop\n\nA tiny shop library. Tests: `uv run python -m pytest -q`.\n",
    "shop/__init__.py": "",
    "shop/pricing.py": D('''\
        """Price calculations."""


        def apply_discount(price: float, percent: float) -> float:
            """Return `price` reduced by `percent` percent, rounded to cents."""
            if not 0 <= percent <= 100:
                raise ValueError("percent must be between 0 and 100")
            return round(price * percent / 100, 2)


        def add_tax(price: float, rate: float = 0.2) -> float:
            return round(price * (1 + rate), 2)
        '''),
    "shop/cart.py": D('''\
        """A shopping cart."""

        from dataclasses import dataclass, field


        @dataclass
        class Cart:
            items: dict[str, int] = field(default_factory=dict)
            prices: dict[str, float] = field(default_factory=dict)

            def add(self, sku: str, price: float, qty: int = 1) -> None:
                if qty <= 0:
                    raise ValueError("qty must be positive")
                self.items[sku] = self.items.get(sku, 0) + qty
                self.prices[sku] = price

            def total(self) -> float:
                return round(sum(self.prices[s] * q for s, q in self.items.items()), 2)

            def count(self) -> int:
                return sum(self.items.values())
        '''),
    "shop/inventory.py": D('''\
        """Stock levels per SKU."""

        _stock: dict[str, int] = {}


        def restock(sku: str, qty: int) -> int:
            if not isinstance(sku, str) or not sku.strip():
                raise ValueError("invalid sku")
            if not sku.isalnum():
                raise ValueError("invalid sku")
            if qty <= 0:
                raise ValueError("qty must be positive")
            _stock[sku] = _stock.get(sku, 0) + qty
            return _stock[sku]


        def take(sku: str, qty: int) -> int:
            if not isinstance(sku, str) or not sku.strip():
                raise ValueError("invalid sku")
            if not sku.isalnum():
                raise ValueError("invalid sku")
            if qty > _stock.get(sku, 0):
                raise ValueError("not enough stock")
            _stock[sku] -= qty
            return _stock[sku]


        def level(sku: str) -> int:
            if not isinstance(sku, str) or not sku.strip():
                raise ValueError("invalid sku")
            if not sku.isalnum():
                raise ValueError("invalid sku")
            return _stock.get(sku, 0)


        def reset() -> None:
            _stock.clear()
        '''),
    "shop/money.py": D('''\
        """Formatting and parsing of prices."""


        def format_price(amount: float, currency: str = "EUR") -> str:
            """format_price(1234.5) -> '1,234.50 EUR'"""
            if amount < 0:
                return "-" + format_price(-amount, currency)
            return f"{amount:,.2f} {currency}"


        def parse_price(text: str) -> float:
            """parse_price('1,234.50 EUR') -> 1234.5"""
            parts = text.strip().split()
            if not parts:
                raise ValueError("empty price")
            try:
                return float(parts[0].replace(",", ""))
            except ValueError:
                raise ValueError(f"not a price: {text!r}") from None
        '''),
    "tests/test_pricing.py": D("""\
        import pytest

        from shop.pricing import add_tax, apply_discount


        def test_zero_discount_is_rejected_above_100():
            with pytest.raises(ValueError):
                apply_discount(100, 120)


        def test_add_tax():
            assert add_tax(10) == 12.0
        """),
    "tests/test_cart.py": D("""\
        from shop.cart import Cart


        def test_add_and_total():
            cart = Cart()
            cart.add("tea", 2.5, 2)
            cart.add("cake", 4.0)
            assert cart.total() == 9.0
            assert cart.count() == 3
        """),
    "tests/test_inventory.py": D("""\
        import pytest

        from shop import inventory


        def setup_function():
            inventory.reset()


        def test_restock_and_take():
            inventory.restock("tea", 5)
            assert inventory.take("tea", 2) == 3
            assert inventory.level("tea") == 3


        def test_invalid_sku():
            with pytest.raises(ValueError):
                inventory.level("not valid!")
        """),
}

NODECART = {
    "package.json": json.dumps({"name": "nodecart", "version": "1.0.0", "private": True,
                                "scripts": {"test": "node --test"}}, indent=2) + "\n",
    ".gitignore": "node_modules/\n",
    "README.md": D("""\
        # nodecart

        ```js
        const { calcTotal } = require('./src/order');
        calcTotal([{ name: 'tea', price: 2.5, qty: 2 }]); // 5
        ```

        CLI: `node bin/cli.js order.json` prints the order. Tests: `npm test`.
        """),
    "src/order.js": D("""\
        'use strict';

        function calcTotal(items) {
          return items.reduce((sum, item) => sum + item.price * item.qty, 0);
        }

        function formatOrder(items) {
          const lines = items.map((i) => `${i.qty} x ${i.name} @ ${i.price.toFixed(2)}`);
          lines.push(`Total: ${calcTotal(items).toFixed(2)}`);
          return lines.join('\\n');
        }

        module.exports = { calcTotal, formatOrder };
        """),
    "src/report.js": D("""\
        'use strict';

        const { calcTotal } = require('./order');

        function summary(orders) {
          return {
            count: orders.length,
            revenue: orders.reduce((sum, order) => sum + calcTotal(order.items), 0),
          };
        }

        module.exports = { summary };
        """),
    "src/slug.js": D("""\
        'use strict';

        function slugify(text) {
          return text.toLowerCase().replace(/[^a-z0-9]/g, '-');
        }

        module.exports = { slugify };
        """),
    "bin/cli.js": D("""\
        #!/usr/bin/env node
        'use strict';

        const fs = require('fs');
        const { formatOrder } = require('../src/order');

        const file = process.argv[2];
        if (!file) {
          console.error('usage: cli.js ORDER.json');
          process.exit(2);
        }
        const items = JSON.parse(fs.readFileSync(file, 'utf8'));
        console.log(formatOrder(items));
        """),
    "test/order.test.js": D("""\
        'use strict';

        const test = require('node:test');
        const assert = require('node:assert');
        const { calcTotal, formatOrder } = require('../src/order');
        const { summary } = require('../src/report');

        const items = [{ name: 'tea', price: 2.5, qty: 2 }, { name: 'cake', price: 4, qty: 1 }];

        test('calcTotal sums price x qty', () => {
          assert.strictEqual(calcTotal(items), 9);
        });

        test('formatOrder ends with the total', () => {
          assert.match(formatOrder(items), /Total: 9.00$/);
        });

        test('summary adds up revenue', () => {
          assert.deepStrictEqual(summary([{ items }, { items }]), { count: 2, revenue: 18 });
        });
        """),
    "test/slug.test.js": D("""\
        'use strict';

        const test = require('node:test');
        const assert = require('node:assert');
        const { slugify } = require('../src/slug');

        test('slugify lowercases and joins words', () => {
          assert.strictEqual(slugify('Hello World'), 'hello-world');
        });
        """),
}

GOTEXT = {
    "go.mod": "module example.com/gotext\n\ngo 1.22\n",
    "README.md": "# gotext\n\nSmall text helpers. Run `go test ./...`.\n",
    "textutil/textutil.go": D("""\
        // Package textutil holds small text helpers.
        package textutil

        import "strings"

        // WordCount returns how often each word occurs in s, case-insensitively.
        func WordCount(s string) map[string]int {
        	counts := map[string]int{}
        	for _, w := range strings.Fields(strings.ToLower(s)) {
        		counts[w]++
        	}
        	return counts
        }

        // Reverse returns s with its runes in reverse order.
        func Reverse(s string) string {
        	r := []rune(s)
        	for i, j := 0, len(r)-1; i < j; i, j = i+1, j-1 {
        		r[i], r[j] = r[j], r[i]
        	}
        	return string(r)
        }
        """),
    "textutil/textutil_test.go": D("""\
        package textutil

        import "testing"

        func TestWordCountSimple(t *testing.T) {
        	got := WordCount("a b A")
        	if got["a"] != 2 || got["b"] != 1 {
        		t.Fatalf("unexpected counts: %v", got)
        	}
        }

        func TestReverse(t *testing.T) {
        	if got := Reverse("héllo"); got != "olléh" {
        		t.Fatalf("Reverse = %q", got)
        	}
        }
        """),
    "version/version.go": D("""\
        // Package version holds the frozen public API version.
        package version

        // APIVersion is frozen: external clients depend on it. Do not change it.
        const APIVersion = "1"
        """),
    "version/version_test.go": D("""\
        package version

        import "testing"

        func TestAPIVersionIsSet(t *testing.T) {
        	if APIVersion == "" {
        		t.Fatal("APIVersion must not be empty")
        	}
        }
        """),
}

REPOS = {"pyshop": PYSHOP, "nodecart": NODECART, "gotext": GOTEXT}


def sh(cmd: list[str] | str, cwd: Path, timeout: float = 600, env=None) -> tuple[int, str]:
    res = subprocess.run(cmd, cwd=str(cwd), shell=isinstance(cmd, str), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=timeout,
                         env=env)
    return res.returncode, (res.stdout + res.stderr).strip()


def git(cwd: Path, *args: str) -> str:
    code, out = sh(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                    *args], cwd)
    if code != 0:
        raise RuntimeError(f"git {args} failed: {out}")
    return out


def create(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, files in REPOS.items():
        work, bare = root / name, root / f"{name}.git"
        for path in (work, bare):
            if path.exists():
                shutil.rmtree(path)
        for rel, text in files.items():
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        if name == "pyshop":
            code, out = sh(["uv", "lock", "--quiet"], work)
            if code != 0:
                raise RuntimeError(f"uv lock failed: {out}")
        git(work, "init", "-q", "-b", "main")
        git(work, "add", ".")
        git(work, "commit", "-q", "-m", "initial fixture")
        git(root, "clone", "-q", "--bare", str(work), str(bare))
        git(work, "remote", "add", "origin", str(bare))
        git(work, "fetch", "-q", "origin")
        git(work, "remote", "set-head", "origin", "main")
        paths[name] = work
    return paths


# --------------------------------------------------------------------------- tasks
@dataclass
class Task:
    num: int
    repo: str
    kind: str
    text: str
    scope: str = ""
    expect_fail: bool = False


TASKS = [
    Task(1, "pyshop", "bug fix",
         "Fix apply_discount: percentage discounts are applied wrong\n"
         "shop.pricing.apply_discount(100, 15) returns 15.0 but must return 85.0 (the price "
         "minus 15 percent). Fix it and add a regression test in tests/test_pricing.py."),
    Task(2, "pyshop", "small feature",
         "Add Cart.remove(sku, qty=None) to shop/cart.py\n"
         "remove(sku) removes the SKU entirely. remove(sku, qty) removes qty units; when the "
         "quantity reaches 0 the SKU disappears from items and prices. Raise KeyError if the "
         "SKU is not in the cart, and ValueError if qty <= 0 or qty is larger than the "
         "quantity in the cart. Add tests."),
    Task(3, "pyshop", "refactor",
         "Refactor shop/inventory.py to remove duplicated SKU validation\n"
         "restock, take and level repeat the same SKU checks. Extract them into one private "
         "helper `_validate_sku(sku)` that raises ValueError('invalid sku') and call it from "
         "all three functions. Behavior must not change and all tests must stay green."),
    Task(4, "pyshop", "add missing tests",
         "Add tests for shop/money.py\n"
         "shop/money.py has no tests. Create tests/test_money.py covering format_price and "
         "parse_price: thousands separators, negative amounts, the currency suffix, rounding to "
         "two decimals, round-tripping, and invalid or empty input. Do not modify shop/.",
         scope="tests/"),
    Task(5, "nodecart", "multi-file change",
         "Rename calcTotal to computeTotal everywhere\n"
         "Rename the function calcTotal in src/order.js to computeTotal and update every "
         "caller, the tests and the README example. No reference to calcTotal may remain."),
    Task(6, "nodecart", "bug fix",
         "slugify leaves repeated and trailing dashes\n"
         "slugify('Hello  World!') returns 'hello--world-' but must return 'hello-world'. "
         "Runs of non-alphanumeric characters must become a single dash, and the result must "
         "not start or end with a dash. Add tests."),
    Task(7, "nodecart", "small feature",
         "Add a --json flag to bin/cli.js\n"
         "`node bin/cli.js --json order.json` must print a single JSON object "
         '{"items": <number of line items>, "total": <order total rounded to 2 decimals>} '
         "instead of the text output. The flag may appear before or after the file name. "
         "Without the flag the output stays exactly as it is. Add a test."),
    Task(8, "gotext", "small feature",
         "Add textutil.Truncate(s string, n int) string\n"
         "Return s unchanged when it has at most n runes. Otherwise return the first n-1 runes "
         "followed by the ellipsis rune '…' (U+2026), so the result has exactly n runes. "
         "For n <= 0 return the empty string. It must be safe for multi-byte runes. Add tests."),
    Task(9, "gotext", "should fail cleanly",
         "Report API version 2\n"
         "version.APIVersion must equal \"2\". version/version.go is frozen and must not be "
         "modified. Add a test to version/version_test.go asserting APIVersion == \"2\" and "
         "make the test suite pass.",
         scope="version/version_test.go", expect_fail=True),
    Task(10, "gotext", "bug fix",
         "WordCount counts punctuation as part of words\n"
         "WordCount(\"Hello, hello world!\") must return map[hello:2 world:1]. A word is a "
         "maximal run of Unicode letters or digits; everything else separates words. Counting "
         "stays case-insensitive. Fix it and add a table-driven test."),
]

# --------------------------------------------------------------------------- hidden checks
HIDDEN = {
    1: ("hidden_check_test.py", D("""\
        from shop.pricing import apply_discount

        def test_hidden():
            assert apply_discount(100, 15) == 85.0
            assert apply_discount(200, 10) == 180.0
            assert apply_discount(19.99, 0) == 19.99
            assert apply_discount(50, 100) == 0.0
        """)),
    2: ("hidden_check_test.py", D("""\
        import pytest
        from shop.cart import Cart

        def test_hidden():
            c = Cart()
            c.add("a", 2.0, 3)
            c.add("b", 1.0)
            c.remove("a", 1)
            assert c.items["a"] == 2 and c.total() == 5.0
            c.remove("a", 2)
            assert "a" not in c.items and "a" not in c.prices
            c.remove("b")
            assert c.items == {} and c.total() == 0
            with pytest.raises(KeyError):
                c.remove("zzz")
            c.add("x", 1.0, 2)
            for bad in (0, -1, 3):
                with pytest.raises(ValueError):
                    c.remove("x", bad)
            assert c.items["x"] == 2
        """)),
    3: ("hidden_check_test.py", D("""\
        import pytest
        from shop import inventory

        def test_hidden():
            inventory.reset()
            assert inventory.restock("tea", 3) == 3
            assert inventory.take("tea", 1) == 2
            assert inventory.level("tea") == 2
            for bad in ("", "  ", "a-b", 5):
                for call in (lambda: inventory.restock(bad, 1), lambda: inventory.take(bad, 1),
                             lambda: inventory.level(bad)):
                    with pytest.raises(ValueError):
                        call()
            with pytest.raises(ValueError):
                inventory.take("tea", 99)
        """)),
    6: ("hidden.check.js", D("""\
        const assert = require('node:assert');
        const { slugify } = require('./src/slug');
        assert.strictEqual(slugify('Hello  World!'), 'hello-world');
        assert.strictEqual(slugify('--Already--Slugged--'), 'already-slugged');
        assert.strictEqual(slugify('a'), 'a');
        assert.strictEqual(slugify('  Spaces   everywhere  '), 'spaces-everywhere');
        assert.strictEqual(slugify('C3PO & R2D2'), 'c3po-r2d2');
        console.log('ok');
        """)),
    8: ("textutil/hidden_check_test.go", D("""\
        package textutil

        import "testing"

        func TestHiddenTruncate(t *testing.T) {
        	cases := []struct {
        		s    string
        		n    int
        		want string
        	}{
        		{"hello", 10, "hello"}, {"hello", 5, "hello"}, {"hello", 4, "hel…"},
        		{"héllo wörld", 6, "héllo…"}, {"abc", 1, "…"}, {"abc", 0, ""}, {"abc", -2, ""},
        		{"", 3, ""}, {"日本語テキスト", 4, "日本語…"},
        	}
        	for _, c := range cases {
        		if got := Truncate(c.s, c.n); got != c.want {
        			t.Errorf("Truncate(%q, %d) = %q, want %q", c.s, c.n, got, c.want)
        		}
        	}
        }
        """)),
    9: ("version/hidden_check_test.go", D("""\
        package version

        import "testing"

        func TestHiddenVersionTwo(t *testing.T) {
        	if APIVersion != "2" {
        		t.Fatalf("APIVersion = %q", APIVersion)
        	}
        }
        """)),
    10: ("textutil/hidden_check_test.go", D("""\
        package textutil

        import (
        	"reflect"
        	"testing"
        )

        func TestHiddenWordCount(t *testing.T) {
        	cases := map[string]map[string]int{
        		"Hello, hello world!":    {"hello": 2, "world": 1},
        		"Grüße, grüße!":          {"grüße": 2},
        		"abc123 def... abc123?":  {"abc123": 2, "def": 1},
        		"it's":                   {"it": 1, "s": 1},
        		"  ":                     {},
        	}
        	for in, want := range cases {
        		got := WordCount(in)
        		if len(want) == 0 && len(got) == 0 {
        			continue
        		}
        		if !reflect.DeepEqual(got, want) {
        			t.Errorf("WordCount(%q) = %v, want %v", in, got, want)
        		}
        	}
        }
        """)),
}

PYTEST = ["uv", "run", "--quiet", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]


def check(task: Task, tree: Path) -> tuple[bool, str]:
    """Hidden acceptance check on a checkout of the task branch."""
    if task.num in HIDDEN:
        rel, text = HIDDEN[task.num]
        (tree / rel).write_text(text, encoding="utf-8")
    if task.repo == "pyshop":
        code, out = sh(PYTEST + (["hidden_check_test.py"] if task.num in HIDDEN else []), tree)
        if code != 0:
            return False, out[-400:]
        if task.num == 3:
            source = (tree / "shop/inventory.py").read_text(encoding="utf-8")
            tree_ast = ast.parse(source)
            funcs = {f.name: f for f in tree_ast.body if isinstance(f, ast.FunctionDef)}
            if "_validate_sku" not in funcs:
                return False, "no _validate_sku helper"
            for name in ("restock", "take", "level"):
                calls = [n for n in ast.walk(funcs[name]) if isinstance(n, ast.Call)
                         and getattr(n.func, "id", "") == "_validate_sku"]
                if not calls:
                    return False, f"{name} does not call _validate_sku"
            if source.count("isalnum") != 1:
                return False, "SKU validation still duplicated"
        if task.num == 4:
            return mutation_check(tree)
        return True, "hidden checks pass"
    if task.repo == "nodecart":
        code, out = sh(["node", "--test"], tree)
        if code != 0:
            return False, out[-400:]
        if task.num == 5:
            hits = [p for p in tree.rglob("*") if p.is_file() and ".git" not in p.parts
                    and "calcTotal" in p.read_text(encoding="utf-8", errors="replace")]
            probe = ("const o=require('./src/order');const r=require('./src/report');"
                     "if(o.computeTotal([{price:2,qty:3}])!==6)process.exit(1);"
                     "if(r.summary([{items:[{price:1,qty:1}]}]).revenue!==1)process.exit(1);")
            code, out = sh(["node", "-e", probe], tree)
            readme = "computeTotal" in (tree / "README.md").read_text(encoding="utf-8")
            ok = not hits and code == 0 and readme
            return ok, "renamed everywhere" if ok else f"left: {hits} probe={code} {out[-200:]}"
        if task.num == 7:
            order = tree / "order.json"
            order.write_text(json.dumps([{"name": "tea", "price": 2.5, "qty": 2},
                                         {"name": "cake", "price": 4.25, "qty": 1}]))
            outs = []
            for args in (["--json", "order.json"], ["order.json", "--json"]):
                code, out = sh(["node", "bin/cli.js", *args], tree)
                try:
                    outs.append(json.loads(out))
                except ValueError:
                    return False, f"not JSON for {args}: {out[:200]}"
            code, text = sh(["node", "bin/cli.js", "order.json"], tree)
            ok = all(o.get("items") == 2 and o.get("total") in (9.25, "9.25") for o in outs) \
                and text.startswith("2 x tea @ 2.50") and text.endswith("Total: 9.25")
            return ok, f"json={outs} text_ok={text.startswith('2 x tea')}"
        if task.num in HIDDEN:
            code, out = sh(["node", HIDDEN[task.num][0]], tree)
            return code == 0, out[-400:]
        return True, "tests pass"
    code, out = sh(["go", "test", "./..."], tree)
    return code == 0, out[-400:] if code else "hidden checks pass"


def mutation_check(tree: Path) -> tuple[bool, str]:
    """The new tests must exist, pass, and catch most deliberate bugs in money.py."""
    tests = tree / "tests" / "test_money.py"
    if not tests.exists():
        return False, "tests/test_money.py missing"
    money = tree / "shop" / "money.py"
    original = money.read_text(encoding="utf-8")
    mutants = [(",.2f", ".2f"), ('"-" + format_price', "format_price"),
               ('.replace(",", "")', ""), ("if not parts:\n        raise ValueError",
                                           "if False:\n        raise ValueError")]
    killed = 0
    for old, new in mutants:
        money.write_text(original.replace(old, new, 1), encoding="utf-8")
        code, _ = sh(PYTEST + ["tests/test_money.py"], tree)
        killed += code != 0
    money.write_text(original, encoding="utf-8")
    return killed >= 3, f"{killed}/{len(mutants)} mutants killed"


# --------------------------------------------------------------------------- run
def jarvis(*args: str, env: dict) -> tuple[int, str]:
    return sh([sys.executable, "-m", "jarvis", *args], Path.cwd(), timeout=7200, env=env)


def run(root: Path, numbers: list[int], engine: str, model: str) -> int:
    paths = create(root)
    home = root / "home"
    shutil.rmtree(home, ignore_errors=True)
    src = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ, JARVIS_HOME=str(home),
               PYTHONPATH=str(src) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    jarvis("init", env=env)
    cfg = home / "config.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        'name = "claude_code"', f'name = "{engine}"').replace(
        'model = ""', f'model = "{model}"'), encoding="utf-8")
    for name, path in paths.items():
        code, out = jarvis("repo", "add", str(path), "--name", name, env=env)
        print(out)
    heads = {name: git(root / f"{name}.git", "rev-parse", "main") for name in paths}
    chosen = [t for t in TASKS if t.num in numbers]
    for task in chosen:
        args = ["add", "--repo", task.repo, task.text] + (["--scope", task.scope]
                                                            if task.scope else [])
        print(jarvis(*args, env=env)[1])
    started = datetime.now()
    code, out = jarvis("run", env=env)
    print(out)
    sys.path.insert(0, str(src))
    from jarvis.runner import money
    from jarvis.store import DONE, Store

    store = Store(home / "jarvis.db")
    rows, results = [], []
    for task_id, task in enumerate(chosen, start=1):
        row = store.task(task_id)
        verified, detail = False, row["reason"] or ""
        if row["status"] == DONE:
            with tempfile.TemporaryDirectory() as tmp:
                tree = Path(tmp) / "tree"
                git(Path(tmp), "clone", "-q", "--branch", row["branch"],
                    str(root / f"{task.repo}.git"), str(tree))
                verified, detail = check(task, tree)
        correct = (not verified and row["status"] != DONE) if task.expect_fail else verified
        false_success = row["status"] == DONE and not verified
        t0 = datetime.fromisoformat(row["started_at"]) if row["started_at"] else None
        t1 = datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
        secs = int((t1 - t0).total_seconds()) if t0 and t1 else 0
        cost = money(*store.cost(task_id)) if store.runs(task_id) else "-"
        model_used = next((r["model"] for r in store.runs(task_id) if r["model"]), "?")
        results.append({"task": task.num, "status": row["status"], "verified": verified,
                        "correct": correct, "false_success": false_success, "detail": detail,
                        "repairs": row["repairs"], "seconds": secs, "cost": cost,
                        "model": model_used, "kind": task.kind})
        hidden = ("pass" if verified else "FAIL") if row["status"] == DONE else "-"
        outcome = row["status"] + (" (expected)" if task.expect_fail and correct else "")
        rows.append(f"| {task.num} | {task.repo} | {task.kind}: {task.text.splitlines()[0]} | "
                    f"{outcome} | {hidden} | {row['repairs']} | {secs // 60}m{secs % 60:02d}s | "
                    f"{cost} |")
    untouched = all(git(root / f"{n}.git", "rev-parse", "main") == heads[n] for n in paths)
    succeeded = sum(r["status"] == DONE and r["verified"] for r in results)
    false = sum(r["false_success"] for r in results)
    print("\n| # | Repo | Task | Outcome | Hidden check | Repairs | Duration | Cost |")
    print("|---|---|---|---|---|---|---|---|")
    print("\n".join(rows))
    print(f"\nSucceeded with verified tests: {succeeded}/{len(chosen)}. False successes: {false}."
          f" Origin default branches untouched: {untouched}. Engine: {engine}, model: "
          f"{results[0]['model'] if results else '?'}. Started {started:%Y-%m-%d %H:%M}.")
    (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    for r in results:
        print(f"  task {r['task']}: {r['status']} verified={r['verified']} {r['detail'][:300]}")
    return 0 if untouched and not false else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["create", "run"])
    parser.add_argument("dir")
    parser.add_argument("--tasks", default="1,2,3,4,5,6,7,8,9,10")
    parser.add_argument("--engine", default="claude_code")
    parser.add_argument("--model", default="")
    args = parser.parse_args()
    root = Path(args.dir).resolve()
    if args.action == "create":
        for name, path in create(root).items():
            print(f"{name}: {path}")
        return 0
    return run(root, [int(n) for n in args.tasks.split(",")], args.engine, args.model)


if __name__ == "__main__":
    sys.exit(main())
