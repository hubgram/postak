import unittest

from postak.generation import OpenAIGenerator


class OpenAIGeneratorTest(unittest.TestCase):
    def test_set_model_changes_future_model(self) -> None:
        generator = OpenAIGenerator(model="first", api_key="test-key")

        generator.set_model("second")

        self.assertEqual(generator.model, "second")


if __name__ == "__main__":
    unittest.main()
