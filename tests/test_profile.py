from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

from iris.config import IrisConfig
from iris.profile import infer_system_profile, load_user_profile, save_user_profile
from iris.state import open_state
from tests.test_state_features import build_config


class ProfileTests(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {
            "IRIS_USER_NAME": "Clinton Imaro",
            "IRIS_USER_FIRST_NAME": "",
            "IRIS_USER_PRONOUNS": "he/him",
        },
        clear=False,
    )
    def test_infers_first_name_from_full_name(self) -> None:
        profile = infer_system_profile()
        self.assertEqual(profile.full_name, "Clinton Imaro")
        self.assertEqual(profile.preferred_name, "Clinton")
        self.assertEqual(profile.pronouns, "he/him")

    def test_saved_profile_overrides_inferred_profile(self) -> None:
        with tempfile.TemporaryDirectory() as handle:
            config: IrisConfig = build_config(Path(handle))
            with open_state(config) as db:
                save_user_profile(db, preferred_name="CJ", pronouns="he/him")
                profile = load_user_profile(config, db)
        self.assertEqual(profile.preferred_name, "CJ")
        self.assertEqual(profile.pronouns, "he/him")


if __name__ == "__main__":
    unittest.main()
