import tempfile
import unittest
from pathlib import Path
from app.storage import Store
from app.provenance import create_job, verify_job


class ProvenanceTests(unittest.TestCase):
    def test_snapshot_rejects_reapproval_of_changed_content(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"first")
            store = Store(Path(directory) / "studio.db")
            store.save_source(str(source), "Creator", "", "Permission", True)
            job = create_job(store, str(source), 1, 3)
            verify_job(store, job)
            source.write_bytes(b"second")
            store.save_source(str(source), "Creator", "", "Permission", True)
            with self.assertRaises(PermissionError):
                verify_job(store, job)

    def test_unapproved_source_cannot_create_job(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.touch()
            store = Store(Path(directory) / "studio.db")
            with self.assertRaises(PermissionError):
                create_job(store, str(source), 0, 2)

    def test_invalid_segment_rejected(self):
        with self.assertRaises(ValueError):
            create_job(None, "unused", 5, 1)


if __name__ == "__main__":
    unittest.main()
