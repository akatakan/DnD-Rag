import unittest

from errors import AppError, normalize_error


class QdrantFailure(Exception):
    __module__ = "qdrant_client.http.exceptions"


class NormalizeErrorTest(unittest.TestCase):
    def test_identifies_qdrant_by_exception_type(self):
        error = normalize_error(QdrantFailure("connection refused"), "ollama")

        self.assertIsInstance(error, AppError)
        self.assertIn("Qdrant", str(error))

    def test_identifies_gemini_auth_failure(self):
        error = normalize_error(RuntimeError("HTTP 401 unauthorized"), "gemini")

        self.assertIn("API anahtarı", str(error))


if __name__ == "__main__":
    unittest.main()
