"""Check that green Run builds/flashes the selected profile, not a stale ELF."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class IDEProfileTests(unittest.TestCase):
    def test_regenerated_metadata_profile_selection_and_idempotence(self):
        spec = importlib.util.spec_from_file_location("configure_ide", ROOT / "configure_ide.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            tree = ET.parse(ROOT / ".cproject")
            settings = tree.getroot().find("storageModule[@moduleId='org.eclipse.cdt.core.settings']")
            # Model CubeMX replacing metadata with its two ordinary profiles.
            for config in list(settings):
                metadata = config.find("storageModule[@moduleId='org.eclipse.cdt.core.settings']")
                if metadata is not None and metadata.get("name") == "ChannelTest":
                    settings.remove(config)
            for option in tree.getroot().iter("option"):
                for item in list(option):
                    if item.get("value", "").startswith("F103_CHANNEL_TEST"):
                        option.remove(item)
            module.configure_profiles(tree.getroot())
            tree.write(project / ".cproject")
            module.ROOT = project
            module.configure_run()
            module.configure_run("ChannelTest")
            configs = {c.find("storageModule[@moduleId='org.eclipse.cdt.core.settings']").get("name"): c
                       for c in settings.findall("cconfiguration")}
            self.assertEqual(set(configs), {"Debug", "Release", "ChannelTest"})
            self.assertEqual([e.attrib for e in configs["Debug"].iter("extension")],
                             [e.attrib for e in configs["ChannelTest"].iter("extension")])
            self.assertEqual(next(configs["ChannelTest"].iter("targetPlatform")).get("binaryParser"),
                             "org.eclipse.cdt.core.ELF")
            for name, config in configs.items():
                defines = [v.get("value") for v in config.iter("listOptionValue")
                           if v.get("value", "").startswith("F103_CHANNEL_TEST")]
                self.assertEqual(defines, ["F103_CHANNEL_TEST="+str(int(name == "ChannelTest"))])
                for builder in config.iter("builder"):
                    self.assertTrue(builder.get("buildPath").endswith("/"+name))
            for name, filename, local in (
                    ("Debug", "haptics_f103c8.launch", "haptics_run.local.cfg"),
                    ("ChannelTest", "haptics_f103c8 Channel Test.launch", "haptics_channel_test.local.cfg")):
                launch = ET.parse(project / filename).getroot()
                values = {e.get("key"): e.get("value") for e in launch if e.get("key")}
                prefix = "org.eclipse.cdt.launch."
                self.assertEqual(values[prefix+"PROJECT_BUILD_CONFIG_ID_ATTR"], configs[name].get("id"))
                self.assertEqual(values[prefix+"ATTR_BUILD_BEFORE_LAUNCH_ATTR"], "1")
                self.assertEqual(values[prefix+"PROGRAM_ARGUMENTS"], "-f "+local)
            before = ET.tostring(tree.getroot())
            module.configure_profiles(tree.getroot())
            self.assertEqual(before, ET.tostring(tree.getroot()))
            ids = [e.get("id") for e in tree.getroot().iter("option")]
            self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
