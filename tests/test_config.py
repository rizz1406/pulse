import unittest
from unittest import mock

import config


class TestConfig(unittest.TestCase):
    def test_validate_allows_local_only_mode(self):
        with mock.patch.object(config, "GROQ_API_KEY", ""):
            config.validate()

    def test_validate_rejects_placeholder_key(self):
        with mock.patch.object(config, "GROQ_API_KEY", "your-key"):
            with self.assertRaises(RuntimeError):
                config.validate()


if __name__ == "__main__":
    unittest.main()
