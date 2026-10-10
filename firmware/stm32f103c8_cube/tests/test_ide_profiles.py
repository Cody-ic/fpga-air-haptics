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
                if metadata is not None and metadata.get("name") in ("ChannelTest", "PinTest"):
                    settings.remove(config)
            for option in tree.getroot().iter("option"):
                for item in list(option):
                    if item.get("value", "").split("=",1)[0] in ("F103_CHANNEL_TEST", "F103_PIN_TEST", "F103_FIRMWARE_MODE"):
                        option.remove(item)
            # Existing projects may still force business via old -D options.
            # Regeneration must migrate them so editing the header can work.
            debug = next(c for c in settings.findall("cconfiguration")
                         if c.find("storageModule[@moduleId='org.eclipse.cdt.core.settings']").get("name") == "Debug")
            symbols = next(o for o in debug.iter("option")
                           if o.get("superClass") == module.BASE+"compiler.option.definedsymbols")
            for value in ("F103_CHANNEL_TEST=0", "F103_PIN_TEST=0", "F103_FIRMWARE_MODE=0"):
                ET.SubElement(symbols, "listOptionValue", builtIn="false", value=value)
            module.configure_profiles(tree.getroot())
            tree.write(project / ".cproject")
            module.ROOT = project
            module.configure_run()
            module.configure_run("ChannelTest")
            module.configure_run("PinTest")
            configs = {c.find("storageModule[@moduleId='org.eclipse.cdt.core.settings']").get("name"): c
                       for c in settings.findall("cconfiguration")}
            self.assertEqual(set(configs), {"Debug", "Release", "ChannelTest", "PinTest"})
            for name in ("ChannelTest", "PinTest"):
                self.assertEqual([e.attrib for e in configs["Debug"].iter("extension")],
                                 [e.attrib for e in configs[name].iter("extension")])
                self.assertEqual(next(configs[name].iter("targetPlatform")).get("binaryParser"),
                                 "org.eclipse.cdt.core.ELF")
            for name, config in configs.items():
                defines = [v.get("value") for v in config.iter("listOptionValue")
                           if v.get("value", "").split("=", 1)[0] in
                           ("F103_CHANNEL_TEST", "F103_PIN_TEST", "F103_FIRMWARE_MODE")]
                self.assertEqual(defines, [])
                for builder in config.iter("builder"):
                    self.assertTrue(builder.get("buildPath").endswith("/"+name))
            for name, filename, local in (
                    ("Debug", "haptics_f103c8.launch", "haptics_run.local.cfg"),
                    ("ChannelTest", "haptics_f103c8 Channel Test.launch", "haptics_channel_test.local.cfg"),
                    ("PinTest", "haptics_f103c8 Pin Test.launch", "haptics_pin_test.local.cfg")):
                launch = ET.parse(project / filename).getroot()
                values = {e.get("key"): e.get("value") for e in launch if e.get("key")}
                prefix = "org.eclipse.cdt.launch."
                self.assertEqual(values[prefix+"PROJECT_BUILD_CONFIG_ID_ATTR"], configs[name].get("id"))
                self.assertEqual(values[prefix+"ATTR_BUILD_BEFORE_LAUNCH_ATTR"], "1")
                self.assertEqual(values[prefix+"PROGRAM_ARGUMENTS"], "-f "+local)
                favorites = launch.find("listAttribute[@key='org.eclipse.debug.ui.favoriteGroups']")
                self.assertEqual(favorites is not None, name == "Debug")
            # Old PinTest overrides must not hide an edit in the shared header.
            symbols=next(o for o in configs["PinTest"].iter("option")
                         if o.get("superClass")==module.BASE+"compiler.option.definedsymbols")
            ET.SubElement(symbols,"listOptionValue",builtIn="false",value="F103_PIN_TEST_CHANNEL=2")
            ET.SubElement(symbols,"listOptionValue",builtIn="false",value="F103_PIN_TEST_ON_MS=1000")
            ET.SubElement(symbols,"listOptionValue",builtIn="false",value="F103_GROUP_TEST_MASK=4")
            ET.SubElement(symbols,"listOptionValue",builtIn="false",value="F103_GROUP_TEST_ON_MS=1000")
            ET.SubElement(symbols,"listOptionValue",builtIn="false",value="F103_GROUP_TEST_FIRST_CHANNEL=0")
            ET.SubElement(symbols,"listOptionValue",builtIn="false",value="F103_GROUP_TEST_LAST_CHANNEL=15")
            module.configure_profiles(tree.getroot())
            self.assertFalse(any(v.get("value", "").startswith("F103_") for v in symbols))
            before = ET.tostring(tree.getroot())
            module.configure_profiles(tree.getroot())
            self.assertEqual(before, ET.tostring(tree.getroot()))
            ids = [e.get("id") for e in tree.getroot().iter("option")]
            self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
