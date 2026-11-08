"""Migration regression checks; do not import bot.py (it starts/configures the bot)."""
import ast
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "bot.py"


class FailClosedDbTests(unittest.TestCase):
    def test_db_does_not_change_configured_path_on_failure(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        func = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "db")
        # Compile only db(); importing bot.py would initialize the production DB.
        compiled = compile(ast.Module(body=[func], type_ignores=[]), str(SOURCE), "exec")
        scope = {"sqlite3": sqlite3, "DB": "/unavailable/production/motivation.db"}
        exec(compiled, scope)
        with patch.object(sqlite3, "connect", side_effect=sqlite3.OperationalError("disk unavailable")) as connect:
            with self.assertRaises(sqlite3.OperationalError):
                scope["db"]()
        connect.assert_called_once_with("/unavailable/production/motivation.db")
        self.assertEqual(scope["DB"], "/unavailable/production/motivation.db")

    def test_no_fallback_messages_or_assignment(self):
        code = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("Falling back to local motivation.db", code)
        self.assertNotIn('DB = "motivation.db"\n', code)


if __name__ == "__main__":
    unittest.main()
