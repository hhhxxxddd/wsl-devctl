from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


class ExampleTests(unittest.TestCase):
    def test_templates_are_unbranded_and_valid_toml(self) -> None:
        root = Path(__file__).resolve().parents[1]
        examples = sorted((root / "examples").glob("dev-*.toml"))
        self.assertGreaterEqual(len(examples), 3)
        for path in examples:
            self.assertTrue(path.name.startswith("dev-"))
            with path.open("rb") as stream:
                value = tomllib.load(stream)
            self.assertTrue(str(value["name"]).startswith("dev-"))
            text = path.read_text(encoding="utf-8").lower()
            for brand in ("acme", "examplecorp", "internal-product"):
                self.assertNotIn(brand, text)


if __name__ == "__main__":
    unittest.main()
