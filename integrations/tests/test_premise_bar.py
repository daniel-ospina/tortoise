#!/usr/bin/env python3
"""Tests for premise_bar — menu bar app launcher."""
import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "menu-bar"))
import premise_bar

# Rest of tests unchanged from the working version above
class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.orig_path = premise_bar.CONFIG_PATH
    def tearDown(self):
        premise_bar.CONFIG_PATH = self.orig_path
        os.unlink(self.tmp.name)
    def test_load_valid_config(self):
        config = {"services": [{"id":"t","name":"T","icon":"x","check":{"type":"http","url":"http://localhost:9999"},"launch":{"type":"shell","command":"echo ok"},"stop":{"type":"shell","command":"echo ok"}}]}
        with open(self.tmp.name,"w") as f: json.dump(config,f)
        premise_bar.CONFIG_PATH = self.tmp.name
        self.assertEqual(len(premise_bar.load_config()),1)
    def test_load_empty_config(self):
        with open(self.tmp.name,"w") as f: json.dump({"services":[]},f)
        premise_bar.CONFIG_PATH = self.tmp.name
        self.assertEqual(premise_bar.load_config(),[])
    def test_missing_creates_default(self):
        premise_bar.CONFIG_PATH = "/tmp/nonexistent-premise-config.json"
        self.assertIsInstance(premise_bar.load_config(),list)

class TestServiceChecks(unittest.TestCase):
    def test_check_http_offline(self):
        self.assertFalse(premise_bar.check_http("http://localhost:19999",timeout=1))
    @patch("premise_bar.socket.create_connection")
    def test_check_tcp_open(self,m): self.assertTrue(premise_bar.check_tcp("localhost",6379,timeout=1))
    @patch("premise_bar.socket.create_connection",side_effect=OSError)
    def test_check_tcp_closed(self,m): self.assertFalse(premise_bar.check_tcp("localhost",19999,timeout=1))
    @patch("premise_bar.subprocess.run")
    def test_check_process_running(self,m):
        m.return_value.returncode=0
        self.assertTrue(premise_bar.check_process("python3"))
    @patch("premise_bar.subprocess.run")
    def test_check_launchctl(self,m):
        m.return_value.returncode=0
        self.assertTrue(premise_bar.check_launchctl("com.apple.dock"))
    def test_is_running_http_false(self):
        self.assertFalse(premise_bar.is_running({"check":{"type":"http","url":"http://localhost:19999"}}))
    def test_is_running_unknown_type(self):
        self.assertFalse(premise_bar.is_running({"check":{"type":"nonexistent"}}))

if __name__=="__main__":
    unittest.main()
